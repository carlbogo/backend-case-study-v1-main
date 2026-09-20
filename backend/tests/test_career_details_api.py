import copy
import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from server import app
from sqlalchemy import text

from src.career_details._matching.service import CareerMatchingService
from src.career_details._matching.views import EmploymentMatch
from src.cv._extraction.service import CVExtractionService
from src.cv.views.cv_data import CVData
from src.db.service import DatabaseService
from src.llm.service import LLMService
from src.llm.views.results import StructuredOutputResponse
from src.utils.env import env

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not os.environ.get("TEST_DB_URL"), reason="Requires a local disposable TEST_DB_URL"),
]
EXAMPLES = Path(__file__).parents[1] / "examples"
ALICE = {"Authorization": "Bearer alice-token"}
BOB = {"Authorization": "Bearer bob-token"}
VALUES = {"annual_salary": 90000, "salary_currency": "EUR", "weekly_hours": 40, "direct_reports": 4}


def entries(cv):
    return next(section["employment"] for section in cv["sections"] if section["kind"] == "employment")


def route(item):
    return f"/career-details/{item['id']}"


@pytest.fixture
def workspace(monkeypatch):
    monkeypatch.setattr(env, "CAREER_MATCHING_USE_LLM", False)
    created = []
    with TestClient(app) as client:

        def import_cv(filename="cv-en.json", headers=ALICE, data=None):
            response = client.post("/cv/import", json=data or json.loads((EXAMPLES / filename).read_text()), headers=headers)
            assert response.status_code == 201, response.text
            cv = response.json()
            created.append((cv["id"], headers))
            return cv

        yield client, import_cv, created
        for cv_id, headers in reversed(created):
            client.delete(f"/cv/{cv_id}", headers=headers)


def test_exact_imports_share_updates_delete_and_account_isolation(workspace):
    client, import_cv, _ = workspace
    first, second = import_cv(), import_cv()
    bob = import_cv(headers=BOB)
    mia = import_cv("cv-en-other-person.json")
    a, b = entries(first)[0], entries(second)[0]
    assert client.get(route(a), headers=ALICE).json() is None
    assert client.put(route(a), json=VALUES, headers=ALICE).json() == VALUES
    assert client.get(route(b), headers=ALICE).json() == VALUES
    updated = VALUES | {"annual_salary": 105000}
    assert client.put(route(b), json=updated, headers=ALICE).json() == updated
    assert client.get(route(a), headers=ALICE).json() == updated
    assert client.get(route(entries(first)[1]), headers=ALICE).json() is None
    assert client.get(route(entries(mia)[0]), headers=ALICE).json() is None
    assert client.get(route(entries(bob)[0]), headers=BOB).json() is None
    for method in ("get", "put", "delete"):
        kwargs = {"json": VALUES} if method == "put" else {}
        assert getattr(client, method)(route(a), headers=BOB, **kwargs).status_code == 404
    assert client.delete(route(b), headers=ALICE).status_code == 204
    assert client.get(route(a), headers=ALICE).json() is None
    assert client.delete(route(a), headers=ALICE).status_code == 204


@pytest.mark.parametrize(
    "order",
    [
        ["cv-en.json", "cv-de.json", "cv-en-varied.json"],
        ["cv-en-varied.json", "cv-de.json", "cv-en.json"],
    ],
)
def test_translated_and_varied_examples_with_mocked_semantics(workspace, monkeypatch, order):
    client, import_cv, _ = workspace
    calls = []

    async def semantic_response(self, **kwargs):
        # Test orchestration, not model intelligence. Date/employer filtering should
        # give exactly one correct role for each supplied translated/varied entry.
        prompt = kwargs["prompt"]
        assert len(prompt.candidates) == 1
        payload = prompt.user_prompt()
        assert "annual_salary" not in payload and "email" not in payload
        calls.append(prompt)
        return StructuredOutputResponse(
            id="mock",
            data=EmploymentMatch(candidate_id=prompt.candidates[0].id),
            cache_policy="none",
        )

    monkeypatch.setattr(env, "CAREER_MATCHING_USE_LLM", True)
    monkeypatch.setattr(env, "OPENAI_API_KEY", type(env.OPENAI_API_KEY)("fake-for-mocked-test"))
    monkeypatch.setattr(type(LLMService()), "call_with_structured_output", semantic_response)
    cvs = [import_cv(filename) for filename in order]
    mia = import_cv("cv-en-other-person.json")
    for index in (0, 1):
        values = VALUES | {"annual_salary": 90000 + index}
        assert client.put(route(entries(cvs[0])[index]), json=values, headers=ALICE).status_code == 200
        for cv in cvs[1:]:
            assert client.get(route(entries(cv)[index]), headers=ALICE).json() == values
    assert client.get(route(entries(mia)[0]), headers=ALICE).json() is None
    varied = cvs[order.index("cv-en-varied.json")]
    assert client.get(route(entries(varied)[2]), headers=ALICE).json() is None
    assert calls


