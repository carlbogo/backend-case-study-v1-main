-- Stop the application before applying. No semantic matching or external calls
-- occur here: existing data is preserved one-to-one, including timestamps.
BEGIN;

-- create_all() may already have created these new, empty tables on startup;
-- it does not alter the existing CV or career-details tables.
CREATE TABLE IF NOT EXISTS career_persons (
    id UUID PRIMARY KEY,
    user_id VARCHAR NOT NULL,
    first_name VARCHAR NOT NULL,
    last_name VARCHAR NOT NULL,
    email VARCHAR,
    date_of_birth DATE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_career_person_account UNIQUE (id, user_id)
);
CREATE INDEX IF NOT EXISTS ix_career_persons_user_id ON career_persons(user_id);

CREATE TABLE IF NOT EXISTS employment_experiences (
    id UUID PRIMARY KEY,
    person_id UUID NOT NULL REFERENCES career_persons(id) ON DELETE CASCADE,
    title VARCHAR,
    organization VARCHAR,
    city VARCHAR,
    description VARCHAR,
    start_year INTEGER,
    start_month INTEGER,
    end_year INTEGER,
    end_month INTEGER,
    is_legacy BOOLEAN NOT NULL DEFAULT false,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS ix_employment_experiences_person_id ON employment_experiences(person_id);

ALTER TABLE cvs ADD COLUMN person_id UUID;
ALTER TABLE cvs ADD CONSTRAINT fk_cv_person_account FOREIGN KEY (person_id, user_id)
    REFERENCES career_persons(id, user_id);
CREATE INDEX ix_cvs_person_id ON cvs(person_id);
ALTER TABLE cv_items ADD COLUMN employment_experience_id UUID REFERENCES employment_experiences(id);
ALTER TABLE cv_items ADD COLUMN employment_match_method VARCHAR;
CREATE INDEX ix_cv_items_employment_experience_id ON cv_items(employment_experience_id);

-- Reusing IDs across different tables makes the one-to-one backfill explicit.
INSERT INTO career_persons (id, user_id, first_name, last_name, email, date_of_birth)
SELECT id, user_id, first_name, last_name, email, date_of_birth FROM cvs;
UPDATE cvs SET person_id = id;

INSERT INTO employment_experiences (
    id, person_id, title, organization, city, description,
    start_year, start_month, end_year, end_month, is_legacy
)
SELECT i.id, s.cv_id, i.title, i.organization, i.city, i.description,
       i.start_year, i.start_month, i.end_year, i.end_month, true
FROM cv_items i JOIN cv_sections s ON s.id = i.section_id
WHERE s.type = 'employment' OR EXISTS (SELECT 1 FROM career_details d WHERE d.cv_item_id = i.id);

UPDATE cv_items SET employment_experience_id = id, employment_match_method = 'legacy'
WHERE id IN (SELECT id FROM employment_experiences);

ALTER TABLE career_details DROP CONSTRAINT career_details_cv_item_id_fkey;
ALTER TABLE career_details RENAME COLUMN cv_item_id TO employment_experience_id;
ALTER TABLE career_details ADD CONSTRAINT career_details_employment_experience_id_fkey
    FOREIGN KEY (employment_experience_id) REFERENCES employment_experiences(id) ON DELETE CASCADE;

COMMIT;
