# Decision Agent QA report

The run is recorded for all98 primary IDs. This does **not** mean full validation:25 cases are partial and16 are blocked. No application source code was changed.

| Status | Primary98 | Including exploration/auxiliary |
|---|---:|---:|
| PASS | 37 | 38 |
| FAIL | 19 | 20 |
| PARTIAL | 25 | 25 |
| BLOCKED | 16 | 16 |
| NOT_IMPLEMENTED | 0 | 0 |
| OBSERVATION | 1 | 29 |

Observed AI API requests: **1199/2000**. Current80% stop line:1600. The user approved budget2000 and the424 estimate for E06. Failed HTTP responses and the selftest probe are included; simulated benchmark calls are excluded.

## Most important failures

1. **E01/E04/B01** — Both capstone real-model scans fail during JSON extraction; no usable six/ten-line accuracy result.
2. **E06** — Big: exact140/160; normalized143/160; 90 known-consistent artifacts falsely flagged, 22 historical artifacts flagged urgently.
3. **F06** — Answer saved but five UNCERTAIN verdicts unchanged and zero rejudge requests.
4. **F12** — Quoted old text in local EML is treated as a new decision, producing two false findings.
5. **F03** — Explicit MVP-only change yields no expected stale findings and asks an unnecessary scope question.
6. **F02** — Cancelled payment feature remains in tasks/tests but is not reported as stale.
7. **F09/F10** — Local mail scenarios leave expected stale task/test lines missed or uncertain.
8. **B02/QA-SETTINGS** — Folder addition/settings block behind other active checks and exceed30s HTTP timeout.
9. **B07** — Removing and re-adding unchanged project loses stored verdicts and spends5 requests.
10. **D02/C03** — Quick search omits unjudged file body; default30s debounce misses the five-second propagation target.

## Highest-value product gaps

1. **Clarification actually updates verdicts** — Human clarification only saves effort when the answer changes the scope decision and produces actionable results.
2. **Live cost and failed-run accounting** — The run budget must include extraction, failures and retries before the confirmation gate, not only a successful last cycle.
3. **Decision timeline per topic** — A timeline lets a lead distinguish replacement, reversal, cancellation and scope changes before acting.
4. **Per-decision history and sources** — A reviewer needs all source decisions, not only the latest summary, to resolve conflicting instructions.
5. **Cache removal and recoverable invalid responses** — A user needs a focused retry of a bad response and optional reuse when re-adding an unchanged project.
6. **Export findings as CSV, Markdown or PDF** — Teams need reviewable findings in issue trackers, release checklists and meeting records.
7. **Owner assignment and follow-up reminders** — A finding needs a responsible person and a due date to turn detection into completed propagation.
8. **Mark fixed and automatically verify** — Hiding a finding should be distinguishable from checking that its source line was corrected.
9. **Undo dismiss** — A mistaken dismissal must be reversible from the UI without editing app state.
10. **Finding filters and grouping** — Leads need a view of their own folder, release, owner and urgency rather than one long mixed list.

## Major measured times

| Case | Wall time and result |
|---|---|
| A01 updater | 304.0s + 289.2s; both exit0, no shortcuts replaced |
| C02 | 0.040s; 0 real requests |
| C03 | 36.974s; 3 real requests |
| E01/E04 | Initial extraction failed; no completion timing/cost record. Cached retry timings are in each case JSON. |
| E06 bookswap | 183.6s; 199 requests |
| E06 big | 467.7s excluding approval wait; 349 requests |
| F isolated fixtures | 350.9s summed measured case durations; see CASE_TIMINGS.json |
| C09 idle | 60.1s, averageCPU 0.865% of one core |

Calendar elapsed duration includes multi-day runtime suspension and user-input waits and must not be read as active QA hours. Exploratory topics were reviewed alongside cases; a continuous one-hour manual session was not certified.

## Blocked prerequisites

- **A06**: QA HTTP server has no tray; supplied controls did not provide a verified native tray interaction. Stopping the HTTP server is not counted as quitting from the tray.
- **D05**: Isolated server uses --no-tray and has no native helper window. A native desktop session using only the QA data is required.
- **G10**: Explicit approval to revoke ME account access has not been given. Existing Google credential state was preserved.
- **G13**: Naver IMAP enablement and an app password were not provided through the app. No password was requested in chat.
- **H02**: User action on incremental Google send consent is pending; sending remains disabled and send_granted is false.
- **H03**: Blocked by H02: no Gmail send grant. No test mail was sent.
- **H04**: Blocked by H02. Live send and recipient-side header verification require the send grant.
- **H05**: Live permission path is unavailable. Eleven distinct recipients would also violate the run restriction to three named addresses; a safe reduced-limit test requires sending enabled first.
- **H08**: No live send or cancel activity exists from this run because H02 is pending. Technical-log privacy was separately checked in K06.
- **H09**: A send grant is required before the send failure path can be reached. Permission revocation also requires explicit user approval.
- **H10**: No Outlook or IMAP account is connected. Read-only notices are present in Settings, but connected behavior is not proved.
- **I01**: Prepared ICS was not imported; approval/manual import confirmation is pending. No calendar write was performed.
- **I02**: Connected duplicate-path test depends on I01 import. Only local ICS rendering and stale-event judgment were exercised.
- **J01**: No Outlook credentials or connected account available.
- **J02**: No IMAP account/app password provided in the app.
- **J03**: No credentials or test workspaces configured for Notion, Slack or Jira.

Provide mail send consent in the app, a freshly timed MAIL-NEW from OTHER2, approved calendar import and revocation/reconnect confirmation, and optional connector credentials through the app UI. Never paste passwords or tokens into the QA log/chat. Native tray/helper/scaling/toast paths need a dedicated native QA session.

## Safety and artifacts

Demo source hashes matched; the completed F16 full scan also left its project unchanged. Intentional edits stayed in scratch QA projects. No test mail was sent. No Google calendar event was written. The autostart switch was restored immediately after its case. The isolated QA background server is stopped after evidence capture; the preexisting native app is not terminated by cleanup.

- C:\qa_work\FEATURE_LOG.md
- C:\qa_work\BUGS.md
- C:\qa_work\MISSING_FEATURES.md
- C:\qa_work\results.jsonl
- C:\qa_work\FINAL_SUMMARY.json
- C:\qa_work\FINAL_STATE_EXPORT.json
- C:\qa_work\CASE_TIMINGS.json
- Screenshots: C:\qa_work\out\qa\
