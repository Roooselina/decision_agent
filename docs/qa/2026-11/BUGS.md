# QA bugs

Latest result per stable ID; QA-driver errors superseded by a corrected run are not product bugs.

## B01 Add first folder and complete initial check

Severity: High

Steps: Add clean inner capstone copy; watch reading and extraction progress

Expected: Initial check starts, progress visible, ends with completion record

Actual: Progress was visible, but extraction failed at 256/326 with 결정 추출에 실패했습니다. 모델 호출에 실패했습니다: Unterminated string starting at: line 1 column 246 (char 245). No completion record.

Evidence: B01_add.json, B01_failure_state.json, out/qa/B01_extract_failure.png

Notes: Failure occurred before confirmation gate and consistency analysis. Raw JSON parsing error shown to the user.

## B02 Independent second folder startup

Severity: Medium

Steps: Start capstone scan. While extraction is running POST /api/folders/add for full copy. Wait 30 seconds. Read status and home.

Expected: Second folder starts independently while first runs

Actual: Folder add request timed out after 30s. Status and home still showed only one folder running.

Evidence: B02_before.json, B02_add.json, out/qa/B02_add_during_scan.png, ui/B02_add_during_scan.json

Notes: Implementation inspection corroborates: add_folder calls reconfigure which waits on jobs.exclusive. No application changes made.

## B07 Remove and re-add unchanged project

Severity: Medium

Steps: Remove only QA F08 through app; re-add unchanged folder; count real requests

Expected: Rebuild from stored data without a full model run

Actual: {"requests": 5, "hashes_unchanged": true, "state": {"state": "verified", "label": "검사 완료", "detail": "2026-10-30T12:59:35"}}

Evidence: B07_readd.json, out/qa/B07_readd.png

Notes: App removes per-folder state database on removal, so cached verdicts are lost. User files remained byte-identical.

## B09 Unsupported and locked files

Severity: Medium

Steps: Index a fresh Windows deny-sharing locked markdown plus db/sqlite; wait for retries

Expected: Skipped with plain message and no crash

Actual: {"locked_filename": "locked_070714.md", "locked_file_row": null, "file_statuses": [["cp949.txt", "ok", ""], ["locked.md", "ok", ""], ["locked_030941.md", "ok", ""], ["protected.pdf", "error", "File has not been decrypted"]], "scan_stats": {"at": "2026-11-02T07:07:14", "seen": 8, "skipped": {"파일이 최대 크기를 넘습니다.": 1, "읽을 수 없는 형식입니다.": 2}}, "server_alive": {"app": "decision-agent", "version": "0.2.0"}}

Evidence: B09_file_edges.json, out/qa/B09_locked_unsupported.png

Notes: Fresh locked file has no indexed error/status row or readable locked-file warning. Unreadable-format skips have a generic reason. Earlier repeat reused a cached file; this fresh filename prevents that QA ambiguity. Unlock reread is in B09_after_unlock.json.

## C01 Cold balanced scan requests and time

Severity: High

Steps: Cold capstone copy, real configured models, balanced

Expected: Record complete scan requests, wall time and reused lines

Actual: Cold scan did not complete: decision extraction returned invalid/truncated JSON after about 96 seconds. No findings or completion cost record.

Evidence: B01_failure_state.json, out/qa/B01_extract_failure.png

Notes: Requests will be reported from HTTP response log; the state export has no last_cycle for this failed check.

## C03 One stale requirement line via real watcher

Severity: Medium

Steps: Append one stale FR-QA03 line, do not press rescan, observe actual native watcher and debounce

Expected: Few requests; finding appears within 5 seconds plus model latency

Actual: {"requests": 3, "seconds": 36.97359470004449, "model_phase_seconds": 6.606, "new_findings": 1, "debounce_setting": 30.0}

Evidence: C03_incremental.json, out/qa/C03_incremental.png

Notes: 

## D02 Substring search over filenames and text

Severity: Medium

Steps: Search SSO, Lightsail, 11/17, impossible word, requirements.md through quick-search API

Expected: Substring matches indexed file text and names; impossible word has zero matches

Actual: {"SSO": 14, "Lightsail": 0, "11/17": 0, "QA_NO_SUCH_WORD_49176": 0, "requirements.md": 30}

Evidence: D02_search.json, out/qa/D02_search.png

Notes: Lightsail occurs in the indexed capstone deploy guide. Search implementation uses finding text/topic summaries/filenames/notes, not all indexed file text; failed scan makes this gap visible.

## E01 Real-model demo_capstone answer-key check

Severity: High

Steps: Cold first scan failed; replay saved extraction after restart; inspect findings/state

Expected: Exactly six stale file lines

