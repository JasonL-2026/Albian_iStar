## Summary
<!-- What does this PR change and why? -->


## Type of change
- [ ] Bug fix
- [ ] Enhancement / new feature
- [ ] Data change (new source CSV columns, schema change)
- [ ] Methodology change (calculation logic altered — `Dashboard_Methodology.md` must be updated)
- [ ] Documentation only

## Testing
**Branch being merged into:** <!-- TEST / PROD / BACKUP -->

**PR path:** <!-- e.g., session/my-change -> TEST, TEST -> PROD, PROD -> BACKUP -->

**Data used to test:**
<!-- e.g., "AllLoadsDumps.csv for shifts 260801D, 260801N" -->


**Dashboard renders without errors:** [ ] Yes

**Key outputs validated (scores, waterfall totals):**
<!-- e.g., "Combined Haulage Score ≈ 64 %, Loading Score ≈ 50 %" -->


## Checklist
- [ ] Tested locally — `python build_dashboard.py` runs without errors
- [ ] `Haulage_Dashboard.html` opens and displays correctly in browser
- [ ] `Dashboard_Methodology.md` updated (required if any calculation changed)
- [ ] No live operational CSVs committed to the repo
- [ ] Linked to issue: `Closes #`
- [ ] PR path follows policy (`session|feature|fix -> TEST`, `TEST -> PROD`, `PROD -> BACKUP`)

## Shifts validated
<!-- List the ShiftIds used for end-to-end testing -->


## Notes for reviewer
<!-- Anything the reviewer should pay particular attention to -->
