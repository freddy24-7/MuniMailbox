"""Queue order and filters (FR-18), the team dashboard (FR-22), audit search (FR-23)."""

import re
import uuid
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.models import Draft, Email, EmailStatus, Role, Topic, Urgency, User
from app.orchestrator.ingestion import ingest_email
from app.review.stats import team_stats
from app.services import Services
from tests.conftest import TEAM
from tests.helpers import BSN, SENDER, incoming_email, logged_in

Db = async_sessionmaker[AsyncSession]
Users = dict[Role, User]
NOW = datetime(2026, 10, 1, 12, 0, tzinfo=UTC)


def _email(
    subject: str,
    *,
    urgency: Urgency | None = Urgency.NORMAL,
    hours_ago: int = 1,
    topic: Topic | None = Topic.PERMITS,
    status: EmailStatus = EmailStatus.DRAFT_READY,
    team: str = TEAM,
) -> Email:
    return Email(
        correlation_id=uuid.uuid4(),
        subject_redacted=subject,
        received_at=NOW - timedelta(hours=hours_ago),
        urgency=urgency,
        topic=topic,
        status=status,
        team=team,
    )


async def _add(session_factory: Db, *emails: Email) -> None:
    async with session_factory() as session:
        session.add_all(emails)
        await session.commit()


def _subjects(page: str) -> list[str]:
    return re.findall(r"<td>(Mail [^<]+)</td>", page)


# --- Queue ---


async def test_the_queue_shows_the_most_urgent_first_and_then_the_oldest(
    app: FastAPI, users: Users, session_factory: Db
) -> None:
    await _add(
        session_factory,
        _email("Mail normal new", hours_ago=1),
        _email("Mail low old", urgency=Urgency.LOW, hours_ago=90),
        _email("Mail high new", urgency=Urgency.HIGH, hours_ago=2),
        _email("Mail normal old", hours_ago=30),
        _email("Mail high old", urgency=Urgency.HIGH, hours_ago=50),
        _email("Mail unclassified", urgency=None, topic=None, hours_ago=10),
    )
    async with logged_in(app, users[Role.CASEWORKER]) as client:
        page = (await client.get("/queue")).text

    assert _subjects(page) == [
        "Mail high old",
        "Mail high new",
        "Mail normal old",
        "Mail unclassified",  # not classified yet: treated as normal
        "Mail normal new",
        "Mail low old",
    ]


async def test_the_queue_filters_by_topic_and_status_within_the_users_own_scope(
    app: FastAPI, users: Users, session_factory: Db
) -> None:
    await _add(
        session_factory,
        _email("Mail permit ready"),
        _email("Mail permit done", status=EmailStatus.APPROVED),
        _email("Mail waste ready", topic=Topic.WASTE_COLLECTION),
        _email("Mail other team", team="Team B"),
    )
    async with logged_in(app, users[Role.CASEWORKER]) as client:
        by_topic = await client.get("/queue", params={"topic": "permits"})
        by_both = await client.get("/queue", params={"topic": "permits", "status": "draft_ready"})
        rows = await client.get("/queue/rows", params={"topic": "waste_collection", "status": ""})
        unknown = await client.get("/queue", params={"topic": "taxes"})

    assert _subjects(by_topic.text) == ["Mail permit ready", "Mail permit done"]
    assert _subjects(by_both.text) == ["Mail permit ready"]
    assert _subjects(rows.text) == ["Mail waste ready"]
    # The page keeps refreshing with the same filter.
    assert 'hx-get="/queue/rows?topic=permits&amp;status=draft_ready"' in by_both.text
    assert unknown.status_code == 422


# --- Dashboard ---


async def _handled_emails(app: FastAPI, users: Users) -> uuid.UUID:
    """Two emails through the real pipeline: one approved as it came, one edited first.
    Returns the id of the email that was approved as it came."""
    services: Services = app.state.services
    async with services.session_factory() as session:
        for number in range(2):
            await ingest_email(
                session, services.bus, services.cipher, incoming_email(f"Nr {number}."), team=TEAM
            )
        await services.bus.join()
        first, second = (await session.scalars(select(Draft).order_by(Draft.created_at))).all()
    async with logged_in(app, users[Role.CASEWORKER]) as client:
        await client.post(f"/drafts/{first.id}/approve")
        await client.post(f"/drafts/{second.id}/edit", data={"text": "Aangepast antwoord."})
        await client.post(f"/drafts/{second.id}/approve")
    # Named by its draft: with several workers the emails are not finished in the order
    # they came in, so "the first email" would be a guess.
    return first.email_id


