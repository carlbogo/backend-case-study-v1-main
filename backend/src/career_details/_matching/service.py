import asyncio
import logging
from itertools import combinations
from uuid import UUID

from sqlalchemy import text
from sqlmodel import col, select

from src.career_details._matching.models import EmploymentExperienceModel, PersonModel
from src.career_details._matching.prompts import EmploymentMatchPrompt
from src.career_details._matching.utils import (
    employment_candidate,
    employment_identity_unchanged,
    exact_employment,
    same_person,
)
from src.career_details._matching.views import (
    CVMatchingSnapshot,
    EmploymentCandidate,
    EmploymentEvidence,
    IdentityEvidence,
    ReconciliationResult,
)
from src.career_details.models import CareerDetailsModel
from src.career_details.views import CareerDetails
from src.cv.models.cv import CVModel
from src.cv.models.cv_section import CVSectionModel, CVSectionType
from src.db import selectinload, with_database_session
from src.db.service import AsyncSession, DatabaseService
from src.llm.service import LLMService
from src.utils.env import env

logger = logging.getLogger(__name__)


class CareerMatchingService:
    def __init__(self):
        self.db_service = DatabaseService()
        self.llm = LLMService()

    async def lock_account(self, session: AsyncSession, user_id: str):
        """Serialize related mutations, including across processes, until commit.

        A hash collision only serializes unrelated accounts; it cannot share data.
        Every query still checks ownership independently of this lock.
        """
        await session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:account, 0))"),
            {"account": f"career-details:{user_id}"},
        )

    def snapshot(self, cv: CVModel) -> CVMatchingSnapshot:
        return CVMatchingSnapshot(
            person_id=cv.person_id,
            identity=IdentityEvidence.model_validate(cv),
            items={
                item.id: EmploymentEvidence.model_validate(item)
                for section in cv.sections
                if section.type == CVSectionType.employment
                for item in section.items
            },
        )

    async def reconcile_cv(
        self,
        session: AsyncSession,
        cv: CVModel,
        previous: CVMatchingSnapshot | None = None,
        *,
        backfill: bool = False,
    ) -> ReconciliationResult:
        """Caller holds the account lock and owns the transaction."""
        result = ReconciliationResult(cvs_processed=1)
        identity = IdentityEvidence.model_validate(cv)
        old_person_id = cv.person_id
        old_person = await session.get(PersonModel, old_person_id) if old_person_id else None
        person = await self._resolve_person(session, cv, previous, backfill)
        cv.person_id = person.id
        candidates = list(
            (
                await session.scalars(
                    select(EmploymentExperienceModel)
                    .where(EmploymentExperienceModel.person_id == person.id, col(EmploymentExperienceModel.is_legacy).is_(False))
                    .order_by(col(EmploymentExperienceModel.created_at), col(EmploymentExperienceModel.id))
                )
            ).all()
        )
        claimed: set[UUID] = set()
        # An inserted/reordered entry must not take a link from an unchanged item
        # that happens to occur later in this CV.
        reserved = {
            item.employment_experience_id
            for section in cv.sections
            if section.type == CVSectionType.employment
            for item in section.items
            if not backfill
            and previous is not None
            and previous.person_id == person.id
            and item.employment_experience_id is not None
            and item.id in previous.items
            and employment_identity_unchanged(EmploymentEvidence.model_validate(item), previous.items[item.id])
        }
        semantic_calls = 0
        for section in cv.sections:
            for item in section.items:
                if section.type != CVSectionType.employment:
                    item.employment_experience_id = None
                    item.employment_match_method = None
                    continue
                evidence = EmploymentEvidence.model_validate(item)
                old_id = item.employment_experience_id
                old = await session.get(EmploymentExperienceModel, old_id) if old_id else None
                old_evidence = previous.items.get(item.id) if previous else None
                if (
                    not backfill
                    and old is not None
                    and old.person_id == person.id
                    and old.id not in claimed
                    and old_evidence is not None
                    and employment_identity_unchanged(evidence, old_evidence)
                    and previous is not None
                    and previous.person_id == person.id
                ):
                    claimed.add(old.id)
                    continue

                eligible = [
                    candidate
                    for candidate in candidates
                    if candidate.id not in claimed
                    and (not backfill or candidate.id != old_id)
                    and (candidate.id not in reserved or candidate.id == old_id)
                    and (
                        not backfill
                        or old is None
                        or old.is_legacy
                        or old.person_id != person.id
                        or (candidate.created_at, candidate.id) < (old.created_at, old.id)
                    )
                    and employment_candidate(evidence, EmploymentEvidence.model_validate(candidate))
                ]
                exact = [candidate for candidate in eligible if exact_employment(evidence, EmploymentEvidence.model_validate(candidate))]
                target: EmploymentExperienceModel | None = None
                method = "new"
                # Even a literal match is unsafe when another plausible role competes.
                if len(eligible) == 1 and len(exact) == 1:
                    target, method = exact[0], "exact"
                elif eligible and len(eligible) <= 8 and semantic_calls < 4:
                    semantic_calls += 1
                    selected = await self._semantic_match(evidence, eligible)
                    target = next((candidate for candidate in eligible if candidate.id == selected), None)
                    if target is not None:
                        method = "semantic"

                # Reconciliation may copy old values only when it has independently
                # confirmed the same person. Ordinary CV edits never copy private values.
                preserve_values = (
                    backfill
                    and old_person is not None
                    and (old_person.id == person.id or same_person(identity, IdentityEvidence.model_validate(old_person)))
                )
                source_details = await session.get(CareerDetailsModel, old_id) if preserve_values and old_id else None
                if target is not None and source_details is not None:
                    existing_details = await session.get(CareerDetailsModel, target.id)
                    if existing_details is not None and self._detail_values(source_details) != self._detail_values(existing_details):
                        target, method = None, "conflict"
                        result.conflicts_preserved += 1

                if target is None:
                    if backfill and old is not None and old.person_id == person.id and old.id not in claimed:
                        target = old
                        target.is_legacy = False
                    else:
                        target = EmploymentExperienceModel(person_id=person.id, **evidence.model_dump())
                        session.add(target)
                        await session.flush()
                    if all(candidate.id != target.id for candidate in candidates):
                        candidates.append(target)
                item.employment_experience_id = target.id
                item.employment_match_method = method
                claimed.add(target.id)
                if old_id != target.id:
                    result.entries_linked += 1
                if source_details is not None and await session.get(CareerDetailsModel, target.id) is None:
                    session.add(
                        CareerDetailsModel(
                            employment_experience_id=target.id,
                            created_at=source_details.created_at,
                            updated_at=source_details.updated_at,
                            **self._detail_values(source_details).model_dump(),
                        )
                    )
                await session.flush()
        return result

    async def _resolve_person(
        self,
        session: AsyncSession,
        cv: CVModel,
        previous: CVMatchingSnapshot | None,
        backfill: bool,
    ) -> PersonModel:
        identity = IdentityEvidence.model_validate(cv)
        current = await session.get(PersonModel, cv.person_id) if cv.person_id else None
        if current is not None and current.user_id == cv.user_id and not backfill:
            if previous is not None and previous.identity == identity:
                return current
            if same_person(identity, IdentityEvidence.model_validate(current)):
                return current
        persons = list(
            (
                await session.scalars(
                    select(PersonModel).where(PersonModel.user_id == cv.user_id).order_by(col(PersonModel.created_at), col(PersonModel.id))
                )
            ).all()
        )
        matches = [person for person in persons if same_person(identity, IdentityEvidence.model_validate(person))]
        if matches and all(
            same_person(IdentityEvidence.model_validate(left), IdentityEvidence.model_validate(right))
            for left, right in combinations(matches, 2)
        ):
            return matches[0]
        if backfill and current is not None and IdentityEvidence.model_validate(current) == identity:
            return current
        person = PersonModel(user_id=cv.user_id, **identity.model_dump())
        session.add(person)
        await session.flush()
        return person

    async def _semantic_match(self, evidence: EmploymentEvidence, candidates: list[EmploymentExperienceModel]) -> UUID | None:
        if not env.CAREER_MATCHING_USE_LLM or env.OPENAI_API_KEY.get_secret_value() in {
            "test-key",
            "local-placeholder-not-a-real-key",
        }:
            return None
        # Bound data sent to the model. Identity and private career details are never included.
        bounded = self._bounded_evidence(evidence)
        payload = [
            EmploymentCandidate(id=candidate.id, **self._bounded_evidence(EmploymentEvidence.model_validate(candidate)).model_dump())
            for candidate in candidates
        ]
        try:
            async with asyncio.timeout(8):
                response = await self.llm.call_with_structured_output(
                    model="default_extraction_fast",
                    prompt=EmploymentMatchPrompt(bounded, payload),
                    max_output_tokens=300,
                    cache_policy="none",
                )
            candidate_id = response.data.candidate_id
            return candidate_id if any(candidate.id == candidate_id for candidate in candidates) else None
        except Exception as error:
            # Do not log CV text, private values, or SDK exception bodies.
            logger.warning("Employment matching unavailable (%s); keeping entries separate", type(error).__name__)
            return None

    def _bounded_evidence(self, evidence: EmploymentEvidence) -> EmploymentEvidence:
        return EmploymentEvidence(
            title=(evidence.title or "")[:300],
            organization=(evidence.organization or "")[:300],
            city=(evidence.city or "")[:300],
            description=(evidence.description or "")[:2000],
            start_year=evidence.start_year,
            start_month=evidence.start_month,
            end_year=evidence.end_year,
            end_month=evidence.end_month,
        )

    async def cleanup_orphans(self, session: AsyncSession, user_id: str):
        await session.flush()
        await session.execute(
            text("""
            DELETE FROM employment_experiences e USING career_persons p
            WHERE e.person_id = p.id AND p.user_id = :user_id
              AND NOT EXISTS (SELECT 1 FROM cv_items i WHERE i.employment_experience_id = e.id)
        """),
            {"user_id": user_id},
        )
        await session.execute(
            text("""
            DELETE FROM career_persons p WHERE p.user_id = :user_id
              AND NOT EXISTS (SELECT 1 FROM cvs c WHERE c.person_id = p.id)
              AND NOT EXISTS (SELECT 1 FROM employment_experiences e WHERE e.person_id = p.id)
        """),
            {"user_id": user_id},
        )

    @with_database_session
    async def reconcile_account(self, session: AsyncSession, user_id: str) -> ReconciliationResult:
        """Explicit backfill/retry; conflicting values are preserved, never overwritten."""
        await self.lock_account(session, user_id)
        cvs = list(
            (
                await session.scalars(
                    select(CVModel)
                    .where(CVModel.user_id == user_id)
                    .order_by(col(CVModel.created_at), col(CVModel.id))
                    .options(selectinload(CVModel.sections).selectinload(CVSectionModel.items))
                )
            ).all()
        )
        total = ReconciliationResult()
        for cv in cvs:
            result = await self.reconcile_cv(session, cv, backfill=True)
            total.cvs_processed += result.cvs_processed
            total.entries_linked += result.entries_linked
            total.conflicts_preserved += result.conflicts_preserved
        await self.cleanup_orphans(session, user_id)
        await session.commit()
        return total

    def _detail_values(self, details: CareerDetailsModel) -> CareerDetails:
        return CareerDetails(
            annual_salary=details.annual_salary,
            salary_currency=details.salary_currency,
            weekly_hours=details.weekly_hours,
            direct_reports=details.direct_reports,
        )
