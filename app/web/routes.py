import json
import uuid
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qs, urlencode

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import writer as audit
from app.auth.dependencies import current_user, require
from app.auth.permissions import Permission, PermissionDeniedError, has_permission
from app.auth.scope import can_access_email, visible_emails
from app.auth.session import SESSION_COOKIE
from app.db.models import (
    AuditEvent,
    Draft,
    Email,
    EmailStatus,
    OutboxMessage,
    Review,
    Role,
    Topic,
    Urgency,
    User,
)
from app.db.session import get_session
from app.orchestrator.ingestion import ingest_next_from_feed
from app.privacy.restore import read_original, restore
from app.review.service import (
    REASSIGNABLE,
    NotApprovableError,
    approve_draft,
    current_text,
    edit_draft,
    reassign_email,
    reject_draft,
)
from app.review.stats import team_stats
from app.services import Services

router = APIRouter(include_in_schema=False)
# Autoescaping is on for .html templates: email text is untrusted and is never marked safe.
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

Db = Annotated[AsyncSession, Depends(get_session)]


def _services(request: Request) -> Services:
    services: Services = request.app.state.services
    return services


async def _form(request: Request) -> dict[str, str]:
    """The fields of a plain HTML form (application/x-www-form-urlencoded)."""
    fields = parse_qs((await request.body()).decode("utf-8"), keep_blank_values=True)
    return {name: values[-1] for name, values in fields.items()}


def _navigation(user: User | None) -> list[tuple[str, str]]:
    """The pages this user's role can open, for the links in the header."""
    pages = (
        ("Queue", "/queue", Permission.QUEUE_VIEW),
        ("Dashboard", "/dashboard", Permission.DASHBOARD_VIEW),
        ("Audit log", "/audit", Permission.AUDIT_VIEW),
    )
    if user is None:
        return []
    return [(label, url) for label, url, needed in pages if has_permission(user.role, needed)]


templates.env.globals["navigation"] = _navigation


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


# Most urgent first (FR-18). An email that is not classified yet counts as normal.
_URGENCY_RANK = case((Email.urgency == Urgency.HIGH, 0), (Email.urgency == Urgency.LOW, 2), else_=1)


def _choice[E: StrEnum](enum_class: type[E], value: str) -> E | None:
    """A filter from the query string: empty means "all", anything unknown is an error."""
    if not value:
        return None
    try:
        return enum_class(value)
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY) from error


async def _queue_context(
    session: AsyncSession, user: User, topic: str, email_status: str
) -> dict[str, object]:
    chosen_topic, chosen_status = _choice(Topic, topic), _choice(EmailStatus, email_status)
    query = select(Email).where(visible_emails(user))
    if chosen_topic is not None:
        query = query.where(Email.topic == chosen_topic)
    if chosen_status is not None:
        query = query.where(Email.status == chosen_status)
    # Within one urgency the oldest comes first: it has waited longest.
    emails = (await session.scalars(query.order_by(_URGENCY_RANK, Email.received_at))).all()
    return {
        "emails": emails,
        "user": user,
        "topics": list(Topic),
        "statuses": list(EmailStatus),
        "topic": chosen_topic,
        "status": chosen_status,
        "filters": urlencode({"topic": topic, "status": email_status}),
    }


@router.get("/queue", response_class=HTMLResponse)
async def queue(
    request: Request,
    session: Db,
    user: Annotated[User, Depends(require(Permission.QUEUE_VIEW))],
    topic: str = "",
    status: Annotated[str, Query(alias="status")] = "",
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "queue.html", await _queue_context(session, user, topic, status)
    )


@router.get("/queue/rows", response_class=HTMLResponse)
async def queue_rows(
    request: Request,
    session: Db,
    user: Annotated[User, Depends(require(Permission.QUEUE_VIEW))],
    topic: str = "",
    status: Annotated[str, Query(alias="status")] = "",
) -> HTMLResponse:
    """The table body only; HTMX polls this so statuses update without a reload."""
    return templates.TemplateResponse(
        request, "_queue_rows.html", await _queue_context(session, user, topic, status)
    )