async def test_the_dashboard_counts_the_team_leads_own_team(
    app: FastAPI, users: Users, session_factory: Db
) -> None:
    await _handled_emails(app, users)
    await _add(
        session_factory,
        _email("Mail waiting", topic=Topic.BENEFITS),
        _email("Mail other team", team="Team B"),
    )

    async with session_factory() as session:
        stats = await team_stats(session, users[Role.TEAM_LEAD])
    assert stats.total == 3  # not the other team's email
    assert stats.by_topic == {"other": 2, "benefits": 1}
    assert stats.by_status == {"draft_ready": 1, "approved": 2}
    assert (stats.handled, stats.approved, stats.approved_unchanged_share) == (2, 2, 0.5)
    assert stats.average_handling_time is not None
    assert timedelta(0) <= stats.average_handling_time < timedelta(minutes=5)

    async with logged_in(app, users[Role.TEAM_LEAD]) as client:
        page = await client.get("/dashboard")
    assert page.status_code == 200
    assert "50% of 2" in page.text
    assert BSN not in page.text
    assert SENDER not in page.text


async def test_an_empty_dashboard_shows_dashes_not_errors(app: FastAPI, users: Users) -> None:
    async with logged_in(app, users[Role.TEAM_LEAD]) as client:
        page = await client.get("/dashboard")
    assert page.status_code == 200
    assert "No emails yet." in page.text


async def test_the_dashboard_is_for_team_leads_only(app: FastAPI, users: Users) -> None:
    for role in (Role.CASEWORKER, Role.AUDITOR):
        async with logged_in(app, users[role]) as client:
            assert (await client.get("/dashboard")).status_code == 403


async def test_the_header_links_only_to_pages_the_role_can_open(app: FastAPI, users: Users) -> None:
    expected = {
        Role.CASEWORKER: (["/queue"], "/queue"),
        Role.TEAM_LEAD: (["/queue", "/dashboard"], "/queue"),
        Role.AUDITOR: (["/audit"], "/audit"),
    }
    for role, (links, home) in expected.items():
        async with logged_in(app, users[role]) as client:
            page = (await client.get(home)).text
        navigation = re.search(r"<nav>(.*?)</nav>", page, re.DOTALL)
        assert navigation is not None
        assert re.findall(r'href="([^"]+)"', navigation.group(1)) == links


# --- Audit search ---


def _rows(page: str) -> int:
    return page.count("<code>")  # one hash cell per event row


async def test_the_audit_log_can_be_searched_per_email_user_and_action(
    app: FastAPI, users: Users, session_factory: Db
) -> None:
    unchanged = await _handled_emails(app, users)
    caseworker = f"user:{users[Role.CASEWORKER].id}"

    async with logged_in(app, users[Role.AUDITOR]) as client:
        everything = (await client.get("/audit")).text
        per_email = (await client.get("/audit", params={"email": str(unchanged)})).text
        per_user = (await client.get("/audit", params={"actor": caseworker})).text
        per_action = (await client.get("/audit", params={"action": "review."})).text
        combined = await client.get(
            "/audit", params={"email": str(unchanged), "actor": caseworker, "action": "review."}
        )
        wildcard = (await client.get("/audit", params={"action": "%"})).text
        garbage = await client.get("/audit", params={"email": "not-an-id"})

    # This email: received, 9 pipeline events, approved, outbox. No views: nobody opened it.
    assert _rows(per_email) == 12
    assert str(unchanged) in per_email
    # Already filtered to this email: its ID is text, not a link back to the same page.
    assert f'<a href="/audit?email={unchanged}"' not in per_email
    assert f'<a href="/audit?email={unchanged}"' in everything
    # The caseworker: one login, two approvals, one edit.
    assert _rows(per_user) == 4
    assert "privacy.completed" not in per_user
    assert _rows(per_action) == 3  # review.approved twice, review.edited once
    assert _rows(combined.text) == 1
    assert _rows(wildcard) == 0  # typed text is matched literally, not as a pattern
    assert garbage.status_code == 422
    assert _rows(everything) > _rows(per_email)
    # A filtered view still verifies the whole chain and still shows no personal data.
    for page in (per_email, per_user, per_action):
        assert "Hash chain intact" in page
        assert BSN not in page
        assert SENDER not in page


async def test_only_an_auditor_can_search_the_audit_log(app: FastAPI, users: Users) -> None:
    for role in (Role.CASEWORKER, Role.TEAM_LEAD):
        async with logged_in(app, users[role]) as client:
            assert (await client.get("/audit", params={"action": "review."})).status_code == 403
