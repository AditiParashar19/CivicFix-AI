# CivicFix AI: an agentic system for civic issue reporting

Citizens report potholes, garbage, broken streetlights and water leakage. A small team of cooperating
agents classifies the report, scores priority with explainable rules, checks for duplicates, recommends a
department, and later flags reports that have stalled. **A human reviews and confirms every report before it is saved.**

> **Prototype honesty notice.** Departments come from a *mock* directory (`.example` addresses). Routing,
> reminders, escalations and status changes are **simulated**: no real complaint is filed, no department is
> contacted, no notification is sent, nothing is "resolved" by any real authority. All sample data is fictional.

## Quick start

Requires Python 3.10+ (developed on 3.12).

```bash
cd civicfix-ai
python -m venv .venv
source .venv/bin/activate            # Windows (PowerShell): .venv\Scripts\Activate.ps1
pip install -r requirements.txt      # add requirements-dev.txt for tests/linting
cp .env.example .env                 # Windows: copy .env.example .env   (all values optional)
streamlit run app.py                 # http://localhost:8501
```

Local development has a clearly labelled local admin identity and should use fictional data. Production mode
fails closed unless the user signs in through Streamlit's OIDC integration, has an authorized CivicFix role in
the verified identity token, and a MongoDB URI is configured. Production role assignments must come from the
identity provider; they are never editable in the app.

### OIDC and role setup

1. Register the app with an OIDC provider and set its redirect URL to `https://<your-host>/oauth2callback`.
2. Copy `.streamlit/secrets.toml.example` to `.streamlit/secrets.toml`; fill in the provider client ID, client
   secret, server metadata URL, HTTPS redirect URL, and a long random cookie secret. Keep that file private.
3. Configure the provider to include a signed `civicfix_role` claim in the ID token with one of `citizen`,
   `reviewer`, `operator`, or `admin`. Set `CIVICFIX_OIDC_ROLE_CLAIM` if your provider uses another claim name.
   Accounts without an assigned role or with `email_verified=false` are denied.
4. Set `CIVICFIX_ENV=production`, `MONGODB_URI`, and `MONGODB_DATABASE` in the deployment secret manager.
   Production startup refuses the local SQLite backend.

Citizens can submit and see only their own complaints. The owner key is the verified OIDC `sub` value, not an
email address or a browser-supplied field. Existing SQLite/MongoDB records are preserved with a null owner and
remain visible only to staff; they are never automatically assigned to a citizen. Reviewers may move Submitted
reports to Under Review. Operators may move Under Review reports to In Progress. Only admins may complete the
final simulated transition to Resolved.

With no configuration the app uses a **local SQLite file** (fine for development and demos). For a deployment
that must keep data, use **MongoDB Atlas**: see [docs/MONGODB_SETUP.md](docs/MONGODB_SETUP.md).

First run starts **empty**. To try it with fictional data use sidebar → *Demo controls* → *Load sample data*
(or `python -m database.seed`). Demo records are flagged `is_demo`, so they can be replaced or removed without touching real reports.

## Storage architecture

| | MongoDB Atlas (`MONGODB_URI` set) | Local SQLite (default) |
|---|---|---|
| Persistence | Survives restarts and redeploys | A file on the server's disk; **lost on hosts with temporary disks** |
| Several users / servers | Yes (shared) | One server only |
| Needs a database service | Yes (free tier exists) | No |
| Use for | Any shared or hosted deployment | Development, offline demos, tests |

* Agents and pages talk only to `database/db.py` (a repository facade). `database/sqlite_backend.py` and
  `database/mongo_backend.py` implement the same contract and the **same search semantics**.
* If `MONGODB_URI` is set and unreachable, the app shows an error. It **never silently falls back** to a local file.
* **Why not "no database"?** Browser storage is per-device and invisible to the server (so it cannot feed a shared
  dashboard, the follow-up agent or an audit log); server memory / temp files vanish on restart and are not shared
  between instances. Complaint records and audit history need persistence, so a database is justified.