Actual: {"state": {"state": "retrying", "label": "실패, 다시 시도 중", "detail": "결정 추출에 실패했습니다. 모델 호출에 실패했습니다: Unterminated string starting at: line 1 column 246 (char 245)"}, "completion": null, "findings": 0, "retry_seconds": 39.93799999996554, "reported_retry_calls": 0}

Evidence: E01_demo_result.json, out/qa/E01_demo_failure.png

Notes: An incomplete scan is not an accuracy score. The initial cold failure is also evidenced in B01_failure_state.json and checkpoint snapshots.

## E04 Real-model demo_capstone_full answer-key check

Severity: High

Steps: Cold first scan failed; replay saved extraction after restart; inspect findings/state

Expected: Record recall out of ten; benchmark target nine

Actual: {"state": {"state": "retrying", "label": "실패, 다시 시도 중", "detail": "결정 추출에 실패했습니다. 모델 호출에 실패했습니다: Extra data: line 3 column 1 (char 1277)"}, "completion": null, "findings": 0, "retry_seconds": 1.3119999999180436, "reported_retry_calls": 0}

Evidence: E04_demo_result.json, out/qa/E04_demo_failure.png

Notes: An incomplete scan is not an accuracy score. The initial cold failure is also evidenced in B01_failure_state.json and checkpoint snapshots.

## E06 Old demos: exact file-and-line accuracy

Severity: High

Steps: Real models; user-approved estimate424; compare (source file,text) pairs against external160-line key

Expected: Big >=145/160; report false findings and time

Actual: {"bookswap_bad": 51, "bookswap_uncertain": 36, "bookswap_requests": 199, "bookswap_seconds": 183.59400000004098, "big_exact": "140/160", "big_normalized": "143/160", "big_exact_extra_flags": 118, "big_normalized_extra_labels": {"CONSISTENT": 90, "HISTORICAL": 22, "unmatched": 3}, "big_requests": 349, "big_seconds_excluding_approval_wait": 467.704000000027}

Evidence: E06_old_demos.json, E06_424_approval.json, E06_big_scoring.json, E06_big_approved_result.json, out/qa/E06_big_approved.png

Notes: Corrected scoring uses file+text, as bench_engine does. Text-only scoring wrongly collapsed160 separate expected artifacts to107 strings. Normalization removes whitespace/punctuation/bullets only; unmatched extras are reviewed separately from definite known-consistent false positives.

## F02 기능 취소

Severity: High

Steps: Add C:\qa_work\suite\F02; allow <=400 estimate; inspect real findings and questions

Expected: {"stale": ["토스페이먼츠 결제 위젯", "토스페이먼츠 승인"], "correct": ["FR-01", "계좌이체 안내"], "question": false}

Actual: {"state": {"state": "verified", "label": "검사 완료", "detail": "2026-10-29T23:56:49"}, "found": [], "missed": ["토스페이먼츠 결제 위젯", "토스페이먼츠 승인"], "false_findings": [], "questions": 1, "requests": 6, "seconds": 20.284}

Evidence: cases/F02.json, out/qa/F02_results.png

Notes: Case result is from configured real model, not simulation. Fixture expectations stay outside watched folder.

## F03 MVP 범위 제한

Severity: High

Steps: Add C:\qa_work\suite\F03; allow <=400 estimate; inspect real findings and questions

Expected: {"stale": ["FR-01", "MVP 사진 3장 제한"], "correct": ["FR-02", "FR-03", "Production 사진 3장", "MVP에서 사진"], "question": false}

Actual: {"state": {"state": "verified", "label": "검사 완료", "detail": "2026-10-29T23:57:13"}, "found": [], "missed": ["FR-01", "MVP 사진 3장 제한"], "false_findings": [], "questions": 1, "requests": 5, "seconds": 20.752}

Evidence: cases/F03.json, out/qa/F03_results.png

Notes: Case result is from configured real model, not simulation. Fixture expectations stay outside watched folder.

## F06 Clarification answer is saved but did not rejudge

Severity: High

Steps: Answer MVP-only scope; inspect immediate verdicts and saved answer after restart

Expected: Answer triggers selective rejudge; only MVP stale; answer survives restart

Actual: {"answers_saved": 1, "requests_after_answer": 0, "immediate_uncertain": 5}

Evidence: F06_answer.json, F06_restart_answers.json, out/qa/F06_answer.png

Notes: The answer persisted, but the immediate answer action cleared the question while all five uncertain verdicts stayed unchanged. Later profile experiments are separate from this failed answer flow.

## F09 docx와 eml 결정 중복

Severity: High

Steps: Add C:\qa_work\suite\F09; allow <=400 estimate; inspect real findings and questions

Expected: {"stale": ["FR-01", "이메일 인증 메일 구현", "이메일 인증 메일 수신"], "correct": ["FR-02", "학교 구글 SSO"], "question": false}

