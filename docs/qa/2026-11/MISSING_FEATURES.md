# Missing features and product gaps

Ranked by value for this decision-propagation agent. Observations distinguish missing UI, broken behavior, and features that already partly exist. No implementation changes were made.

1. **Clarification actually updates verdicts** (High; L25) — F06 answer cleared the question but returned zero new requests and retained all five UNCERTAIN verdicts. Human clarification only saves effort when the answer changes the scope decision and produces actionable results.

2. **Live cost and failed-run accounting** (High; L24) — Extraction progress showed zero requests while real HTTP calls occurred; failed scans had no completion request total. The run budget must include extraction, failures and retries before the confirmation gate, not only a successful last cycle.

3. **Decision timeline per topic** (High; L01) — Results group by latest decision and date; no chronological topic timeline found in reviewed controls. A timeline lets a lead distinguish replacement, reversal, cancellation and scope changes before acting.

4. **Per-decision history and sources** (High; L02) — Current evidence/source/date/line are visible. No complete per-decision provenance history view found. A reviewer needs all source decisions, not only the latest summary, to resolve conflicting instructions.

5. **Cache removal and recoverable invalid responses** (High; L26) — Malformed JSON in stored extraction responses repeatedly failed demo scans; removing a watched folder deletes its stored state. A user needs a focused retry of a bad response and optional reuse when re-adding an unchanged project.

6. **Export findings as CSV, Markdown or PDF** (High; L03) — No findings export action found in Results or audited desktop API routes. Diagnostics copy is available. Teams need reviewable findings in issue trackers, release checklists and meeting records.

7. **Owner assignment and follow-up reminders** (High; L04) — Owners imported from task data can appear; no assignment/editor or owner follow-up workflow found in result actions. A finding needs a responsible person and a due date to turn detection into completed propagation.

8. **Mark fixed and automatically verify** (High; L05) — Dismiss/false-positive/snooze exist; file watcher rechecks edits. No explicit fixed-and-verify workflow found. Hiding a finding should be distinguishable from checking that its source line was corrected.

9. **Undo dismiss** (High; L09) — Per-item hide actions work; no visible undo in results after dismissal found. An API can reopen a finding. A mistaken dismissal must be reversible from the UI without editing app state.

10. **Finding filters and grouping** (High; L07) — Topic grouping and search are visible; filter control audit saved. Owner/folder/severity grouping views were not found. Leads need a view of their own folder, release, owner and urgency rather than one long mixed list.

11. **Keyboard and accessibility** (High; L10) — Skip link and several ARIA names exist. Mail Escape failed after Enter; accessible-name/focus audit saved. Reliable focus and accessible labels are required to triage quickly and to use a screen reader.

12. **Time zones for mail and calendar** (High; L19) — Equivalent UTC/KST ICS times both became 08:30, but UTC event retained 11/16 while KST event used 11/17. Connected calendar rendering remains unverified. A UTC event near midnight must be compared with the decision on the correct local calendar day.

13. **Offline behavior** (High; L21) — Cached local UI remains usable during ordinary reads; failed model checks expose a retrying state. Scoped outage test pending. Offline mode should preserve evidence, explain unavailable actions and avoid repeated expensive retries.

14. **Model provider outage** (High; L22) — Default model extraction errors prevented both demo scans. Partial results/recovery messaging needs clearer coverage. The core workflow needs bounded retries and a trustworthy last-verified result while the provider is failing.

15. **Data export and delete all** (High; L23) — Diagnostics copy and delete-all exist; no portable user-data export found. Delete-all also removes shared credential-store entries. Users need backup/migration and scoped project deletion without unexpectedly disconnecting every account.

16. **Preserve handled state across rename/move (remaining coverage)** (High; L15) — Basic file move worked: 5 rows before/after, old path removed, new source updated, 3 requests. Preserving dismissal/snooze across moves remains unverified. A rename should update evidence paths without duplicating or resurrecting dismissed findings.

17. **Bulk dismiss** (Medium; L06) — Per-finding actions exist; no selection and bulk dismiss control found. A large first run otherwise requires repetitive individual clicks and makes triage costly.

18. **Multiple Google accounts** (Medium; L20) — One Gmail connection and one account status are exposed. No account picker/add-second-account flow found. A lead may keep school, personal and organization projects in different accounts.

19. **First-run onboarding** (Medium; L08) — Empty home gives folder-add guidance; model consent and data coverage require separate Settings steps. A guided first check should explain folder boundary, external-send consent, estimate and what was skipped.

20. **Weekly digest** (Medium; L12) — Daily digest setting exists. No weekly summary schedule found. A weekly review should collect new, resolved, dismissed and overdue findings instead of repeating a live list.

21. **Exact-line file links** (Medium; L14) — Details show file and line. Existing open-location action accepts a target path without a line argument. Opening an editor at the stale line would shorten repair time and reduce wrong-file edits.

22. **Calendar write-back preference** (Medium; L13) — Connections explicitly advertise read-only calendar access. User preference question sent; no answer yet. An optional reviewed change proposal could close the loop, but should be built only if the user wants it.

23. **English UI option** (Low; L11) — No language selector found in reviewed settings. Current UI strings remain Korean. Mixed-language teams need translated controls while preserving original evidence text.

24. **Skipped-file coverage summary** (Medium; L16/B09) — Large files and unsupported types have generic skip reasons; a fresh locked file has no indexed warning row. A lead needs to know exactly what was not checked.

25. **Password-protected-file wording** (Medium; L17) — Encrypted PDF does not crash the app, but shows `File has not been decrypted`. Show a plain Korean explanation. CP949 text support was confirmed and is not listed as missing.
