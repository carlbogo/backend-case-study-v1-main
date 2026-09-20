from datetime import datetime
from typing import ClassVar
from uuid import UUID

from sqlalchemy import BigInteger
from sqlmodel import Field, SQLModel

from src.db.mixins import utc_created_at_field, utc_updated_at_field


class CareerDetailsModel(SQLModel, table=True):
    __tablename__: ClassVar[str] = "career_details"

    cv_item_id: UUID = Field(primary_key=True, foreign_key="cv_items.id", ondelete="CASCADE")

    annual_salary: int = Field(sa_type=BigInteger)
    salary_currency: str = Field(max_length=3)
    weekly_hours: int
    direct_reports: int

    created_at: datetime = utc_created_at_field()
    updated_at: datetime = utc_updated_at_field()
