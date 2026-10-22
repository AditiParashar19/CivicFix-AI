# MongoDB Atlas setup

1. **Create a cluster** at <https://www.mongodb.com/atlas> (the free M0 tier is enough for a prototype).
2. **Create a database user** (Database Access → Add New User, password authentication). Give it the built-in
   **`readWrite` role on the `civicfix` database only** (not "Atlas admin"). The app needs to create collections and indexes, which `readWrite` allows.
3. **Allow network access** (Network Access → Add IP Address). Use your host's egress IPs where possible; `0.0.0.0/0` is convenient for hosts with changing IPs but is less safe, so rely on a strong password.
4. **Copy the connection string** (Connect → Drivers). It looks like `mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority`.
   URL-encode special characters in the password (`@` → `%40`, `:` → `%3A`, `/` → `%2F`).
5. **Configure the app** in `.env` (local) or your host's secrets (deployment):
   ```
   MONGODB_URI=mongodb+srv://<user>:<password>@<cluster>.mongodb.net/?retryWrites=true&w=majority
   MONGODB_DATABASE=civicfix
   CIVICFIX_ENV=production
   ```
6. `streamlit run app.py`. On first start the app creates the `complaints` and `activity` collections (with schema validation) and indexes:
   unique `complaint_id`, unique `workflow_id` (idempotent saves), `status`/`category`/`priority` + `created_at`, activity by complaint/agent/time, and a unique `dedupe_key` for follow-up events.
   The sidebar shows `Storage: MongoDB` when connected; any failure is displayed as an error (there is no silent fallback).

## Migrating an existing SQLite file
```bash
python -m database.migrate --from data/civicfix.db --dry-run     # shows counts, writes nothing
python -m database.migrate --from data/civicfix.db               # copies complaints, status history, activity
```
Read-only on the source; additive on the target; re-runnable (existing `complaint_id`s are skipped). Demo-flagged records are skipped unless `--include-demo`.
Back up first if the data matters. Nothing is deleted on either side.

## Notes and limits
* Schema validation is created with the collection. Existing collections are not altered (that needs the `collMod` privilege); the app validates every write itself.
* Timestamps are stored as ISO-8601 UTC strings so SQLite and MongoDB sort and filter identically.
* A status change and its history entry are one atomic update; the audit event is a second write and the app reverts the status if it fails. Reports and their initial events are compensated on failure (no multi-document transactions are used).
* Search uses case-insensitive regular expressions (escaped, so user text is never a pattern). That is fine for prototype-scale data; for very large datasets add an Atlas Search index.
* Rotate the database password if it was ever exposed, and keep `.env` out of version control (it is in `.gitignore`).