def test_duplicates_share_and_survive_source_deletion(workspace):
    client, import_cv, created = workspace
    cv = import_cv()
    item = entries(cv)[0]
    client.put(route(item), json=VALUES, headers=ALICE)
    response = client.post(f"/cv/{cv['id']}/duplicate", headers=ALICE)
    assert response.status_code == 201, response.text
    duplicate = response.json()
    created.append((duplicate["id"], ALICE))
    assert entries(duplicate)[0]["id"] != item["id"]
    assert client.delete(f"/cv/{cv['id']}", headers=ALICE).status_code == 204
    assert client.get(route(entries(duplicate)[0]), headers=ALICE).json() == VALUES


def test_edits_preserve_tailoring_but_detach_changed_person_and_role(workspace):
    client, import_cv, _ = workspace
    original, edited = import_cv(), import_cv()
    client.put(route(entries(original)[0]), json=VALUES, headers=ALICE)
    entries(edited)[0]["description"] = "Reworded bullet points"
    response = client.put(f"/cv/{edited['id']}", json=edited, headers=ALICE)
    assert response.status_code == 200, response.text
    edited = response.json()
    assert client.get(route(entries(edited)[0]), headers=ALICE).json() == VALUES
    entries(edited)[0]["employer"] = "Different Company"
    assert client.put(f"/cv/{edited['id']}", json=edited, headers=ALICE).status_code == 200
    assert client.get(route(entries(edited)[0]), headers=ALICE).json() is None
    assert client.get(route(entries(original)[0]), headers=ALICE).json() == VALUES
    changed_person = import_cv()
    changed_person["personal_details"] = json.loads((EXAMPLES / "cv-en-other-person.json").read_text())["personal_details"]
    assert client.put(f"/cv/{changed_person['id']}", json=changed_person, headers=ALICE).status_code == 200
    assert client.get(route(entries(changed_person)[0]), headers=ALICE).json() is None
    assert client.get(route(entries(original)[0]), headers=ALICE).json() == VALUES


def test_uncertainty_and_missing_identity_do_not_share(workspace):
    client, import_cv, _ = workspace
    cv = import_cv()
    client.put(route(entries(cv)[0]), json=VALUES, headers=ALICE)
    translated = import_cv("cv-de.json")  # no live model configured
    assert client.get(route(entries(translated)[0]), headers=ALICE).json() is None
    source = json.loads((EXAMPLES / "cv-en.json").read_text())
    source["personal_details"]["email"] = None
    source["personal_details"]["date_of_birth"] = None
    unidentified = import_cv(data=source)
    assert client.get(route(entries(unidentified)[0]), headers=ALICE).json() is None


def test_distinct_roles_in_one_cv_and_competing_candidates_stay_separate(workspace):
    client, import_cv, _ = workspace
    source = json.loads((EXAMPLES / "cv-en.json").read_text())
    additional = copy.deepcopy(entries(source)[0])
    additional["id"] = str(uuid4())
    additional["job_title"] = "Product Manager"
    entries(source).append(additional)
    first = import_cv(data=source)
    client.put(route(entries(first)[0]), json=VALUES, headers=ALICE)
    assert client.get(route(entries(first)[2]), headers=ALICE).json() is None
    second = import_cv()
    # Two plausible roles share employer/dates; do not select one using title alone.
    assert client.get(route(entries(second)[0]), headers=ALICE).json() is None


def test_concurrent_imports_and_writes_share_one_record(workspace):
    client, import_cv, _ = workspace
    with ThreadPoolExecutor(max_workers=4) as pool:
        cvs = list(pool.map(lambda _: import_cv(), range(4)))
        payloads = [VALUES | {"annual_salary": 100000 + index, "direct_reports": index} for index in range(4)]
        responses = list(
            pool.map(
                lambda pair: client.put(route(entries(pair[0])[0]), json=pair[1], headers=ALICE),
                zip(cvs, payloads),
            )
        )
    assert all(response.status_code == 200 for response in responses)
    observed = [client.get(route(entries(cv)[0]), headers=ALICE).json() for cv in cvs]
    assert all(value == observed[0] for value in observed)
    assert observed[0] in payloads  # no torn updates across the four fields


def test_pdf_import_uses_the_same_linking_path(workspace, monkeypatch):
    client, import_cv, created = workspace
    cv = import_cv()
    client.put(route(entries(cv)[0]), json=VALUES, headers=ALICE)

    async def extract(self, document, user_id):
        return CVData.model_validate_json((EXAMPLES / "cv-en.json").read_text())

    monkeypatch.setattr(type(CVExtractionService()), "extract_cv_data", extract)
    response = client.post("/cv/create-from-upload", files={"file": ("cv.pdf", b"%PDF-stub", "application/pdf")}, headers=ALICE)
    assert response.status_code == 201, response.text
    uploaded = response.json()
    created.append((uploaded["id"], ALICE))
    assert client.get(route(entries(uploaded)[0]), headers=ALICE).json() == VALUES