* Existing SQLite files are upgraded **in place and non-destructively** on start (new columns/tables/indexes; one
  backfilled history entry per existing complaint). To copy data to MongoDB: `python -m database.migrate --from data/civicfix.db --dry-run`, then without `--dry-run`.
  It only adds, skips complaints already present, and is safe to re-run.

## Configuration (`.env`)

| Variable | Purpose | Default |
|---|---|---|
| `MONGODB_URI`, `MONGODB_DATABASE` | Use MongoDB (credentials stay in the environment, never in the UI) | empty → SQLite, `civicfix` |
| `CIVICFIX_DB_PATH` | SQLite file (only when no `MONGODB_URI`) | `data/civicfix.db` |
| `CIVICFIX_ENV` | `development` or `production` (production hides demo controls) | `development` |
| `CIVICFIX_OIDC_ROLE_CLAIM` | Signed identity-token claim containing the CivicFix role | `civicfix_role` |
| `CIVICFIX_AUTO_SEED` | Load fictional data automatically into an **empty** database | `false` |
| `ANTHROPIC_API_KEY` | Optional: polish the draft letter. Category, location, priority and description are sent to the provider | empty (offline) |
| `CIVICFIX_LLM_MODEL` | Model used when a key is set (unchanged default; verify it against your account) | `claude-sonnet-5-5` |
| `FOLLOWUP_THRESHOLD_DAYS` | Default follow-up threshold (adjustable in the UI) | `3` |

## Tests

```bash
pip install -r requirements-dev.txt
pytest -q                                        # SQLite tests + (if mongomock is installed) MongoDB contract tests
CIVICFIX_TEST_MONGODB_URI="mongodb+srv://..." pytest -q tests/test_search.py   # optional: run the contract tests on a real cluster (uses a throw-away database)
ruff check . && mypy --ignore-missing-imports .  # optional linting / types
```
Tests use temporary databases and never touch real data. `tests/test_apptest.py` renders the real app with Streamlit's `AppTest`;
`tests/test_pages_stub.py` runs the page scripts against a recording stub (logic checks that also work without Streamlit).

## Container and scheduled follow-ups

For a local-only container demo, run `docker compose up --build`. The app listens on loopback and stores its SQLite
database in a named Docker volume. The compose file uses the development admin identity; do not expose it publicly.
The Dockerfile can also be used by a managed container platform, which must mount OIDC secrets and configure
production environment variables before `CIVICFIX_ENV=production` is enabled.

Run one follow-up evaluation from a scheduler with `python -m agents.followup_job --threshold-days 3`. The job
checks up to 5,000 open reports, logs due reminder/escalation recommendations idempotently, and emits a JSON
summary. It never sends a notification or changes a complaint status. For example, a system cron can run it hourly:

```cron
0 * * * * cd /srv/civicfix-ai && .venv/bin/python -m agents.followup_job --threshold-days 3
```

The host scheduler is responsible for monitoring job exit codes and retaining logs. The repository includes a
GitHub Actions workflow that installs the development dependencies and runs the full test suite on pushes and pull requests.

## Features

Home dashboard with live metrics · guided 8-step report workflow · Intake Agent (category + confidence label,
explained priority, summary, formal draft, optional AI polish with validation) · Duplicate Agent (50% text + 30% location +
20% category; warns, never rejects; re-checked at save time) · Planning Agent (mock department, simulated SLA with its basis, next steps) ·
unique IDs `CF-YYYYMMDD-XXXX` · paginated search (all words, case-insensitive, `%`/`_` literal) with status/category/priority/date
filters and sorting · complaint details, status timeline and agent decisions · simulated status changes (one step forward, confirmed, noted, audited) ·
Follow-up Agent (recommended reminder / escalation, shown next to the persisted simulated status; recording is idempotent) · activity explorer
(agent, event type, outcome, text, date, complaint filters; pagination) · fictional demo data with safe reset.

## Architecture

```
OIDC identity ─► Role gate ─► Citizen form ─► Orchestrator ─► Intake Agent ─► Duplicate Agent ─► Planning Agent
                     │                            (validates stages, persists failures)              │
                     └── owner scope for citizen queries                                      HUMAN REVIEW
                                                                                          confirm + duplicate acknowledgement
                                                                                                      │
                                                                                      Repository (SQLite | MongoDB)
                                                                                                      │
                                                                       Follow-up Agent ◄── saved reports (staff only)
```
* **Bounded autonomy:** analysis reads storage but writes nothing. `save_report()` is the only place a new complaint is stored; it requires a
  validated workflow, the duplicate acknowledgement when needed, and is idempotent per workflow (double clicks / reruns save once).
