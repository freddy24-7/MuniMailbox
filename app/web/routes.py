import json
import uuid
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.auth.dependencies import current_user, require
from app.auth.permissions import Permission, PermissionDeniedError, has_permission
from app.auth.scope import can_access_email, visible_emails
from app.auth.session import SESSION_COOKIE
from app.db.models import AuditEvent, Draft, Email, OutboxMessage, User
from app.db.session import get_session
from app.orchestrator.ingestion import ingest_next_from_feed
from app.privacy.restore import read_original, restore
from app.review.service import NotApprovableError, approve_draft
from app.services import Services

router = APIRouter(include_in_schema=False)
# Autoescaping is on for .html templates: email text is untrusted and is never marked safe.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

Db = Annotated[AsyncSession, Depends(get_session)]


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


def _home_for(user: User) -> str:
    return "/queue" if has_permission(user.role, Permission.QUEUE_VIEW) else "/audit"


@router.get("/")
async def home(user: Annotated[User, Depends(current_user)]) -> RedirectResponse:
    return RedirectResponse(_home_for(user), status_code=303)


# --- Demo login (PRD: no real identity integration). Public by design. ---


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, session: Db) -> HTMLResponse:
    users = (await session.scalars(select(User).order_by(User.team, User.role, User.name))).all()
    return templates.TemplateResponse(request, "login.html", {"users": users, "user": None})


@router.post("/login/{user_id}")
async def login(request: Request, user_id: uuid.UUID, session: Db) -> RedirectResponse:
    user = await session.get(User, user_id)
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND)
    await audit.record(session, actor=f"user:{user.id}", action="user.logged_in")
    await session.commit()

    services = _services(request)
    response = RedirectResponse(_home_for(user), status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        services.session_codec.encode(user.id),
        max_age=services.settings.session_ttl_seconds,
        httponly=True,
        samesite="lax",
        secure=services.settings.environment == "production",
    )
    return response


@router.post("/logout")
async def logout(_: Annotated[User, Depends(current_user)]) -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# --- Queue ---


async def _queue_context(session: AsyncSession, user: User) -> dict[str, object]:
    emails = (
        await session.scalars(
            select(Email).where(visible_emails(user)).order_by(Email.ingested_at.desc())
        )
    ).all()
    return {"emails": emails, "user": user}


@router.get("/queue", response_class=HTMLResponse)
async def queue(
    request: Request, session: Db, user: Annotated[User, Depends(require(Permission.QUEUE_VIEW))]
) -> HTMLResponse:
    return templates.TemplateResponse(request, "queue.html", await _queue_context(session, user))


@router.get("/queue/rows", response_class=HTMLResponse)
async def queue_rows(
    request: Request, session: Db, user: Annotated[User, Depends(require(Permission.QUEUE_VIEW))]
) -> HTMLResponse:
    """The table body only; HTMX polls this so statuses update without a reload."""
    return templates.TemplateResponse(
        request, "_queue_rows.html", await _queue_context(session, user)
    )


@router.post("/intake/next")
async def intake_next(
    request: Request, session: Db, user: Annotated[User, Depends(require(Permission.INTAKE_PULL))]
) -> RedirectResponse:
    services = _services(request)
    # Until the intake agent routes by topic, an email belongs to the team that fetched it.
    await ingest_next_from_feed(
        session, services.bus, services.cipher, services.settings.feed_path, team=user.team
    )
    return RedirectResponse("/queue", status_code=303)


# --- Email detail and approval ---


@router.get("/emails/{email_id}", response_class=HTMLResponse)
async def email_detail(
    request: Request,
    email_id: uuid.UUID,
    session: Db,
    user: Annotated[User, Depends(require(Permission.EMAIL_VIEW_RESTORED))],
) -> HTMLResponse:
    cipher = _services(request).cipher
    email = await session.get(Email, email_id)
    # Another team's email looks exactly like one that does not exist.
    if email is None or not can_access_email(user, email):
        raise HTTPException(status.HTTP_404_NOT_FOUND)

    draft = await session.scalar(
        select(Draft).where(Draft.email_id == email.id).order_by(Draft.created_at.desc()).limit(1)
    )
    draft_restored = None
    if draft is not None:
        (draft_restored,) = await restore(
            session, cipher, user=user, email=email, texts=[draft.text_redacted]
        )
    case_context = email.case_context or []
    case_summaries = await restore(
        session,
        cipher,
        user=user,
        email=email,
        texts=[case["summary_redacted"] for case in case_context],
    )
    sent = await session.scalar(select(OutboxMessage).where(OutboxMessage.email_id == email.id))

    # Seeing personal data is itself an audited action (GR-7).
    await audit.record(
        session, actor=f"user:{user.id}", action="email.viewed_restored", email_id=email.id
    )
    await session.commit()

    return templates.TemplateResponse(
        request,
        "detail.html",
        {
            "user": user,
            "email": email,
            "original": read_original(cipher, user=user, email=email),
            "draft": draft,
            "draft_restored": draft_restored,
            "cases": [
                {**case, "summary": summary}
                for case, summary in zip(case_context, case_summaries, strict=True)
            ],
            "sent": json.loads(cipher.decrypt(sent.payload_encrypted)) if sent else None,
            "can_approve": has_permission(user.role, Permission.DRAFT_APPROVE),
        },
    )


@router.post("/drafts/{draft_id}/approve")
async def approve(
    request: Request,
    draft_id: uuid.UUID,
    session: Db,
    user: Annotated[User, Depends(require(Permission.DRAFT_APPROVE))],
) -> RedirectResponse:
    try:
        outbox_message = await approve_draft(
            session, _services(request).cipher, user=user, draft_id=draft_id
        )
    except PermissionDeniedError as error:
        raise HTTPException(status.HTTP_404_NOT_FOUND) from error
    except NotApprovableError as error:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(error)) from error
    await session.commit()
    return RedirectResponse(f"/emails/{outbox_message.email_id}", status_code=303)


# --- Audit log (auditors: process only, no email content) ---


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request, session: Db, user: Annotated[User, Depends(require(Permission.AUDIT_VIEW))]
) -> HTMLResponse:
    events = (
        await session.scalars(select(AuditEvent).order_by(AuditEvent.id.desc()).limit(200))
    ).all()
    return templates.TemplateResponse(
        request,
        "audit.html",
        {"user": user, "events": events, "chain": await audit.verify_chain(session)},
    )
