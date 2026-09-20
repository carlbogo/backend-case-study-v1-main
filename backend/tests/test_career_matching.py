import asyncio
from datetime import date
from uuid import uuid4

import pytest

from src.career_details._matching.models import EmploymentExperienceModel
from src.career_details._matching.service import CareerMatchingService
from src.career_details._matching.utils import employment_candidate, exact_employment, same_person
from src.career_details._matching.views import EmploymentEvidence, EmploymentMatch, IdentityEvidence
from src.llm.service import LLMService
from src.llm.views.results import StructuredOutputResponse
from src.utils.env import env


def identity(**changes) -> IdentityEvidence:
    return IdentityEvidence.model_validate(
        {
            "first_name": "Alex",
            "last_name": "Smith",
            "email": "alex@example.org",
            "date_of_birth": "1990-01-01",
            **changes,
        }
    )


@pytest.mark.parametrize(
    "changes,expected",
    [
        ({"first_name": "Alex Jordan", "email": " ALEX@example.org "}, True),
        ({"email": "new@example.org"}, True),  # compatible name plus DOB
        ({"first_name": "Alexa"}, False),
        ({"date_of_birth": date(1992, 1, 1)}, False),
        ({"email": None, "date_of_birth": None}, False),
        ({"first_name": "", "last_name": ""}, False),
    ],
)
def test_identity_requires_corroboration_and_rejects_conflicts(changes, expected):
    assert same_person(identity(), identity(**changes)) is expected


def test_shared_email_does_not_override_conflicting_middle_names():
    assert not same_person(identity(first_name="Alex James"), identity(first_name="Alex Jordan"))


def employment(**changes) -> EmploymentEvidence:
    return EmploymentEvidence.model_validate(
        {
            "organization": "Acme Ltd",
            "title": "Data Engineer",
            "city": "Tallinn",
            "start_year": 2020,
            "start_month": 1,
            "end_year": 2023,
            "end_month": 12,
            **changes,
        }
    )


def test_missing_months_are_unknown_and_small_discrepancies_need_semantics():
    assert exact_employment(employment(), employment(organization=" ACME ", start_month=None, end_month=None))
    assert employment_candidate(employment(), employment(start_month=2))
    assert not exact_employment(employment(), employment(start_month=2))
    assert not employment_candidate(employment(), employment(start_month=8))
    assert not employment_candidate(employment(), employment(start_year=None))


def test_promotions_and_returning_to_an_employer_are_distinct():
    assert not employment_candidate(employment(), employment(start_year=2024, end_year=None))
    assert not exact_employment(employment(), employment(title="Engineering Manager"))
    assert not employment_candidate(employment(), employment(organization="Acme Healthcare"))


@pytest.mark.parametrize("behavior", ["unknown_id", "uncertain", "failure", "timeout", "valid"])
def test_semantic_response_is_bounded_validated_and_fail_closed(monkeypatch, behavior):
    candidate = EmploymentExperienceModel(person_id=uuid4(), **employment().model_dump())
    calls = []

    async def fake_call(self, **kwargs):
        calls.append(kwargs)
        if behavior == "failure":
            raise RuntimeError("unavailable")
        if behavior == "timeout":
            raise TimeoutError()
        chosen = candidate.id if behavior == "valid" else uuid4() if behavior == "unknown_id" else None
        return StructuredOutputResponse(id="test", data=EmploymentMatch(candidate_id=chosen), cache_policy="none")

    monkeypatch.setattr(env, "CAREER_MATCHING_USE_LLM", True)
    # Fake key enables the code path; the SDK is mocked and no network is used.
    monkeypatch.setattr(env, "OPENAI_API_KEY", type(env.OPENAI_API_KEY)("fake-for-mocked-test"))
    monkeypatch.setattr(type(LLMService()), "call_with_structured_output", fake_call)
    result = asyncio.run(CareerMatchingService()._semantic_match(employment(description="x" * 10000), [candidate]))
    assert result == (candidate.id if behavior == "valid" else None)
    assert len(calls) == 1
    assert len(calls[0]["prompt"].employment.description) == 2000