# --- Team dashboard ---


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: Db,
    user: Annotated[User, Depends(require(Permission.DASHBOARD_VIEW))],
) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "dashboard.html", {"user": user, "stats": await team_stats(session, user)}
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
    draft_restored = reply_redacted = None
    reviews: list[Review] = []
    if draft is not None:
        reply_redacted = await current_text(session, draft)
        (draft_restored,) = await restore(
            session, cipher, user=user, email=email, texts=[reply_redacted]
        )
        reviews = list(
            await session.scalars(
                select(Review).where(Review.draft_id == draft.id).order_by(Review.created_at)
            )
        )
    rejection = next((review for review in reviews if review.reason), None)
    rejection_reason = None
    if rejection is not None:
        (rejection_reason,) = await restore(
            session, cipher, user=user, email=email, texts=[rejection.reason or ""]
        )
    caseworkers = (
        await session.scalars(
            select(User).where(User.role == Role.CASEWORKER).order_by(User.team, User.name)
        )
    ).all()
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
            "reply_redacted": reply_redacted,
            "edited": reply_redacted is not None and reply_redacted != draft.text_redacted
            if draft
            else False,
            "rejection_reason": rejection_reason,
            "caseworkers": caseworkers,
            "teams": sorted({worker.team for worker in caseworkers if worker.team}),
            "cases": [
                {**case, "summary": summary}
                for case, summary in zip(case_context, case_summaries, strict=True)
            ],
            "sent": json.loads(cipher.decrypt(sent.payload_encrypted)) if sent else None,
            "can_approve": has_permission(user.role, Permission.DRAFT_APPROVE),
            "can_edit": has_permission(user.role, Permission.DRAFT_EDIT),
            "can_reject": has_permission(user.role, Permission.DRAFT_REJECT),
            "can_reassign": has_permission(user.role, Permission.EMAIL_REASSIGN)
            and email.status in REASSIGNABLE,
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


def _refused(error: Exception) -> HTTPException:
    """Out of scope looks like "does not exist"; anything else is a conflict with the
    state the email is in."""
    if isinstance(error, PermissionDeniedError):
        return HTTPException(status.HTTP_404_NOT_FOUND)
    return HTTPException(status.HTTP_409_CONFLICT, detail=str(error))


@router.post("/drafts/{draft_id}/edit")
async def edit(
    request: Request,
    draft_id: uuid.UUID,
    session: Db,
    user: Annotated[User, Depends(require(Permission.DRAFT_EDIT))],
) -> RedirectResponse:
    services = _services(request)
    try:
        review = await edit_draft(
            session,
            services.cipher,
            services.redactor,
            user=user,
            draft_id=draft_id,
            text=(await _form(request)).get("text", ""),
        )
    except (PermissionDeniedError, NotApprovableError) as error:
        raise _refused(error) from error
    draft = await session.get_one(Draft, review.draft_id)
    await session.commit()
    return RedirectResponse(f"/emails/{draft.email_id}", status_code=303)


@router.post("/drafts/{draft_id}/reject")
async def reject(
    request: Request,
    draft_id: uuid.UUID,
    session: Db,
    user: Annotated[User, Depends(require(Permission.DRAFT_REJECT))],
) -> RedirectResponse:
    services = _services(request)
    try:
        review = await reject_draft(
            session,
            services.cipher,
            services.redactor,
            user=user,
            draft_id=draft_id,
            reason=(await _form(request)).get("reason", ""),
        )
    except (PermissionDeniedError, NotApprovableError) as error:
        raise _refused(error) from error
    draft = await session.get_one(Draft, review.draft_id)
    await session.commit()
    return RedirectResponse(f"/emails/{draft.email_id}", status_code=303)


@router.post("/emails/{email_id}/reassign")
async def reassign(
    request: Request,
    email_id: uuid.UUID,
    session: Db,
    user: Annotated[User, Depends(require(Permission.EMAIL_REASSIGN))],
) -> RedirectResponse:
    form = await _form(request)
    try:
        assignee_id = uuid.UUID(form["assignee"]) if form.get("assignee") else None
    except ValueError as error:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY) from error
    try:
        await reassign_email(
            session,
            user=user,
            email_id=email_id,
            team=form.get("team") or None,
            assignee_id=assignee_id,
        )
    except (PermissionDeniedError, NotApprovableError) as error:
        raise _refused(error) from error
    await session.commit()
    # The email may now be outside this user's scope, so back to the queue.
    return RedirectResponse("/queue", status_code=303)


# --- Audit log (auditors: process only, no email content) ---


@router.get("/audit", response_class=HTMLResponse)
async def audit_log(
    request: Request,
    session: Db,
    user: Annotated[User, Depends(require(Permission.AUDIT_VIEW))],
    email: str = "",
    actor: str = "",
    action: str = "",
) -> HTMLResponse:
    """The audit log, searchable per email, per actor and per kind of action (FR-23)."""
    query = select(AuditEvent)
    if email:
        try:
            query = query.where(AuditEvent.email_id == uuid.UUID(email.strip()))
        except ValueError as error:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY) from error
    if actor:
        query = query.where(AuditEvent.actor == actor)
    if action:
        query = query.where(AuditEvent.action.startswith(action.strip(), autoescape=True))
    events = (await session.scalars(query.order_by(AuditEvent.id.desc()).limit(200))).all()
    staff = (await session.scalars(select(User).order_by(User.name))).all()
    return templates.TemplateResponse(
        request,
        "audit.html",
        {
            "user": user,
            "events": events,
            # Always the whole chain: a filtered view must not hide a broken link.
            "chain": await audit.verify_chain(session),
            "staff": staff,
            "names": {f"user:{person.id}": person.name for person in staff},
            "email": email,
            "actor": actor,
            "action": action,
            "filtered": bool(email or actor or action),
        },
    )
