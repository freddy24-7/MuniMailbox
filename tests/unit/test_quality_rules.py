from typing import Any

from app.agents.quality import rule_based_warnings
from app.db.models import Draft, Email

SOURCE = {"id": 1, "text": "Grofvuil - Kosten\nOphalen kost € 25 en duurt 5 werkdagen."}


def _warnings(
    text: str,
    *,
    sources: list[dict[str, Any]] | None = None,
    policy_context: list[dict[str, Any]] | None = None,
    body: str = "Wat kost grofvuil? Groet, [PERSON_1]",
    placeholders: frozenset[str] = frozenset({"[PERSON_1]"}),
) -> list[tuple[str, str]]:
    context = [SOURCE] if policy_context is None else policy_context
    draft = Draft(
        text_redacted=text, sources=[{"id": 1}] if sources is None else sources, warnings=[]
    )
    email = Email(body_redacted=body, policy_context=context)
    found = rule_based_warnings(draft, email, set(placeholders))
    assert all(warning["origin"] == "rule" for warning in found)
    return [(warning["type"], warning["message"]) for warning in found]


def test_a_reply_that_sticks_to_its_sources_gets_no_warning() -> None:
    assert _warnings("Geachte [PERSON_1], ophalen kost € 25 en duurt 5 werkdagen.") == []


def test_an_amount_that_is_in_no_source_is_flagged() -> None:
    ((kind, message),) = _warnings("Ophalen kost € 40.")
    assert kind == "unsupported_number"
    assert "€ 40" in message


def test_a_term_that_is_in_no_source_is_flagged_once() -> None:
    found = _warnings("U hoort binnen 6 weken van ons. Echt binnen 6 weken.")
    assert [kind for kind, _ in found] == ["unsupported_number"]


def test_spacing_and_sentence_punctuation_do_not_make_a_different_number() -> None:
    assert _warnings("Ophalen kost €25.") == []
    assert _warnings("Dat is € 25, zoals u weet.") == []


def test_decimals_are_part_of_the_amount() -> None:
    ((_, message),) = _warnings("Ophalen kost € 25,50.")
    assert '"€ 25,50"' in message


def test_a_number_the_citizen_wrote_themselves_is_supported() -> None:
    assert _warnings("U schrijft dat u 3 weken wacht.", body="Ik wacht al 3 weken.") == []


def test_a_placeholder_that_is_not_in_the_mapping_is_flagged() -> None:
    ((kind, message),) = _warnings("Geachte [PERSON_2], dank voor uw bericht.")
    assert kind == "unknown_placeholder"
    assert "[PERSON_2]" in message


def test_a_reply_without_sources_says_why() -> None:
    ((kind, cited_none),) = _warnings("Dank voor uw bericht.", sources=[])
    ((_, found_none),) = _warnings("Dank voor uw bericht.", sources=[], policy_context=[])

    assert kind == "no_source"
    assert "cited none" in cited_none
    assert "No relevant document" in found_none
