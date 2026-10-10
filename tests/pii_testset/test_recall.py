"""The PII recall gate (GR-4). Run with: uv run pytest tests/pii_testset -s

Two numbers must stay at or above the configured threshold:
- the whole pipeline, which also knows the sender's citizen record;
- values about *other* people, where only the recognizers stand between the value and a model.
"""

import pytest

from app.config import Settings
from app.privacy.entities import EntityType
from app.privacy.redaction import Redactor
from tests.pii_testset.recall import Case, Report, build_cases, measure


@pytest.fixture(scope="module")
def cases() -> list[Case]:
    return build_cases()


def _explain(report: Report) -> str:
    misses = "\n".join(
        f"  {kind}: {value!r} in {subject!r}" for kind, value, subject in report.misses
    )
    return f"\n{report.table()}\nMissed:\n{misses or '  nothing'}"


def test_the_test_set_covers_every_entity_type(cases: list[Case]) -> None:
    labelled = {span.entity_type for case in cases for span in case.email.pii}
    assert labelled == set(EntityType)


def test_pipeline_recall(redactor: Redactor, cases: list[Case], settings: Settings) -> None:
    report = measure(redactor, cases, use_citizen_record=True)
    print(f"\nPipeline (recognizers + sender's citizen record):{_explain(report)}")
    assert report.recall() >= settings.pii_recall_threshold, _explain(report)


def test_recall_for_people_we_have_no_record_of(
    redactor: Redactor, cases: list[Case], settings: Settings
) -> None:
    report = measure(redactor, cases, use_citizen_record=False)
    print(f"\nRecognizers alone:{_explain(report)}")
    assert report.recall("other people") >= settings.pii_recall_threshold, _explain(report)
