"""End to end, in a real browser (acceptance criteria in the PRD):

- a synthetic email comes in and gets a draft with at least one source reference;
- the caseworker sees restored personal data, while what is stored has placeholders only;
- an auditor cannot see restored personal data.

Run with: uv run pytest tests/e2e   (first time: uv run playwright install chromium)
"""

import re

from playwright.sync_api import Page, expect

from tests.e2e.data import SOURCE_TITLE, SOURCE_URL, USERS
from tests.helpers import BSN, SENDER


def _log_in(page: Page, role: str) -> None:
    page.goto("/login")
    page.get_by_role("row", name=re.compile(USERS[role])).get_by_role(
        "button", name="Log in"
    ).click()


def test_an_email_travels_from_the_inbox_to_an_approved_reply(page: Page) -> None:
    _log_in(page, "caseworker")
    expect(page).to_have_url(re.compile("/queue$"))

    # Nothing has to be reloaded: the queue refreshes itself while the pipeline runs.
    page.get_by_role("button", name="Fetch next email from the feed").click()
    row = page.get_by_role("row", name=re.compile("draft ready"))
    expect(row).to_be_visible(timeout=30_000)
    expect(row).not_to_contain_text(BSN)  # the queue shows the redacted subject only
    row.get_by_role("link", name="Open").click()

    # The caseworker sees the person; the model saw placeholders.
    reply = page.locator("textarea[name=text]")
    expect(reply).to_have_value(re.compile(f"Uw BSN {BSN} is bekend"))
    expect(page.get_by_role("heading", name=f"Email from {SENDER}")).to_be_visible()
    page.get_by_text("What the model saw").click()
    expect(page.get_by_text("Mijn BSN is [BSN_1]")).to_be_visible()

    # At least one source reference, as a link to the document.
    source = page.get_by_role("link", name=SOURCE_TITLE)
    expect(source).to_have_attribute("href", SOURCE_URL)

    # Edit, save, approve.
    reply.fill(f"Geachte heer/mevrouw,\n\nUw BSN {BSN} klopt. Ophalen kost € 25.\n\nGemeente")
    page.get_by_role("button", name="Save changes").click()
    expect(page.get_by_text("Edited by a caseworker")).to_be_visible()
    page.get_by_text("As stored (placeholders)").click()
    expect(page.get_by_text("Uw BSN [BSN_1] klopt.")).to_be_visible()

    page.get_by_role("button", name="Approve and send").click()
    expect(page.get_by_text(f"Approved and written to the outbox for {SENDER}")).to_be_visible()
    expect(page.get_by_role("button", name="Approve and send")).to_have_count(0)


def test_an_auditor_sees_the_process_and_never_the_person(page: Page) -> None:
    _log_in(page, "caseworker")
    page.get_by_role("button", name="Fetch next email from the feed").click()
    opened = page.get_by_role("row", name=re.compile("draft ready")).first.get_by_role("link")
    expect(opened).to_be_visible(timeout=30_000)
    email_url = opened.get_attribute("href") or ""
    page.get_by_role("button", name="Log out").click()

    _log_in(page, "auditor")
    expect(page).to_have_url(re.compile("/audit$"))
    expect(page.get_by_text("Hash chain intact")).to_be_visible()
    expect(page.get_by_role("cell", name="privacy.completed").first).to_be_visible()
    expect(page.locator("body")).not_to_contain_text(BSN)
    expect(page.locator("body")).not_to_contain_text(SENDER)
    # No link leads there, and typing the address does not help either.
    expect(page.get_by_role("link", name="Queue")).to_have_count(0)
    response = page.goto(email_url)
    assert response is not None
    assert response.status == 403
    expect(page.locator("body")).not_to_contain_text(BSN)


def test_a_team_lead_gets_the_numbers_but_no_approve_button(page: Page) -> None:
    _log_in(page, "team_lead")
    page.get_by_role("link", name="Dashboard").click()
    expect(page.get_by_role("heading", name=re.compile("Team dashboard"))).to_be_visible()
    expect(page.get_by_text("Volume per topic")).to_be_visible()
    expect(page.locator("body")).not_to_contain_text(BSN)

    page.get_by_role("link", name="Queue").click()
    page.get_by_role("button", name="Fetch next email from the feed").click()
    row = page.get_by_role("row", name=re.compile("draft ready")).first
    expect(row).to_be_visible(timeout=30_000)
    row.get_by_role("link", name="Open").click()
    expect(page.get_by_text("Only a caseworker can approve a draft.")).to_be_visible()
    expect(page.get_by_role("button", name="Approve and send")).to_have_count(0)
    expect(page.get_by_role("button", name="Reassign")).to_be_visible()
