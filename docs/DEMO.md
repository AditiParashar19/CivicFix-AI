# Demo materials

## Pre-demo checklist
- [ ] `pip install -r requirements.txt` done; `pytest -q` passes on the demo machine
- [ ] `streamlit run app.py` opens; sidebar → **Demo controls** → *Load sample data* on an empty database, or *Replace demo records* for fresh timestamps (real reports are never deleted)
- [ ] Browser zoom ~110%, sidebar visible, other tabs closed
- [ ] No `.env` key needed (offline mode); no internet dependency
- [ ] Have the two demo texts below in a notes file ready to paste
- [ ] Know the line: "Everything routed or sent is simulated; a human confirms before saving."

## End-to-end scenario
**Text 1 (duplicate demo)**: *Large pothole near the City Market bus stop on Sector 4 Main Road. Two bikes slipped yesterday and it is dangerous at night.*  Location: *Sector 4 Main Road, City Market*
Expected: category pothole, priority **Critical** (score 7: base + safety wording 'slipped'/'dangerous' + busy location + scale), Public Works (mock), **duplicate warning** against the seeded
Sector 4 report. Tick "separate issue" to show legitimate submissions are allowed.

**Text 2 (clean submission)**: *The streetlight outside the Rose Garden gate has been flickering and then going out completely for several days.*  Location: *Rose Garden gate, Civil Colony*
Expected: broken streetlight, priority **Medium**, Electrical & Street Lighting (mock), **no duplicate warning**. Edit one line of the draft, confirm, note the new ID.

(Do not use a water-leak example for the clean case: a seeded Ward 7 pipe-burst report would correctly be flagged as a duplicate.)

## 2-minute script
1. **(0:00)** Home: "CivicFix AI coordinates four agents instead of a single chatbot. Everything here is a prototype with fictional data."
2. **(0:15)** Report an Issue: paste Text 1, leave category on Auto-detect, Analyze.
3. **(0:30)** Walk the results: category, **Why this priority?** (each point explained), department, drafted letter.
4. **(0:50)** Point at the duplicate warning: "The Duplicate Agent found a similar open report. We warn, but the citizen decides." Tick the box, edit the draft, **Confirm and save**.
5. **(1:05)** Complaint History: open the new ID → agent timeline: "every step, decision, reason and outcome is recorded." Change status to *Under Review* (simulated; statuses only move one step forward and need the confirmation tick).
6. **(1:20)** Dashboard: counts, category/status charts, filter by *High* priority.
7. **(1:35)** Follow-up Agent: slider at 3 days → escalations and reminders with reasons; open a draft: "marked SIMULATED, never sent".
8. **(1:50)** Close: "Bounded autonomy, explainable decisions, human review, works offline. Next steps: real department integration with approval, geo-coordinates, multilingual input."

## Presentation outline (8 slides)
1. Problem: duplicated, misrouted, forgotten civic complaints
2. Solution: CivicFix AI, an agentic workflow with a human in the loop
3. Architecture: Orchestrator → Intake → Duplicate → Planning → Review → Follow-up
4. Explainability: priority points table; duplicate score formula
5. Safety and honesty: simulated actions, mock directory, image validation, local data, no key required
6. Live demo (script above)
7. Testing and team: test areas, ownership table
8. Limitations and future work