* **Explainable:** priority is a points score where every point has a stated reason; confidence is a keyword-evidence share, labelled as an estimate.
* **Honest limits:** location similarity is text-based (no coordinates); keyword classification is English-only.
* **Scheduled follow-up:** the one-shot worker is idempotent and safe to rerun, but scheduling and monitoring are supplied by the host.
* **Identity and accounts:** sign-in and account lifecycle are managed by the configured OIDC identity provider. CivicFix does not store passwords or provide self-service registration.

## Shared interfaces (unchanged signatures; additions are optional keyword arguments)
```python
classify(text); assess_priority(text, category); analyze_complaint(description, location, category_hint=None, use_llm=True)
find_duplicates(description, location, category, existing=None, threshold=0.55)
plan_resolution(analysis, duplicates); evaluate_followups(complaints, threshold_days, now=None)
run_intake_workflow(description, location, category_hint=None); save_report(workflow, draft=None, image_name=None, image_bytes=None, duplicate_acknowledged=False)
db.log_activity(...); db.get_activity(complaint_id=None, ...)     # + search_complaints, count_by, get_status_history, count_activity
```
"Pending" = Submitted + Under Review; In Progress is counted separately. Behaviour change: statuses now move **one step forward only** (no skipping, no reopening).

## Deployment notes
* **Any hosted environment:** set up OIDC and its verified role claim, then set `MONGODB_URI`, `MONGODB_DATABASE`, and `CIVICFIX_ENV=production` through the host's secret manager; never commit `.env` or `.streamlit/secrets.toml`.
* Uploaded photos are stored on the server's disk (`data/uploads/`), which is temporary on many hosts. Persistent photo storage is not implemented.
* In production, citizens can read only reports owned by their OIDC subject. Staff views and status changes are restricted by server-side role checks. Local development intentionally uses a clearly labelled local admin identity and must not be exposed publicly.

## Troubleshooting
* **"Cannot reach MongoDB"**: check the connection string, that your IP is on Atlas's *Network Access* list, and that the cluster is running.
* **"rejected the credentials"**: re-check username/password (URL-encode special characters) and the user's database permissions.
* **`ModuleNotFoundError`**: activate the virtualenv and `pip install -r requirements.txt`.
* **Port busy**: `streamlit run app.py --server.port 8502`.
* **Start over (local SQLite only)**: stop the app and delete `data/civicfix.db`; or `python -m database.seed --wipe-all --yes` (destructive, deletes every record in the configured storage).

## Known limitations
Mock directory and SLAs; OIDC authentication requires deployment-specific provider configuration and role claims;
no real notifications or portal integration; text-based (not geographic) duplicate detection; follow-up evaluation
runs when a page is opened, not on a schedule; Mongo has no multi-document transactions (the code compensates on partial failure).

### Production-readiness gates

This repository is a tested prototype, not a production-ready municipal service. Before handling real citizen data, address these items in order:

1. Configure and independently review the OIDC provider, role claims, account lifecycle, and deployment domain. The application now fails closed in production and scopes citizen complaint queries to the verified OIDC subject.
2. Replace the mock department directory with verified jurisdiction data and authorized adapters. No report is filed externally, and no acknowledgement or notification is sent.
3. Move follow-up evaluation to a monitored background worker with bounded retries and idempotent delivery. The current agent only recommends actions when a page is opened.
4. Configure managed persistent storage, protected image storage, backups and a tested recovery procedure. Local SQLite and server-local uploads are for development; MongoDB connection settings alone do not provide backups or file persistence.
5. Complete deployment-specific security review, HTTPS and secret configuration, rate limits, and operational monitoring before public access.

Do not expose this prototype publicly with real complaint data until these gates are implemented and independently reviewed. Keep `CIVICFIX_ENV=development` for local demos and use only fictional sample records.
