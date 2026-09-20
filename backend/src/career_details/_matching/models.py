from datetime import date, datetime
from typing import ClassVar
from uuid import UUID, uuid4

from sqlalchemy import UniqueConstraint
from sqlmodel import Field, SQLModel

from src.db.mixins import utc_created_at_field


class PersonModel(SQLModel, table=True):
    """Account-scoped identity evidence; never inferred from employment alone."""

    __tablename__: ClassVar[str] = "career_persons"
    __table_args__ = (UniqueConstraint("id", "user_id", name="uq_career_person_account"),)

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    user_id: str = Field(index=True)
    first_name: str
    last_name: str
    email: str | None = None
    date_of_birth: date | None = None
    created_at: datetime = utc_created_at_field()


class EmploymentExperienceModel(SQLModel, table=True):
    """Stable employment identity, retaining the original matching evidence."""

    __tablename__: ClassVar[str] = "employment_experiences"

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    person_id: UUID = Field(foreign_key="career_persons.id", ondelete="CASCADE", index=True)
    title: str | None = None
    organization: str | None = None
    city: str | None = None
    description: str | None = None
    start_year: int | None = None
    start_month: int | None = None
    end_year: int | None = None
    end_month: int | None = None
    is_legacy: bool = False
    created_at: datetime = utc_created_at_field()
