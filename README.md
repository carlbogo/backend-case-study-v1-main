# CV workspace backend

A Python backend for importing and editing CVs, with private career details attached
to individual work experiences.

## Setup

Install Python 3.11+ and [uv](https://docs.astral.sh/uv/). Place the supplied `.env`
file in `backend/.env`. It contains the hosted database connection and OpenAI key.

From the repository root:

```sh
cd backend
uv run server.py
```

Open [Swagger UI](http://127.0.0.1:8000/docs) to browse and try the API.
Click **Authorize** and enter `alice-token` or `bob-token`. These are development
accounts with separate data.

The server creates missing tables on startup. Changes to existing tables require
SQL migrations in `backend/src/db/migrations/`.

Access the database directly via external tooling or your agent to perform the migration. (We don't have any tooling in this repo).

## Command line

Run commands from `backend/`. Start the server once to initialize an empty database;
after that, CLI commands work without the server running.

```sh
uv run cli.py cv import examples/cv-en.json
uv run cli.py cv list
uv run cli.py cv duplicate CV_ID

uv run cli.py career-details show CV_ID
uv run cli.py career-details set CV_ID
```

Import and duplicate print the new CV ID. `show` lists the CV's employment entries
and their private details. `set` lets you select an entry by number and enter its
annual salary, currency, weekly hours, and direct reports. All four values are
required. When editing, press Enter to keep an existing value.

Commands default to Alice. Use `--user bob` to switch users:

```sh
uv run cli.py cv list --user bob
```

The examples folder contains four synthetic CVs:

| File | Example |
| --- | --- |
| `cv-en.json` | English CV |
| `cv-de.json` | German version |
| `cv-en-varied.json` | Version with variations |
| `cv-en-other-person.json` | Different person's CV |

Import them under the same account to explore the matching problem. You can also
import your own CVs via PDF:

```sh
uv run cli.py cv import /path/to/cv.pdf
```

An account can contain repeated, translated, or outdated CVs for one person, as well
as CVs for completely different people. `--user` selects the account, not the person
described by a CV.

In the original case-study baseline, career details belong to one employment entry and stay outside the CV
content. The case study asks you to share details across matching experiences for the same person while keeping different people's details separate.

## API routes

Request and response schemas are available in [Swagger UI](http://127.0.0.1:8000/docs).

| Method | Route                          | Purpose                                                 |
| ------ | ------------------------------ | ------------------------------------------------------- |
| GET    | `/ping`                        | Check the database connection                           |
| GET    | `/cv/`                         | List the user's CVs                                     |
| POST   | `/cv/create-empty`             | Create an empty CV with `{"language":"en"}`             |
| POST   | `/cv/import`                   | Import structured CV JSON                               |
| POST   | `/cv/create-from-upload`       | Import a PDF, up to 10MB, as multipart field `file`     |
| GET    | `/cv/{cv_id}`                  | Read a CV                                               |
| PUT    | `/cv/{cv_id}`                  | Replace editable CV content                             |
| DELETE | `/cv/{cv_id}`                  | Delete a CV                                             |
| POST   | `/cv/{cv_id}/duplicate`        | Copy a CV with fresh IDs                                |
| GET    | `/cv/{cv_id}/target-country`   | Read the target country                                 |
| PATCH  | `/cv/{cv_id}/target-country`   | Set the target country                                  |
| GET    | `/career-details/{cv_item_id}` | Read an employment entry's details, or `null` if absent |
| PUT    | `/career-details/{cv_item_id}` | Create or replace all four career-detail fields         |
| DELETE | `/career-details/{cv_item_id}` | Remove the career-details record                        |

For CV updates, keep existing section and item IDs and use `null` for new ones.
Omitted sections and items are removed. Career-detail routes use the employment
item's ID, not the CV's ID.

## Shared career details implementation

Matching employment entries now reference one shared private-details record. The
API routes and schemas are unchanged. Duplicates retain known links; imports and
material edits run account-scoped person matching followed by employment matching.
See [RATIONALE.md](RATIONALE.md) for the data model, decisions and limitations.
See [CHANGE_SUMMARY.md](CHANGE_SUMMARY.md) for a short description of every added or updated implementation file.
See [CLI_PIPELINE.md](CLI_PIPELINE.md) to follow each CLI command through its functions and database reads/writes.

Deleting career details through any matching item clears them for all linked items.
Deleting a CV preserves details referenced by another CV. Ambiguous matches stay
separate. Reconciliation preserves conflicting private records instead of silently
overwriting them.

### Upgrade an existing local database

Stop the server before upgrading. From the repository root, apply the new migration
once (the supplied Docker setup mounts the migrations into the database container):

```sh
docker compose up -d db
docker compose exec -T db psql -U case_study -d case_study -v ON_ERROR_STOP=1 -f /docker-entrypoint-initdb.d/003_shared_career_details.sql
```

For the supplied hosted database, execute that same SQL file with your database
client. Do not run the initial migrations again against an existing database.
Startup creates missing tables but cannot upgrade existing columns.

Then, from `backend/`, reconcile existing CVs and start the server:

```sh
uv run cli.py career-details reconcile --user alice
uv run cli.py career-details reconcile --user bob
uv run server.py
```

The reconciliation command can be rerun to retry uncertain matches, for example
after configuring the real OpenAI key. Its output reports changed links and
preserved conflicts. A fresh Docker volume automatically applies all three SQL
migrations; it does not need the separate upgrade command.

### Matching configuration and demo

Set the supplied real `OPENAI_API_KEY` in `backend/.env` to enable translated and
reworded role matching. `CAREER_MATCHING_USE_LLM=false` disables semantic matching.
The local placeholder key also skips semantic calls. Exact matching, duplication
and JSON workflows still work. PDF extraction always requires a real key.

From `backend/`:

```sh
uv run cli.py cv import examples/cv-en.json
uv run cli.py career-details set EN_CV_ID
uv run cli.py cv import examples/cv-de.json
uv run cli.py cv import examples/cv-en-varied.json
uv run cli.py cv import examples/cv-en-other-person.json
uv run cli.py career-details show DE_CV_ID
uv run cli.py career-details show VARIED_CV_ID
uv run cli.py career-details show OTHER_PERSON_CV_ID
```

Replace the uppercase IDs with the IDs printed by import. With successful semantic
matching, Maya's corresponding roles share values; Mia's roles and Maya's additional
Harbor Systems role do not. Set details through a second matching CV and show the
first to demonstrate shared updates. With a placeholder key, use two imports of
`cv-en.json` or `cv duplicate` for an offline sharing demo instead.

### Tests

From `backend/`, the unit tests and type checks do not require a database or real key:

```sh
uv run pytest -m 'not integration'
uv run pyright
```

For integration tests, create a disposable local database once, from the repository
root (if `case_study_test` already exists, skip `createdb`):

```sh
docker compose exec -T db createdb -U case_study case_study_test
```

The test application creates the latest schema in an empty test database. A test
database left over from the baseline needs migration 003 applied once, just like
the development database. Then, from `backend/`:

```sh
TEST_DB_URL=postgresql+asyncpg://case_study:case_study@127.0.0.1:55432/case_study_test uv run pytest -q
```

The test harness requires a local database name ending in `_test`. Tests use mocked
LLM responses; they never call OpenAI. The migration test uses an isolated schema
and verifies the complete legacy-to-new migration with existing private values.