def test_reconciliation_preserves_conflicting_values(workspace, monkeypatch):
    client, import_cv, _ = workspace
    original = import_cv()
    translated = import_cv("cv-de.json")
    other_values = VALUES | {"salary_currency": "USD", "annual_salary": 120000}
    client.put(route(entries(original)[0]), json=VALUES, headers=ALICE)
    client.put(route(entries(translated)[0]), json=other_values, headers=ALICE)

    async def match(self, evidence, candidates):
        return candidates[0].id if len(candidates) == 1 else None

    monkeypatch.setattr(CareerMatchingService, "_semantic_match", match)
    assert client.portal is not None
    result = client.portal.call(CareerMatchingService().reconcile_account, "alice")
    assert result.conflicts_preserved >= 1
    assert client.get(route(entries(original)[0]), headers=ALICE).json() == VALUES
    assert client.get(route(entries(translated)[0]), headers=ALICE).json() == other_values


def test_reconciliation_retries_uncertain_links_and_keeps_values(workspace, monkeypatch):
    client, import_cv, _ = workspace
    original, translated = import_cv(), import_cv("cv-de.json")
    client.put(route(entries(translated)[0]), json=VALUES, headers=ALICE)

    async def match(self, evidence, candidates):
        return candidates[0].id if len(candidates) == 1 else None

    monkeypatch.setattr(CareerMatchingService, "_semantic_match", match)
    assert client.portal is not None
    client.portal.call(CareerMatchingService().reconcile_account, "alice")
    assert client.get(route(entries(original)[0]), headers=ALICE).json() == VALUES
    assert client.get(route(entries(translated)[0]), headers=ALICE).json() == VALUES
    updated = VALUES | {"direct_reports": 6}
    client.put(route(entries(original)[0]), json=updated, headers=ALICE)
    assert client.get(route(entries(translated)[0]), headers=ALICE).json() == updated
    # Re-running must not split the shared record or move values between records.
    again = client.portal.call(CareerMatchingService().reconcile_account, "alice")
    assert again.entries_linked == 0
    assert client.get(route(entries(translated)[0]), headers=ALICE).json() == updated


def test_inserted_entry_cannot_take_an_unchanged_items_link(workspace):
    client, import_cv, _ = workspace
    cv = import_cv()
    original = entries(cv)[0]
    client.put(route(original), json=VALUES, headers=ALICE)
    additional = copy.deepcopy(original)
    additional["id"] = None
    entries(cv).insert(0, additional)
    response = client.put(f"/cv/{cv['id']}", json=cv, headers=ALICE)
    assert response.status_code == 200, response.text
    saved = response.json()
    assert client.get(route(entries(saved)[0]), headers=ALICE).json() is None
    assert client.get(route(original), headers=ALICE).json() == VALUES


def test_removing_an_employment_preserves_another_cvs_details(workspace):
    client, import_cv, _ = workspace
    first, second = import_cv(), import_cv()
    original = entries(first).pop(0)
    client.put(route(original), json=VALUES, headers=ALICE)
    response = client.put(f"/cv/{first['id']}", json=first, headers=ALICE)
    assert response.status_code == 200, response.text
    assert client.get(route(original), headers=ALICE).status_code == 404
    assert client.get(route(entries(second)[0]), headers=ALICE).json() == VALUES


def test_reconciliation_of_legacy_persons_preserves_values(workspace):
    client, import_cv, _ = workspace
    first = import_cv()
    source = json.loads((EXAMPLES / "cv-en.json").read_text())
    source["personal_details"]["email"] = "different@example.org"
    source["personal_details"]["date_of_birth"] = None
    second = import_cv(data=source)
    client.put(route(entries(second)[0]), json=VALUES, headers=ALICE)
    assert client.get(route(entries(first)[0]), headers=ALICE).json() is None

    async def simulate_legacy_migration():
        # Reproduce the migration's separate identity anchors for matching CVs.
        # This is test-only direct SQL; runtime edits use the public API.
        async with await DatabaseService().get_session() as session:
            await session.execute(
                text("""
                UPDATE career_persons SET email = 'maya@example.com', date_of_birth = '1990-01-01'
                WHERE user_id = 'alice'
            """)
            )
            await session.execute(
                text("""
                UPDATE cvs SET email = 'maya@example.com', date_of_birth = '1990-01-01' WHERE user_id = 'alice'
            """)
            )
            await session.execute(text("UPDATE employment_experiences SET is_legacy = true"))
            await session.commit()

    assert client.portal is not None
    client.portal.call(simulate_legacy_migration)
    client.portal.call(CareerMatchingService().reconcile_account, "alice")
    assert client.get(route(entries(first)[0]), headers=ALICE).json() == VALUES
    assert client.get(route(entries(second)[0]), headers=ALICE).json() == VALUES
    updated = VALUES | {"weekly_hours": 35}
    assert client.put(route(entries(first)[0]), json=updated, headers=ALICE).status_code == 200
    assert client.get(route(entries(second)[0]), headers=ALICE).json() == updated
