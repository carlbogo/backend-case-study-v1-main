import asyncio
import os
from pathlib import Path
from uuid import uuid4

import asyncpg
import pytest


@pytest.mark.integration
@pytest.mark.skipif(not os.environ.get("TEST_DB_URL"), reason="Requires a local disposable TEST_DB_URL")
def test_migration_preserves_legacy_data_and_constraints():
    async def run():
        connection = await asyncpg.connect(os.environ["TEST_DB_URL"].replace("postgresql+asyncpg://", "postgresql://"))
        schema = f"migration_test_{uuid4().hex}"
        migrations = Path(__file__).parents[1] / "src/db/migrations"
        try:
            await connection.execute(f'CREATE SCHEMA "{schema}"')
            await connection.execute(f'SET search_path TO "{schema}"')
            await connection.execute((migrations / "001_initial.sql").read_text())
            await connection.execute((migrations / "002_add_career_details.sql").read_text())
            cv_id, section_id, item_id = uuid4(), uuid4(), uuid4()
            await connection.execute(
                """
                INSERT INTO cvs (id, user_id, cv_language, first_name, last_name,
                    privacy_clause_enabled, signature_enabled, signature_include_date, signature_show_name)
                VALUES ($1, 'alice', 'en', 'Alex', 'Example', false, false, true, false)
            """,
                cv_id,
            )
            await connection.execute(
                "INSERT INTO cv_sections (id, cv_id, type, position) VALUES ($1, $2, 'employment', 0)",
                section_id,
                cv_id,
            )
            await connection.execute(
                "INSERT INTO cv_items (id, section_id, position, title) VALUES ($1, $2, 0, 'Engineer')",
                item_id,
                section_id,
            )
            await connection.execute(
                """
                INSERT INTO career_details (cv_item_id, annual_salary, salary_currency, weekly_hours, direct_reports,
                    created_at, updated_at)
                VALUES ($1, 123456, 'EUR', 37, 2, '2024-01-02Z', '2025-03-04Z')
            """,
                item_id,
            )
            before = dict(await connection.fetchrow("SELECT * FROM career_details"))
            await connection.execute((migrations / "003_shared_career_details.sql").read_text())
            after = dict(await connection.fetchrow("SELECT * FROM career_details"))
            before["employment_experience_id"] = before.pop("cv_item_id")
            assert before == after
            assert await connection.fetchval("SELECT person_id FROM cvs") == cv_id
            assert await connection.fetchval("SELECT employment_experience_id FROM cv_items") == item_id
            assert await connection.fetchval("SELECT is_legacy FROM employment_experiences") is True
            # The composite FK prevents attaching a CV to another account's person.
            with pytest.raises(asyncpg.ForeignKeyViolationError):
                await connection.execute("UPDATE cvs SET user_id = 'bob'")
        finally:
            await connection.execute("ROLLBACK")
            await connection.execute("SET search_path TO public")
            await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await connection.close()

    asyncio.run(run())
