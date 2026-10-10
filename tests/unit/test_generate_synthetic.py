from app.db.models import Topic
from app.privacy.checksums import bsn_is_valid, iban_is_valid
from app.privacy.entities import EntityType
from scripts.generate_synthetic import TEMPLATES, generate, render


def test_same_seed_gives_same_dataset() -> None:
    assert generate(5, 10, seed=1) == generate(5, 10, seed=1)
    assert generate(5, 10, seed=1) != generate(5, 10, seed=2)


def test_identifiers_are_structurally_valid() -> None:
    dataset = generate(50, 0, seed=7)
    assert all(bsn_is_valid(citizen.bsn) for citizen in dataset.citizens)
    assert all(iban_is_valid(citizen.iban) for citizen in dataset.citizens)
    assert len({citizen.bsn for citizen in dataset.citizens}) == 50


def test_email_addresses_use_reserved_example_domains() -> None:
    dataset = generate(50, 0, seed=7)
    for citizen in dataset.citizens:
        assert citizen.email.rsplit("@", 1)[1] in {"example.com", "example.org", "example.net"}


def test_pii_labels_point_at_the_exact_text() -> None:
    dataset = generate(20, 200, seed=3)
    for email in dataset.emails:
        assert email.pii, "every template contains at least the sender's name"
        for span in email.pii:
            text = email.subject if span.field == "subject" else email.body
            assert text[span.start : span.end] == span.value


def test_render_labels_personal_slots_only() -> None:
    text, spans = render(
        "Ik woon in {city}, BSN {bsn}.", {"city": "Teststad", "bsn": "123"}, "body"
    )
    assert text == "Ik woon in Teststad, BSN 123."
    assert [(span.entity_type, span.value) for span in spans] == [(EntityType.BSN, "123")]


def test_templates_cover_every_topic_and_a_sensitive_case() -> None:
    assert {template.topic for template in TEMPLATES} == set(Topic)
    assert any(template.sensitive for template in TEMPLATES)


def test_emails_belong_to_generated_citizens() -> None:
    dataset = generate(10, 50, seed=4)
    by_id = {citizen.id: citizen for citizen in dataset.citizens}
    for email in dataset.emails:
        assert email.sender == by_id[email.citizen_id].email
