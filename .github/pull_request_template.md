## Summary
<!-- What does this PR change and why? -->


## Type of change
- [ ] Bug fix
- [ ] Enhancement / new feature
- [ ] Data change (new source CSV columns, schema change)
- [ ] Methodology change (calculation logic altered — `Dashboard_Methodology.md` must be updated)
- [ ] Documentation only

## Testing
<!-- Governance check requires all fields in this section to be completed. -->

**Branch being merged into:** <!-- DEV / TEST / PROD / BACKUP -->

**PR path:** <!-- e.g., feature/my-change -> DEV, DEV -> TEST, TEST -> PROD, PROD -> BACKUP -->

**Data used to test:**
<!-- e.g., "Data/samples with python build_dashboard.py" or "AllLoadsDumps.csv for shifts 260801001, 260801002" -->


**Dashboard renders without errors:** [ ] Yes

**Key outputs validated (scores, waterfall totals):**
<!-- Include at least one KPI percentage, e.g., "Combined Haulage Score ≈ 64%, Loading Score ≈ 50%" -->


## Checklist
- [ ] Tested locally — `python build_dashboard.py` runs without errors
- [ ] `Haulage_Dashboard.html` opens and displays correctly in browser
- [ ] `Dashboard_Methodology.md` updated (required if any calculation changed)
- [ ] No live operational CSVs committed to the repo
- [ ] Linked to issue: `Closes #`
- [ ] PR path follows policy (`feature|fix -> DEV`, `DEV -> TEST`, `TEST -> PROD`, `PROD -> BACKUP`)

## Shifts validated
<!-- List at least one 9-digit ShiftId used for end-to-end testing -->


## Notes for reviewer
<!-- Anything the reviewer should pay particular attention to -->