Actual: {"state": {"state": "verified", "label": "검사 완료", "detail": "2026-10-30T00:05:51"}, "found": ["FR-01", "이메일 인증 메일 구현"], "missed": ["이메일 인증 메일 수신"], "false_findings": [], "questions": 1, "requests": 11, "seconds": 35.419}

Evidence: cases/F09.json, out/qa/F09_results.png

Notes: Case result is from configured real model, not simulation. Fixture expectations stay outside watched folder.

## F10 제목 결정/확정/액션인 eml 본문

Severity: High

Steps: Add C:\qa_work\suite\F10; allow <=400 estimate; inspect real findings and questions

Expected: {"stale": ["FR-01", "이메일 인증 메일 구현", "이메일 인증 메일 수신"], "correct": ["FR-02", "학교 구글 SSO"], "question": false}

Actual: {"state": {"state": "verified", "label": "검사 완료", "detail": "2026-10-30T00:06:25"}, "found": ["FR-01"], "missed": ["이메일 인증 메일 구현", "이메일 인증 메일 수신"], "false_findings": [], "questions": 1, "requests": 7, "seconds": 30.372}

Evidence: cases/F10.json, out/qa/F10_results.png

Notes: Case result is from configured real model, not simulation. Fixture expectations stay outside watched folder.

## F12 메일 인용문 제외

Severity: High

Steps: Add C:\qa_work\suite\F12; allow <=400 estimate; inspect real findings and questions

Expected: {"stale": [], "correct": ["FR-01", "이메일 인증 메일 구현"], "question": false}

Actual: {"state": {"state": "verified", "label": "검사 완료", "detail": "2026-10-30T00:07:32"}, "found": [], "missed": [], "false_findings": ["FR-01 로그인은 이메일 인증을 사용한다.", "이메일 인증 메일 구현"], "questions": 1, "requests": 14, "seconds": 43.995}

Evidence: cases/F12.json, out/qa/F12_results.png

Notes: Case result is from configured real model, not simulation. Fixture expectations stay outside watched folder.

## F15 날짜 없는 회의

Severity: Medium

Steps: Add C:\qa_work\suite\F15; allow <=400 estimate; inspect real findings and questions

Expected: {"stale": [], "correct": ["FR-01", "상품 사진 3장"], "question": false}

Actual: {"state": {"state": "verified", "label": "검사 완료", "detail": "2026-10-30T00:00:04"}, "found": [], "missed": [], "false_findings": ["FR-01 상품 사진 제한은 최대 3장이다.", "상품 사진 3장 제한 구현", "사진 3장 제한을 검증한다."], "questions": 0, "requests": 4, "seconds": 14.367}

Evidence: cases/F15.json, out/qa/F15_results.png

Notes: Case result is from configured real model, not simulation. Fixture expectations stay outside watched folder.

## F17 Plain Korean clarification question controls

Severity: Medium

Steps: Open a real pending question from Home; inspect dialog/actions and total questions

Expected: One at a time; 답변 and 건너뛰기; configured question limit

Actual: {"home_question_count": 7, "buttons": ["", "예", "아니오", "답변 저장", "취소"], "dialog_text": "결정 변경 범위 확인\n\nFR-QA04 요구사항의 사진 첨부 한도를 최신 확정 결정(2026-10-21)에 따라 '최대 3장'에서 '최대 6장'으로 갱신해도 됩니까? (예/아니오)\n\n예\n아니오\n\n출처: 상품 사진 제한을 최대 5장에서 최대 6장으로 변경하기로 확정한다.\n\n답변 저장\n취소"}

Evidence: F17_question_dialog.json, out/qa/F17_question_dialog.png

Notes: Global max questions is not exposed in desktop settings. Multiple project questions can coexist; dialog itself opens one at a time.

## G07 Mail search and keyboard navigation

Severity: Medium

Steps: Search exact poster substring; clear; focus first thread; ArrowDown, Enter, Escape

Expected: Search filters; arrows move focus, Enter opens, Esc returns

Actual: {"filtered": 1, "focus": "trow unread", "selected": 1, "after_escape": 1}

Evidence: G07_keyboard.json, out/qa/G07_search_keyboard.png

Notes: Date filter chips visually present; date boundary behavior not exhaustively tested.

## QA-SETTINGS Settings request waits behind active checks

Severity: Medium

Steps: With several precise scans running, change cost profile or add another folder

Expected: Settings/folder operation should remain responsive and isolate folders

Actual: Settings change and concurrent folder addition each exceeded 30s HTTP timeout. Required stopping active QA folders before restoring balanced settings.

Evidence: C06_profiles.json, profile_timeout_state.json, profile_recovery.json, out/qa/QA_settings_timeout.png

Notes: Global reconfigure waits for all active jobs. Concurrent QA folder-add attempt was stopped by driver timeout; final root presence checked before repeating that case.

