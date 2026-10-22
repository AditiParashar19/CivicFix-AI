# Audit: root causes found and fixed

Each item was reproduced against the original code (or is evident from it) before being fixed.

| # | Problem | Root cause | Fix |
|---|---|---|---|
| 1 | Search misses obvious results ("pothole market" → 0) | Whole query was one `LIKE '%…%'` phrase; dashboard and history used different logic and fields | One shared semantics: split into words, every word must match any of ID/description/location/summary/department/category |
| 2 | Searching `%` or `_` matches everything | LIKE wildcards were not escaped | Escaped in SQLite; `re.escape` in MongoDB |
| 3 | History lacked priority/date filters; no pagination; whole table loaded on each rerun | `list_complaints()` returned everything; dashboard filtered in pandas | `search_complaints()` with filters, sorting, pagination and DB-side counts (`count_by`) |
| 4 | Stale selection / filters in History | Selectbox kept a value no longer in the results; page not reset on filter change | Selection revalidated each run; page resets when the filter signature changes |
| 5 | "Status changed" message never shown | `st.success()` was immediately followed by `st.rerun()` | Flash message stored in session state and shown after the rerun |
| 6 | Any status could be set from any status (Resolved → Submitted) | No transition validation; history only existed as free-text log rows | One-step-forward rule, `status_history`, expected-status conflict check, errors shown |
| 7 | Same reviewed report could be saved twice (double click / rerun) | No idempotency | `workflow_id` + unique index; second save returns the first ID |
| 8 | Follow-up "record" button logged duplicates on every click | No de-duplication | `dedupe_key` (unique); UI reports new vs already-logged |
| 9 | Dashboard "pending" inconsistent | `config.PENDING_STATUSES` included In Progress; header metrics ignored filters while charts used them | `PENDING_STATUSES` fixed; metrics, charts and table share one filter set and say so |
| 10 | Stored XSS risk on Home | Citizen location rendered with `unsafe_allow_html=True` | Rendered via dataframe/text only; HTML helpers escape |
| 11 | Demo data mixed into real data; reset wiped everything on one tick | Auto-seed on empty DB per browser session; reset dropped tables | Seeding is opt-in; demo rows flagged `is_demo`; reset removes demo rows only; full wipe is a separate CLI with `--yes` |
| 12 | Old analysis shown/saved after new invalid input; edits to the form not reflected | Stale `workflow` kept in session state | Cleared on every analysis; "report these results are based on" is shown |
| 13 | Activity errors/failed stages invisible; logging failures could pass silently | Failures only in in-memory trace; no persisted event | Stage failures persisted as system events; logging errors surface as warnings/exceptions |
| 14 | Photo failure after save lost the information | Exception after the insert | `PartialSaveError` carrying the ID + `image_save_failed` event |
| 15 | Silent LLM fallback | `except Exception: return None` | Reason recorded in the draft trace (HTTP status/validation), never the key |
| 16 | Charts/tables may break on newer Streamlit | `use_container_width` is being replaced by `width="stretch"` | Feature-detected wrapper (unverified on real Streamlit here) |

Not reproducible / not found: no broken buttons or missing imports were found in the original code; the activity log saved and retrieved events correctly for saved complaints (its problems were items 8 and 13).
