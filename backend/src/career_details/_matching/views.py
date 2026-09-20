from datetime import date
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class IdentityEvidence(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    first_name: str
    last_name: str
    email: str | None = None
    date_of_birth: date | None = None


class EmploymentEvidence(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    title: str | None = None
    organization: str | None = None
    city: str | None = None
    description: str | None = None
    start_year: int | None = None
    start_month: int | None = None
    end_year: int | None = None
    end_month: int | None = None


class EmploymentCandidate(EmploymentEvidence):
    id: UUID


class EmploymentMatch(BaseModel):
    candidate_id: UUID | None


class CVMatchingSnapshot(BaseModel):
    person_id: UUID | None
    identity: IdentityEvidence
    items: dict[UUID, EmploymentEvidence]


class ReconciliationResult(BaseModel):
    cvs_processed: int = 0
    entries_linked: int = 0
    conflicts_preserved: int = 0
