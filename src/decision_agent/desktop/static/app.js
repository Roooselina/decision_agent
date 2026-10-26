/* Dashboard: layout, router, and every screen. Uses the helpers in common.js. */
"use strict";

(() => {
  const {h, set, icon, api, button, busy, badge, field, input, textarea, select, toggle, segmented, callout,
    progressBar, skeleton, emptyState, errorState, toast, modal, confirmDialog, drawer, anyLayerOpen,
    copyText, applyTheme, fmtTime, relTime, stateInfo, stateBadge, transmissionBadge, labelBadge, KINDS,
    providerName, providerKind, agentState} = DA;

  const S = {status: null, settings: null, offline: false};
  const REFRESH_MS = 4000;
  const PURGE_CONFIRM = "삭제";

  // ---------------------------------------------------------------- routing

  // The Figma sidebar has four screens. Older pages live on inside them; their old addresses
  // (from bookmarks, toasts, or the tray) are sent to the new place.
  const NAV = ["home", "results", "folders", "settings"];
  const ROUTES = {};
  const MOVED = {
    questions: ["home", {}], digest: ["home", {}], activity: ["home", {log: "1"}],
    accounts: ["settings", {tab: "connect"}], profile: ["settings", {tab: "data"}], notes: ["folders", {}],
  };

  function parseHash() {
    const raw = (location.hash || "#/home").slice(2);
    let [name, query] = raw.split("?");
    let params = new URLSearchParams(query || "");
    if (MOVED[name]) {
      const [target, extra] = MOVED[name];
      for (const [k, v] of Object.entries(extra)) if (!params.has(k)) params.set(k, v);
      name = target;
    }
    return {name: ROUTES[name] ? name : "home", params};
  }

  function go(name, params) {
    const query = params ? "?" + new URLSearchParams(params).toString() : "";
    const target = "#/" + name + query;
    if (location.hash === target) renderRoute(true);
    else location.hash = target;
  }

  let renderSeq = 0;
  let lastSig = "";

  async function renderRoute(force) {
    const {name, params} = parseHash();
    const route = ROUTES[name];
    const view = document.getElementById("view");
    const seq = ++renderSeq;
    if (force) {
      lastSig = "";
      set(view, page(route.title, route.lead, null, skeleton(5), skeleton(3)));
      document.title = route.title + " - Decision Agent";
    }
    let data;
    try {
      data = await route.load(params);
    } catch (e) {
      if (seq !== renderSeq) return;
      lastSig = "";
      set(view, page(route.title, null, null, errorState(e.message, () => renderRoute(true))));
      return;
    }
    if (seq !== renderSeq) return;
    const sig = JSON.stringify(data);
    if (!force && sig === lastSig) return;
    lastSig = sig;
    const scroll = view.scrollTop;
    set(view, route.render(data, params));
    if (!force) view.scrollTop = scroll;
    renderNav();
  }

  function page(title, lead, actions, ...body) {
    return h("div", {class: "page"},
      h("div", {class: "page-head"},
        h("div", {}, h("h1", {}, title), lead ? h("p", {class: "lead"}, lead) : null),
        actions ? h("div", {class: "btn-group"}, actions) : null),
      ...body);
  }

  function isTyping() {
    const el = document.activeElement;
    return el && ["INPUT", "TEXTAREA", "SELECT"].includes(el.tagName);
  }

  // ---------------------------------------------------------------- chrome

  // Sidebar: "작업 공간", the four screens, then "빠른 점검" and one status line
  // ("로컬 AI · 정상", "시연용 모드", "로컬 AI · 오류", ...).
  const NAV_ICONS = {home: "home", results: "list", folders: "folder", settings: "settings"};

  function renderNav() {
    const nav = document.getElementById("nav");
    const current = parseHash().name;
    const st = S.status;
    const count = st ? st.inconsistent : 0;
    const item = (name) => {
      const r = ROUTES[name];
      const badgeCount = name === "results" ? count : 0;
      return h("a", {class: "nav-item", href: "#/" + name, "aria-current": name === current ? "page" : null},
        icon(NAV_ICONS[name]), h("span", {class: "nav-label"}, r.title),
        badgeCount ? h("span", {class: "count-pill", "aria-label": "반영 안 됨 " + badgeCount + "건"}, String(badgeCount)) : null);
    };
    set(nav, h("div", {class: "nav-group-label"}, "작업 공간"), ...NAV.map(item));
    renderSidebarFoot();
  }

  // One line under the quick-check button. It never says "정상" for the demo judge.
  function sideStatus(st) {
    if (S.offline || !st) return ["error", "alert-circle", "프로그램 연결 끊김"];
    const judge = providerName(st.provider.id);
    const state = agentState(st);
    if (state === "demo" || (st.provider.local_only && state !== "failed")) {
      if (state === "paused") return ["paused", "pause-bars", "시연용 모드 · 일시 중지"];
      return ["demo", "alert-circle", "시연용 모드"];
    }
    const info = DA.STATES[state] || DA.STATES.incomplete;
    const cls = {ok: "ok", busy: "busy", paused: "paused", error: "error", attention: "demo"}[info.cls] || "ok";
    return [cls, state === "verified" ? "shield-check" : info.icon, judge + " · " + info.short];
  }

  function renderSidebarFoot() {
    const foot = document.getElementById("sidebar-foot");
    const [cls, iconName, text] = sideStatus(S.status);
    const line = h("div", {class: "side-status " + cls, role: "status"}, icon(iconName), h("span", {class: "truncate"}, text));
    if (iconName === "loader") line.querySelector("svg").classList.add("spin");
    set(foot, button("빠른 점검", {onClick: openQuick}), line);
  }

  // Opens the quick-check window (its own always-on-top window in the desktop app).
  async function openQuick() {
    try {
      const r = await api("/api/app/activate", "POST", {page: "/quick"});
      if (r.ok) return;
    } catch (e) { /* fall back to a tab below */ }
    window.open("/quick", "_blank", "noopener,width=480,height=640");
  }

  let openPopover = null;
  function closePopover() { if (openPopover) { openPopover.remove(); openPopover = null; } }
  document.addEventListener("mousedown", (e) => {
    if (openPopover && !openPopover.parentNode.contains(e.target)) closePopover();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closePopover();
  });

  function append(node, children) { DA.append(node, children); return node; }

  async function saveTheme(theme) {
    applyTheme(theme);
    try {
      await api("/api/settings", "POST", {theme});
      S.settings.theme = theme;
    } catch (e) { toast(e.message, "danger"); }
  }

  async function openLocation(target) {
    try { await api("/api/folders/open", "POST", {target}); } catch (e) { toast(e.message, "danger"); }
  }

  // ---------------------------------------------------------------- shared pieces

  function card(title, sub, actions, ...body) {
    return h("section", {class: "card"},
      title ? h("div", {class: "card-head"}, h("div", {}, h("h2", {}, title), sub ? h("div", {class: "sub"}, sub) : null),
        actions ? h("div", {class: "btn-group"}, actions) : null) : null,
      ...body);
  }

  function folderName(path) {
    const parts = String(path || "").split(/[\\/]/).filter(Boolean);
    return parts[parts.length - 1] || path;
  }

  function lastCompletion(st) {
    const times = st.folders.map(f => f.completion && f.completion.at).filter(Boolean).sort();
    return times.length ? times[times.length - 1] : null;
  }

  async function refreshAll() {
    await tick();
    await renderRoute(false);
  }

  // ---------------------------------------------------------------- home

  ROUTES.home = {
    title: "홈", auto: true,
    async load(params) {
      if (calendarIsStale()) resetCalendar();
      if (params.has("week")) { HS.week = params.get("week"); touchCalendar(); }
      const [findings, questions, activity, week] = await Promise.all([
        api("/api/findings"), api("/api/questions"), api("/api/activity"),
        api("/api/calendar" + (HS.week ? "?start=" + encodeURIComponent(HS.week) : "")),
      ]);
      const st = S.status || await api("/api/status");
      return {st, findings, questions, activity: activity.slice(0, 5), week, flash: HS.flash};
    },
    render({st, findings, questions, activity, week}, params) {
      if (params.get("log")) setTimeout(() => { history.replaceState(null, "", "#/home"); openLogModal(); }, 30);
      if (st.registered_folders === 0) {
        return page("홈", null, [h("span", {class: "page-date"}, longDate(new Date()))], h("section", {class: "card"}, emptyState({
          icon: "folder-plus", title: "확인할 폴더를 추가하세요",
          text: "회의록, 요구사항, 작업 표, 테스트 계획, 일정이 있는 프로젝트 폴더를 고르면 결정이 바뀌었는데 반영되지 않은 항목을 찾아 알려 줍니다. 선택한 폴더와 그 안의 모든 폴더를 포함합니다.",
          action: button("폴더 추가", {variant: "primary", onClick: () => startAddFolder()}),
        })));
      }
      const partial = agentState(st) === "partial"
        ? callout("warning", "alert", st.overall.label, st.overall.detail || "") : null;
      return page("홈", null, [h("span", {class: "page-date"}, longDate(new Date()))],
        partial, ...confirmBanners(st), agentCard(st),
        h("div", {class: "home-grid"}, calendarCard(week, st), todoCard(st, findings, questions)),
        recentCard(st, activity));
    },
  };

  // Folders whose costly judging stage waits for the user. "나중에" only folds the card for this
  // wait (keyed by its numbers); nothing runs until "계속 점검" is pressed.
  const CONFIRM_HIDDEN = new Set();
  function confirmKey(f) { const w = f.pending_confirm; return f.root + "|" + w.estimated_calls + "|" + w.calls_so_far + "|" + w.topics; }
  function confirmBanners(st) {
    if (st.paused) return [];
    const many = st.folders.length > 1;
    return st.folders.filter(f => f.pending_confirm && !CONFIRM_HIDDEN.has(confirmKey(f))).map(f => {
      const w = f.pending_confirm;
      const go_ = button("계속 점검", {variant: "primary", onClick: (e) => busy(e.currentTarget, async () => {
        await api("/api/confirm", "POST", {root: f.root});
        toast("점검을 이어서 진행합니다.", "info");
        await refreshAll();
      })});
      // Remove the card itself: the home data did not change, so a re-render would be skipped.
      const later = button("나중에", {onClick: (e) => { CONFIRM_HIDDEN.add(confirmKey(f)); e.currentTarget.closest(".confirm-banner").remove(); }});
      const lower = h("button", {type: "button", class: "link", onclick: () => go("settings", {tab: "connect", section: "cost"})}, "설정에서 정밀도 낮추기");
      const body = h("div", {},
        many ? h("p", {class: "small muted"}, folderName(f.root)) : null,
        h("p", {}, "이번 점검에 약 " + w.estimated_calls + "회 호출이 예상됩니다 (기준 " + w.limit + "회). 결정 추출은 이미 끝났고, 이어서 " + w.topics + "개 결정을 점검합니다."));
      const node = callout("warning", "alert", "모델 호출이 많이 예상됩니다", body, [go_, later, lower]);
      node.classList.add("confirm-banner");
      return node;
    });
  }

  // week and day are what the user picked with the arrows; null means "the week of today, on
  // today". They are reset whenever home is opened again or the window comes back, so a window
  // left open for days never starts on an old week.
  const HS = {week: null, day: null, flash: null, touched: 0};
  const CALENDAR_IDLE_MS = 30 * 60 * 1000;
  function resetCalendar() { HS.week = null; HS.day = null; HS.touched = 0; }
  function touchCalendar() { HS.touched = Date.now(); }
  function calendarIsStale() { return (HS.week !== null || HS.day !== null) && Date.now() - HS.touched > CALENDAR_IDLE_MS; }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden && parseHash().name === "home") { resetCalendar(); renderRoute(false); }
  });
  const WEEKDAYS = ["일", "월", "화", "수", "목", "금", "토"];

  function toDate(iso) {
    const [y, m, d] = String(iso).slice(0, 10).split("-").map(Number);
    return new Date(y, m - 1, d);
  }
  function isoDay(d) {
    const pad = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate());
  }
  function addDays(iso, n) { const d = toDate(iso); d.setDate(d.getDate() + n); return isoDay(d); }
  function longDate(d) { return (d.getMonth() + 1) + "월 " + d.getDate() + "일 " + WEEKDAYS[d.getDay()] + "요일"; }
  function clock(iso) { return iso ? String(iso).slice(11, 16) : "—"; }
  function baseName(path) { return String(path || "").split(/[\\/]/).filter(Boolean).pop() || path; }

  // "14:32" for today, "10월 23일 14:32" otherwise.
  function stamp(iso) {
    if (!iso) return "—";
    return String(iso).slice(0, 10) === isoDay(new Date()) ? clock(iso) : (toDate(iso).getMonth() + 1) + "월 " + toDate(iso).getDate() + "일 " + clock(iso);
  }

  // A relative time that keeps moving without re-rendering ("3분 전").
  function relSpan(iso, cls) {
    return h("span", {class: cls || "rel-time", "data-rel": iso || ""}, iso ? relTime(iso) : "");
  }
  setInterval(() => document.querySelectorAll("[data-rel]").forEach(el => {
    if (el.dataset.rel) el.textContent = relTime(el.dataset.rel);
  }), 30000);

  // Running numbers for the thin bar: files while reading, meeting items while extracting
  // decisions ("결정 추출 n/m"), decisions while judging ("판정 n/m").
  function runningProgress(st) {
    let done = 0, total = 0, text = "";
    for (const f of st.folders) {
      const p = f.progress;
      if (p.phase === "checking" && p.topics_total) { done += p.topics_done; total += p.topics_total; text = "판정"; }
      else if (p.phase === "extracting" && p.extract_total) { done += p.extract_done; total += p.extract_total; text = text === "판정" ? text : "결정 추출"; }
      else if (p.files_total) { done += p.files_done; total += p.files_total; text = text || "문서"; }
    }
    const waiting = st.folders.some(f => f.progress.phase === "awaiting_confirm" || f.pending_confirm) ? " · 확인 대기" : "";
    if (!total) return {pct: 0, text: "문서를 확인하고 있습니다." + waiting};
    const label = text === "문서" ? "문서 " + total + "개 중 " + done + "개 확인" : text + " " + done + "/" + total;
    return {pct: Math.round(done * 100 / total), text: label + waiting};
  }

  // The "문서 확인 AI AGENT" card (Figma 15:44 and the 문서 확인 상태 카드 variants).
  function agentCard(st) {
    const state = agentState(st);
    const info = stateInfo(st.overall, st.paused);
    const watch = st.watch || {};
    const demo = state === "demo";
    const ICON = {verified: ["shield-check", "ok"], running: ["loader", "busy"], paused: ["pause-bars", "paused"],
      failed: ["alert-circle", "error"], demo: ["shield-check", "demo"], retrying: ["alert-circle", "attention"], partial: ["alert-circle", "attention"],
      stale: ["alert-circle", "attention"], incomplete: ["clock", "busy"], awaiting_confirm: ["clock", "attention"]};
    const [iconName, tone] = ICON[state] || ICON.incomplete;
    const mainIcon = icon(iconName, "agent-icon " + tone + (state === "running" ? " spin" : ""));
    const rescan = h("button", {type: "button", class: "btn ghost icon-only sm", title: "지금 확인하기", "aria-label": "지금 확인하기",
      onclick: (e) => busy(e.currentTarget, async () => { await api("/api/rescan", "POST", {}); toast("지금 확인을 시작했습니다.", "info"); await refreshAll(); })},
    icon("refresh", "sm"));
    const head = h("div", {class: "agent-head"}, mainIcon, h("h2", {class: "agent-title"}, "문서 확인 AI AGENT"),
      h("div", {class: "agent-badge"}, rescan, stateBadge(st.overall, null, st.paused)));
    const folders = st.paused ? "0개 · 등록 " + st.registered_folders + "개" : st.registered_folders + "개";
    const metaBlock = (label, value, extra) => h("div", {class: "agent-meta"}, h("span", {class: "agent-label"}, label),
      h("span", {class: "agent-value"}, value, extra || null));
    let right = null, action = null, bar = null, sub = null;
    if (state === "running") {
      const p = runningProgress(st);
      right = h("span", {class: "agent-note"}, p.text);
      bar = h("div", {class: "agent-bar", role: "progressbar", "aria-valuenow": String(p.pct), "aria-valuemin": "0", "aria-valuemax": "100",
        "aria-label": "확인 진행도"}, h("div", {style: {width: Math.max(p.pct, 4) + "%"}}));
    } else if (state === "paused") {
      right = h("span", {class: "agent-note"}, "자동 확인이 일시 중지되었습니다.");
      action = button("다시 시작", {variant: "primary", onClick: async (e) => busy(e.currentTarget, async () => {
        await api("/api/pause", "POST", {paused: false}); await refreshAll(); })});
    } else if (state === "failed") {
      right = h("span", {class: "agent-note danger", title: info.detail || ""}, aiErrorText(info.detail));
      action = button("연결 설정 확인", {variant: "primary", onClick: () => go("settings", {tab: "connect", section: "judge"})});
    } else if (demo) {
      sub = h("p", {class: "agent-sub"}, "시연용 판정기로 동작 중입니다. 실제 문서 검사가 수행되지 않습니다.");
      action = button("AI 설정", {onClick: () => go("settings", {tab: "connect", section: "judge"})});
    } else {
      const r = st.last_result;
      const sends = st.provider.sends_externally;
      right = h("div", {class: "agent-right"},
        r && state === "verified" ? h("span", {class: "agent-note"}, "검사 완료 · 문서 " + r.files + "개, 결정 " + r.topics + "건 확인") : null,
        state === "retrying" || state === "stale" || state === "partial" ? h("span", {class: "agent-note warn", title: info.detail || ""}, info.label) : null,
        state === "awaiting_confirm" ? h("span", {class: "agent-note warn", title: info.detail || ""}, "확인 대기 · " + (info.detail || "")) : null,
        state === "incomplete" ? h("span", {class: "agent-note"}, "첫 확인을 준비하고 있습니다.") : null,
        h("span", {class: "agent-note" + (sends ? " info" : "")}, sends ? "문서 처리: 외부 AI로 전송 중 (" + st.provider.id + ")" : "문서 처리: 내 컴퓨터 · 외부 전송 안 함"));
    }
    const metas = demo
      ? h("div", {class: "agent-meta-line"}, "마지막 확인 ", stamp(watch.last_checked), "   ·   확인 중인 폴더 " + folders)
      : h("div", {class: "agent-metas"},
        metaBlock("마지막 확인", stamp(watch.last_checked), watch.last_checked ? relSpan(watch.last_checked, "agent-rel") : null),
        metaBlock("확인 중인 폴더", folders),
        h("div", {class: "agent-side"}, right));
    const usage = state === "running" ? null : usageBlock(st);
    return h("section", {class: "card agent-card " + tone + (demo ? " demo" : ""), "aria-label": "문서 확인 상태"},
      head, sub, metas, usage, action ? h("div", {class: "agent-action"}, action) : null, bar);
  }

  // Model calls of the last finished check, per folder, with the calls by kind of work folded away.
  const TASK_NAMES = {DECISION_EXTRACT: "결정 추출", DECISION_EXTRACT_BATCH: "결정 추출", RELATION_CLASSIFY: "관계 분류",
    IMPACT_SELECT: "영향 대상 선택", CONSISTENCY: "일치 판정", CONSISTENCY_BATCH: "일치 판정", QUESTION_GENERATE: "확인 질문",
    QUESTION_GAIN: "확인 질문", QUESTION_GAIN_BATCH: "확인 질문", TOOL_SELECT: "도구 선택"};
  function taskRows(byTask) {
    const sums = new Map();
    for (const [task, n] of Object.entries(byTask || {})) {
      const name = TASK_NAMES[task] || "기타";
      sums.set(name, (sums.get(name) || 0) + Number(n || 0));
    }
    return [...sums.entries()].sort((a, b) => b[1] - a[1]);
  }
  function usageBlock(st) {
    const done = st.folders.filter(f => f.completion && typeof f.completion.llm_calls === "number" && providerKind(f.completion.provider_id) !== "demo");
    if (!done.length) return null;
    const many = st.folders.length > 1;
    return h("div", {class: "agent-usage"}, ...done.map(f => {
      const c = f.completion;
      const rows = taskRows(c.calls_by_task);
      return h("div", {class: "usage-folder"},
        h("p", {class: "usage-line"}, (many ? folderName(f.root) + " · " : "") + "이번 점검: 모델 호출 " + c.llm_calls + "회, AI 없이 확인한 줄 " + (c.local_answers || 0) + "건"),
        rows.length ? h("details", {class: "usage-details"}, h("summary", {}, "호출 내역", icon("chevron-right", "sm")),
          h("ul", {class: "usage-list"}, ...rows.map(([name, n]) => h("li", {}, h("span", {}, name), h("span", {class: "usage-n"}, n + "회"))))) : null);
    }));
  }

  function aiErrorText(detail) {
    const text = String(detail || "");
    if (/키|key|인증|401|403/i.test(text)) return "AI 연결을 확인할 수 없습니다. API 키를 확인하세요.";
    if (/모델|model/i.test(text)) return "AI 연결을 확인할 수 없습니다. 모델 이름을 확인하세요.";
    return "AI 연결을 확인할 수 없습니다.";
  }

  // ---------------------------------------------------------------- calendar card

  function calendarCard(week, st) {
    const noSource = !week.calendar_connected && !week.has_events;
    if (noSource) {
      return h("section", {class: "card home-card cal-card", "aria-label": "캘린더"},
        h("div", {class: "home-card-head"}, h("h2", {}, "캘린더")),
        h("div", {class: "cal-empty"}, icon("calendar", "cal-empty-icon"),
          h("strong", {}, "캘린더가 연결되지 않았습니다."),
          h("p", {}, "계정을 연결하면 관련 일정을 확인할 수 있습니다."),
          agentState(st) === "demo" ? null : button("캘린더 연결", {variant: "primary", onClick: () => go("settings", {tab: "connect", focus: "google_calendar"})})));
    }
    const start = week.start;
    const days = Array.from({length: 7}, (_, i) => addDays(start, i));
    const byDay = new Map(days.map(d => [d, []]));
    for (const e of week.events) if (byDay.has(e.date)) byDay.get(e.date).push(e);
    if (!HS.day || !days.includes(HS.day)) {
      HS.day = days.includes(week.today) ? week.today : (days.find(d => byDay.get(d).length) || days[0]);
    }
    const s = toDate(start), e = toDate(week.end);
    const range = s.getFullYear() + "년 " + (s.getMonth() + 1) + "월 " + s.getDate() + "일–" +
      (e.getMonth() !== s.getMonth() ? (e.getMonth() + 1) + "월 " : "") + e.getDate() + "일";
    const move = (n) => { HS.week = addDays(start, 7 * n); HS.day = null; touchCalendar(); renderRoute(false); };
    const isThisWeek = days.includes(week.today);
    const back = () => { resetCalendar(); renderRoute(false); };
    const todayBtn = h("button", {type: "button", class: "btn ghost sm cal-today", title: "오늘이 있는 주로 돌아가기", onclick: back}, "오늘");
    const list = h("div", {class: "cal-list"});
    const strip = h("div", {class: "cal-strip", role: "tablist", "aria-label": "요일"});
    const drawDay = () => {
      todayBtn.disabled = isThisWeek && HS.day === week.today;
      set(strip, ...days.map(d => {
        const date = toDate(d);
        return h("button", {type: "button", class: "cal-day" + (d === HS.day ? " selected" : "") + (d === week.today ? " today" : ""),
          role: "tab", "aria-selected": String(d === HS.day), "aria-label": longDate(date) + " 일정 " + byDay.get(d).length + "건",
          onclick: () => { HS.day = d; touchCalendar(); drawDay(); }},
        h("span", {class: "cal-wd"}, WEEKDAYS[date.getDay()]), h("span", {class: "cal-dn"}, String(date.getDate())),
        byDay.get(d).length ? h("span", {class: "cal-dot", "aria-hidden": "true"}) : null);
      }));
      const items = byDay.get(HS.day);
      if (items.length) {
        set(list, ...items.map(eventRow));
      } else if (week.events.length === 0) {
        set(list, h("div", {class: "cal-none"}, h("span", {}, "이 주에는 일정이 없습니다."),
          week.nearest_week ? h("button", {type: "button", class: "link", onclick: () => { HS.week = week.nearest_week; HS.day = null; touchCalendar(); renderRoute(false); }}, "일정이 있는 주로 이동") : null));
      } else {
        set(list, h("div", {class: "cal-none"}, h("span", {}, "이 날에는 일정이 없습니다.")));
      }
    };
    drawDay();
    return h("section", {class: "card home-card cal-card", "aria-label": "캘린더"},
      h("div", {class: "home-card-head"}, h("h2", {}, "캘린더"),
        h("div", {class: "cal-nav"},
          h("button", {type: "button", class: "icon-btn", "aria-label": "이전 주", title: "이전 주", onclick: () => move(-1)}, icon("chevron-left")),
          h("button", {type: "button", class: "icon-btn", "aria-label": "다음 주", title: "다음 주", onclick: () => move(1)}, icon("chevron-right")),
          todayBtn,
          h("span", {class: "cal-view"}, "주간"))),
      h("p", {class: "cal-range"}, range),
      strip, list);
  }

  function eventRow(e) {
    const linked = Boolean(e.decision);
    return h("button", {type: "button", class: "cal-event" + (linked ? " linked" : ""), onclick: () => openEvent(e)},
      h("span", {class: "cal-bar", "aria-hidden": "true"}),
      h("span", {class: "cal-time"}, e.time ? e.time : "종일"),
      h("span", {class: "cal-title truncate"}, e.title),
      linked ? h("span", {class: "cal-link"}, icon("link", "sm"), "결정 관련") : null);
  }

  // Figma 16:1247: the selected event and its linked decision.
  function openEvent(e) {
    const date = toDate(e.date);
    const d = e.decision;
    const body = h("div", {class: "stack-sm"},
      d ? h("div", {}, labelBadge(d.label)) : null,
      h("p", {class: "ev-when"}, longDate(date) + (e.time ? " · " + e.time : " · 종일")),
      h("p", {class: "small muted"}, "출처 " + e.source_name),
      d ? h("div", {class: "ev-decision"}, h("span", {class: "small muted"}, "연결된 결정"), h("strong", {}, d.topic_label),
        h("p", {class: "muted"}, d.evidence || (e.source_name + "의 " + e.title + " 항목이 변경된 결정과 맞는지 확인이 필요합니다."))) :
        h("p", {class: "muted"}, "이 일정과 연결된 결정이 없습니다."));
    modal({title: e.title, body, plain: true, actions: d
      ? [{label: "관련 점검 결과", variant: "primary", onClick: () => { go("results", {open: [d.root, d.topic, d.artifact_id].join("|")}); }}, {label: "닫기"}]
      : [{label: "닫기"}]});
  }

  // ---------------------------------------------------------------- to-do card

  function itemTitle(f) {
    const text = f.text.length > 26 ? f.text.slice(0, 25) + "…" : f.text;
    return text + (f.label === "INCONSISTENT" ? " 변경 미반영" : " 확인 필요");
  }

  function todoCard(st, findings, questions) {
    const open = findings.filter(f => f.label !== "CONSISTENT")
      .sort((a, b) => (a.label === "INCONSISTENT" ? 0 : 1) - (b.label === "INCONSISTENT" ? 0 : 1));
    const total = open.length + questions.length;
    const demo = agentState(st) === "demo";
    const flash = HS.flash && HS.flash.until > Date.now() ? HS.flash.text : null;
    const findingNode = (f) => h("button", {type: "button", class: "todo-item", onclick: () => go("results", {open: [f.root, f.topic, f.artifact_id].join("|")})},
      icon("alert-circle", "todo-icon " + (f.label === "INCONSISTENT" ? "bad" : "warn")),
      h("span", {class: "todo-main"}, h("span", {class: "todo-title truncate", title: f.text}, itemTitle(f)),
        h("span", {class: "todo-sub truncate"}, baseName(f.source_doc) + (f.owner ? " · " + f.owner : "")),
        h("span", {class: "todo-label " + (f.label === "INCONSISTENT" ? "bad" : "warn")}, DA.LABELS[f.label])),
      h("span", {class: "todo-more"}, "자세히"));
    const questionNode = (q) => h("button", {type: "button", class: "todo-question", onclick: () => openQuestion(q)},
      icon("alert-circle", "todo-icon warn"),
      h("span", {class: "todo-main"}, h("span", {class: "todo-qtitle truncate", title: q.text}, q.text),
        h("span", {class: "todo-sub truncate"}, q.topic_label + " · 확인 질문")),
      h("span", {class: "todo-more"}, "답변"));
    // As in the design: the two most serious items, then the AI questions, then the rest.
    const items = [...open.slice(0, 2).map(findingNode), ...questions.map(questionNode), ...open.slice(2).map(findingNode)];
    return h("section", {class: "card home-card todo-card", "aria-label": "내가 할 일"},
      h("div", {class: "home-card-head"}, h("h2", {}, "내가 할 일"),
        demo ? h("span", {class: "todo-count demo"}, "시연 데이터") : h("span", {class: "todo-count"}, "확인 대기 " + total + "건")),
      total === 0 && !flash
        ? h("div", {class: "todo-empty"}, icon("check", "todo-empty-icon"), h("strong", {}, "현재 처리할 항목이 없습니다."))
        : h("div", {class: "todo-list"}, ...items),
      flash ? h("p", {class: "todo-flash", role: "status"}, flash) : null,
      h("button", {type: "button", class: "link strong todo-all", onclick: () => go("results")}, "전체 결과 보기"));
  }

  function setFlash(text) {
    HS.flash = {text, until: Date.now() + 15000};
    setTimeout(() => { if (HS.flash && HS.flash.until <= Date.now()) { HS.flash = null; if (parseHash().name === "home") renderRoute(false); } }, 15200);
  }

  // Figma 16:1355 / 18:1622: pick an option (or type), then only that decision is checked again.
  function openQuestion(q) {
    const options = (q.options && q.options.length ? q.options : ["예", "아니오"]).slice(0, 4);
    let chosen = null;
    const extra = input({placeholder: "추가 내용을 입력하십시오", "aria-label": "추가 내용", maxlength: "500"});
    const optionBox = h("div", {class: "q-options", role: "radiogroup", "aria-label": "답변 선택지"});
    const drawOptions = () => set(optionBox, ...options.map(o => h("button", {type: "button", class: "q-option" + (o === chosen ? " selected" : ""),
      role: "radio", "aria-checked": String(o === chosen), onclick: () => { chosen = chosen === o ? null : o; drawOptions(); }}, o)));
    drawOptions();
    modal({title: "결정 변경 범위 확인", plain: true,
      body: h("div", {class: "stack"}, h("p", {class: "q-text"}, q.text), optionBox, extra,
        h("p", {class: "small muted"}, "출처: " + q.topic_label)),
      actions: [{label: "답변 저장", variant: "primary", onClick: async () => {
        const typed = extra.value.trim();
        if (!chosen && !typed) throw new Error("선택지를 고르거나 내용을 입력하세요.");
        const text = chosen && typed ? chosen + ". " + typed : (chosen || typed);
        await api("/api/answer", "POST", {root: q.root, topic: q.topic, text});
        setFlash("답변이 저장되었습니다.");
        await refreshAll();
      }}, {label: "취소"}]});
  }

  // ---------------------------------------------------------------- recent records

  function recentCard(st, activity) {
    return h("section", {class: "card recent-card", "aria-label": "최근 점검 기록"},
      h("div", {class: "recent-head"}, h("h2", {}, "최근 점검 기록"),
        h("button", {type: "button", class: "link", onclick: openLogModal}, "전체 보기")),
      activity.length === 0
        ? h("p", {class: "recent-lines muted"}, agentState(st) === "demo" ? "실제 점검 기록이 없습니다." : "아직 기록이 없습니다.")
        : h("ul", {class: "recent-lines"}, ...activity.map(a => h("li", {class: a.level === "error" ? "error" : null},
          h("span", {class: "recent-time"}, clock(a.time.replace(" ", "T"))), h("span", {}, a.text)))));
  }

  function timeline(items) {
    return h("div", {class: "timeline"}, ...items.map(a => h("div", {class: "tl-item"},
      h("span", {class: "tl-dot " + (a.level || "")}, icon(a.level === "error" ? "x" : (a.level === "attention" ? "alert" : "check"))),
      h("div", {class: "stack-sm", style: {gap: "2px"}}, h("span", {}, a.text), h("span", {class: "tl-time"}, relTime(a.time) + " · " + fmtTime(a.time))))));
  }

  // Full history. Technical details (files, check records, system log) sit behind "자세히 보기".
  async function openLogModal() {
    const panel = h("div", {class: "stack"}, skeleton(4));
    const close = modal({title: "점검 기록", wide: true, body: panel, actions: [{label: "닫기"}]});
    let data;
    try {
      const [activity, files, log, digest] = await Promise.all([api("/api/activity"), api("/api/files"), api("/api/log"), api("/api/digest")]);
      data = {activity, files, log, digest};
    } catch (e) { set(panel, errorState(e.message)); return; }
    let tab = "activity";
    const tabs = [["activity", "활동"], ["digest", "오늘 요약"], ["checks", "자세히 보기: 점검 기록"], ["files", "자세히 보기: 읽은 파일"], ["log", "자세히 보기: 시스템 로그"]];
    const bar = h("div", {class: "tabs", role: "tablist"});
    const body = h("div");
    const draw = () => {
      set(bar, ...tabs.map(([id, label]) => h("button", {type: "button", class: "tab", role: "tab", "aria-selected": String(tab === id),
        onclick: () => { tab = id; draw(); }}, label)));
      if (tab === "files") set(body, filesPanel(data.files));
      else if (tab === "checks") set(body, checksPanel(S.status));
      else if (tab === "digest") set(body, digestPanel(data.digest, async () => { data.digest = await api("/api/digest/now", "POST"); data.digest = await api("/api/digest"); draw(); }));
      else if (tab === "log") {
        set(body, h("div", {class: "stack-sm"}, h("div", {class: "row-between"},
          h("span", {class: "muted small"}, "decision-agent.log 최근 " + data.log.lines.length + "줄 (비밀 값은 기록하지 않습니다)"),
          h("div", {class: "btn-group"}, button("복사", {size: "sm", icon: "copy", onClick: () => copyText(data.log.lines.join("\n"))}),
            button("로그 폴더 열기", {size: "sm", icon: "external", onClick: () => openLocation("logs")}))),
        h("pre", {class: "log"}, data.log.lines.join("\n") || "로그가 없습니다.")));
      } else set(body, data.activity.length === 0 ? emptyState({icon: "activity", title: "아직 기록이 없습니다", neutral: true}) : timeline(data.activity));
    };
    draw();
    set(panel, bar, body);
    return close;
  }

  function digestPanel(d, makeNow) {
    const now = button("지금 요약 만들기", {icon: "refresh"});
    now.addEventListener("click", () => busy(now, makeNow));
    return h("div", {class: "stack-sm"}, h("div", {class: "row-between"},
      h("span", {class: "small muted"}, d.name ? d.name : "하루 요약 시각은 설정의 '알림 및 데이터'에서 정합니다."), h("div", {class: "btn-group"}, now,
        d.markdown ? button("복사", {size: "sm", icon: "copy", onClick: () => copyText(d.markdown)}) : null)),
    d.markdown ? markdownView(d.markdown) : emptyState({icon: "calendar", title: "아직 만든 요약이 없습니다", neutral: true}));
  }

  // ---------------------------------------------------------------- results

  // Figma 15:137: filter tabs, decisions on the left, the selected item on the right.
  // limit: how many result rows are drawn (whole decision groups, PAGE_ROWS more per "더 보기");
  // collapsed: decision groups the user folded, by root|topic.
  const RS = {q: "", label: "INCONSISTENT", folder: "all", owner: "all", status: "open", sort: "severity", selected: null, draft: {},
    limit: 0, collapsed: new Set()};
  const PAGE_ROWS = 50;
  const HIDDEN = {dismissed: "확인 완료", false_positive: "잘못된 탐지", snoozed: "내일 다시 알림"};
  const TAB_LABELS = [["INCONSISTENT", "반영 안 됨"], ["UNCERTAIN", "확인 필요"], ["CONSISTENT", "문제없음"]];

  function nowIso() {
    const d = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()) + "T" + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }

  function effectiveStatus(f) {
    if (f.status === "dismissed" || f.status === "false_positive") return f.status;
    if (f.status === "snoozed" && f.snooze_until && f.snooze_until > nowIso()) return "snoozed";
    return "open";
  }

  const keyOf = (f) => [f.root, f.topic, f.artifact_id].join("|");
  function shortDate(iso) { if (!iso) return ""; const d = toDate(iso); return isNaN(d) ? iso : (d.getMonth() + 1) + "월 " + d.getDate() + "일"; }
  function resultTitle(f) {
    const text = f.text.length > 30 ? f.text.slice(0, 29) + "…" : f.text;
    return text + ({INCONSISTENT: " 변경 미반영", UNCERTAIN: " 확인 필요", CONSISTENT: " 문제없음"}[f.label] || "");
  }

  ROUTES.results = {
    title: "점검 결과", auto: true,
    async load(params) {
      if (params.has("label")) RS.label = params.get("label") === "all" ? "INCONSISTENT" : params.get("label");
      if (params.has("q")) RS.q = params.get("q");
      if (params.has("open")) {
        RS.selected = params.get("open");
        history.replaceState(null, "", "#/results");
      }
      return {rows: await api("/api/findings?all=1"), state: S.status ? agentState(S.status) : ""};
    },
    render({rows, state}) {
      if (RS.selected) {
        const row = rows.find(r => keyOf(r) === RS.selected);
        if (row && row.label !== RS.label) RS.label = row.label;
      }
      const search = h("input", {class: "input", type: "search", placeholder: "결정 또는 파일 검색", value: RS.q, "aria-label": "결정 또는 파일 검색"});
      const tabs = h("div", {class: "result-tabs", role: "tablist", "aria-label": "결과 필터"});
      const listCard = h("section", {class: "card result-list", "aria-label": "결정별 목록"});
      const detail = h("section", {class: "card result-detail", "aria-label": "점검 결과 상세"});
      const folders = [...new Set(rows.map(r => r.root))];
      const owners = [...new Set(rows.map(r => r.owner).filter(Boolean))].sort();
      const more = h("details", {class: "result-more"}, h("summary", {}, icon("filter", "sm"), "추가 필터"),
        h("div", {class: "result-more-body"},
          selectControl([["open", "열린 항목"], ["dismissed", "확인 완료"], ["false_positive", "잘못된 탐지"], ["snoozed", "내일 다시 알림"], ["all", "모든 상태"]], RS.status, "상태", (v) => { RS.status = v; redraw(); }),
          folders.length > 1 ? selectControl([["all", "모든 폴더"], ...folders.map(f => [f, folderName(f)])], RS.folder, "폴더", (v) => { RS.folder = v; redraw(); }) : null,
          owners.length ? selectControl([["all", "모든 담당자"], ...owners.map(o => [o, o])], RS.owner, "담당자", (v) => { RS.owner = v; redraw(); }) : null,
          selectControl([["severity", "심각한 순"], ["recent", "최근 발견 순"], ["name", "결정 이름 순"]], RS.sort, "정렬", (v) => { RS.sort = v; redraw(); })));
      search.addEventListener("input", () => { RS.q = search.value; redraw(); });
      // A new filter starts again from the first page.
      function redraw() { RS.limit = PAGE_ROWS; listCard.scrollTop = 0; draw(); }
      if (!RS.limit) RS.limit = PAGE_ROWS;
      let shownRows = [];

      function visible() {
        const terms = RS.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
        return rows.filter(r =>
          (RS.status === "all" || effectiveStatus(r) === RS.status) &&
          (RS.folder === "all" || r.root === RS.folder) &&
          (RS.owner === "all" || r.owner === RS.owner) &&
          terms.every(t => [r.text, r.evidence, r.source_doc, r.owner, r.topic_label, r.decision_text, r.action].join(" ").toLowerCase().includes(t)));
      }

      function draw() {
        const base = visible();
        set(tabs, ...TAB_LABELS.map(([v, label]) => {
          const n = base.filter(r => r.label === v).length;
          return h("button", {type: "button", class: "result-tab", role: "tab", "aria-selected": String(RS.label === v), title: label + " " + n + "건",
            onclick: () => { RS.label = v; RS.selected = null; redraw(); }}, label, n ? h("span", {class: "result-tab-n"}, String(n)) : null);
        }));
        const shown = base.filter(r => r.label === RS.label);
        const groups = new Map();
        for (const r of shown) {
          const key = r.root + "|" + r.topic;
          if (!groups.has(key)) groups.set(key, {first: r, items: []});
          groups.get(key).items.push(r);
        }
        const list = [...groups.values()];
        const recent = (g) => g.items.map(i => i.last_seen || i.first_seen || "").sort().pop() || "";
        if (RS.sort === "recent") list.sort((a, b) => recent(b).localeCompare(recent(a)));
        else if (RS.sort === "name") list.sort((a, b) => a.first.decision_text.localeCompare(b.first.decision_text, "ko"));
        else list.sort((a, b) => b.items.length - a.items.length);
        if (!RS.selected || !shown.some(r => keyOf(r) === RS.selected)) RS.selected = shown.length ? keyOf(list[0].items[0]) : null;
        shownRows = shown;
        if (rows.length === 0) {
          set(listCard, emptyState({icon: "results", title: "아직 점검 결과가 없습니다",
            text: S.status && S.status.registered_folders ? "점검이 끝나면 결정별 결과가 여기에 표시됩니다." : "먼저 확인할 폴더를 추가하세요.", neutral: true}));
        } else if (shown.length === 0) {
          set(listCard, h("p", {class: "result-none"}, RS.q ? "검색어에 맞는 항목이 없습니다." :
            ({INCONSISTENT: "반영 안 된 항목이 없습니다.", UNCERTAIN: "확인이 필요한 항목이 없습니다.", CONSISTENT: "문제없음으로 확인된 항목이 없습니다."}[RS.label])));
        } else {
          // Make sure a row opened from elsewhere (home, search) lies on a drawn page.
          let upto = 0;
          for (const g of list) {
            upto += g.items.length;
            if (g.items.some(r => keyOf(r) === RS.selected)) { RS.limit = Math.max(RS.limit, upto); break; }
          }
          const page = [];
          let rowsDrawn = 0;
          for (const g of list) {
            if (page.length && rowsDrawn >= RS.limit) break;
            page.push(g);
            rowsDrawn += g.items.length;
          }
          const remaining = shown.length - rowsDrawn;
          set(listCard, ...page.map(groupNode),
            remaining > 0 ? h("button", {type: "button", class: "btn ghost result-more-rows", onclick: () => { RS.limit += PAGE_ROWS; draw(); }},
              "더 보기 (" + remaining + "건 남음)") : null,
            h("p", {class: "result-count small muted"}, "결정 " + list.length + "개, 항목 " + shown.length + "건 중 " + Math.min(rowsDrawn, shown.length) + "건 표시"));
        }
        drawDetail();
      }

      // One decision with its rows. The heading folds the group; folding keeps the rows out of the DOM.
      function groupNode(g) {
        const key = g.first.root + "|" + g.first.topic;
        const folded = RS.collapsed.has(key);
        const head = h("button", {type: "button", class: "result-group-head", "aria-expanded": String(!folded),
          onclick: () => { if (folded) RS.collapsed.delete(key); else RS.collapsed.add(key); draw(); }},
        icon(folded ? "chevron-right" : "chevron-down", "sm result-fold"),
        h("span", {class: "result-group-title"},
          h("span", {class: "result-decision"}, g.first.decision_text),
          h("span", {class: "result-decision-sub"}, [baseName(g.first.decision_source), shortDate(g.first.decision_date), g.items.length + "건"].filter(Boolean).join(" · "))));
        return h("div", {class: "result-group" + (folded ? " folded" : "")}, head, ...(folded ? [] : g.items.map(rowNode)));
      }

      function rowNode(r) {
        const key = keyOf(r);
        return h("button", {type: "button", class: "result-row" + (key === RS.selected ? " selected" : ""), "data-key": key,
          "aria-pressed": String(key === RS.selected), onclick: () => select(key)},
        icon(r.label === "CONSISTENT" ? "check" : "alert-circle", "result-icon " + labelTone(r.label)),
        h("span", {class: "result-row-main"}, h("span", {class: "result-row-title truncate", title: r.text}, r.text),
          h("span", {class: "result-row-sub truncate"}, baseName(r.source_doc) + (effectiveStatus(r) !== "open" ? " · " + HIDDEN[effectiveStatus(r)] : ""),
            isLocal(r) ? localBadge() : null)),
        h("span", {class: "result-row-label " + labelTone(r.label)}, DA.LABELS[r.label]));
      }

      // Selecting a row only moves the highlight and redraws the detail panel, not the list.
      function select(key) {
        RS.selected = key;
        for (const node of listCard.querySelectorAll(".result-row")) {
          const on = node.dataset.key === key;
          node.classList.toggle("selected", on);
          node.setAttribute("aria-pressed", String(on));
        }
        drawDetail();
      }

      function drawDetail() {
        const selected = shownRows.find(r => keyOf(r) === RS.selected);
        set(detail, ...(selected ? detailPanel(selected, rows) : [h("p", {class: "result-none"}, "왼쪽에서 항목을 고르면 근거와 수정 문장을 볼 수 있습니다.")]));
      }
      draw();
      const demo = state === "demo";
      return h("div", {class: "page results-page"},
        h("div", {class: "page-head"}, h("h1", {}, "점검 결과"),
          h("div", {class: "search", role: "search"}, icon("search"), search)),
        h("div", {class: "result-toolbar"}, tabs, demo ? h("span", {class: "demo-note"}, icon("alert-circle", "sm"), "시연용 판정기 결과입니다. 실제 검사가 아닙니다.") : null, more),
        h("div", {class: "result-columns"}, listCard, detail));
    },
  };

  function labelTone(label) { return {INCONSISTENT: "bad", UNCERTAIN: "warn", CONSISTENT: "ok"}[label] || ""; }

  function selectControl(options, value, label, onChange) {
    const node = select(options, value, {"aria-label": label});
    node.addEventListener("change", () => onChange(node.value));
    return h("label", {class: "mini-field"}, h("span", {}, label), node);
  }

  // The right-hand panel (Figma 15:216, 16:850, 19:1320, 19:1392).
  // Lines the local pre-filter answered without a model call. It only ever answers CONSISTENT.
  function isLocal(f) { return f.label === "CONSISTENT" && String(f.evidence || "").includes("AI 확인 없이"); }
  function localBadge() {
    return h("span", {class: "badge neutral local-badge", title: "새 결정의 값이 이미 들어 있고 이전 값은 없어 AI 확인 없이 일치로 분류했습니다."}, "로컬 확인");
  }

  function detailPanel(f, rows) {
    const status = effectiveStatus(f);
    const related = f.date ? f : rows.find(r => r.root === f.root && r.topic === f.topic && r.date);
    const left = [
      f.decision_source ? baseName(f.decision_source) + (f.decision_date ? " · " + shortDate(f.decision_date) + " 결정" : "") : null,
      baseName(f.source_doc) + (f.location ? " · " + (/^\d+$/.test(f.location) ? f.location + "행" : f.location) : ""),
    ].filter(Boolean);
    const draftBox = h("div", {class: "draft-slot"});
    const drawDraft = (d) => {
      if (!d) {
        const b = button("수정 문장 제안", {variant: "primary"});
        b.addEventListener("click", () => busy(b, async () => {
          RS.draft[keyOf(f)] = await api("/api/finding/draft", "POST", {root: f.root, topic: f.topic, artifact_id: f.artifact_id});
          drawDraft(RS.draft[keyOf(f)]);
        }));
        set(draftBox, f.label === "CONSISTENT" ? null : b);
        return;
      }
      const copyMain = button(d.revised_text ? "수정 문장 복사" : "메시지 복사", {variant: "primary"});
      copyMain.addEventListener("click", async () => {
        await copyText(d.revised_text || d.message, d.revised_text ? "수정 문장을 복사했습니다." : "메시지를 복사했습니다.");
        set(copyMain, icon("check", "sm"), "복사 완료");
      });
      const copyMsg = h("button", {type: "button", class: "link strong draft-msg-copy"}, "메시지 복사");
      copyMsg.addEventListener("click", async () => { await copyText(d.message, "메시지를 복사했습니다."); copyMsg.textContent = "복사 완료"; });
      set(draftBox, h("div", {class: "draft-box"},
        d.revised_text ? h("p", {class: "draft-text"}, d.revised_text)
          : h("p", {class: "draft-note"}, d.model_used ? (d.note || "제안할 수정 문장을 만들지 못했습니다.") : "시연용 판정기에서는 수정 문장을 만들지 않습니다. 담당자에게 보낼 메시지만 만듭니다."),
        copyMain,
        h("p", {class: "draft-message"}, d.message),
        d.revised_text ? copyMsg : null));
    };
    drawDraft(RS.draft[keyOf(f)]);
    const setStatus = async (next, extra) => {
      await api("/api/finding/status", "POST", Object.assign({root: f.root, topic: f.topic, artifact_id: f.artifact_id, status: next, snooze_hours: next === "snoozed" ? 24 : 0}, extra || {}));
      const short = f.text.length > 20 ? f.text.slice(0, 19) + "…" : f.text;
      if (next === "dismissed") { toast("확인 완료로 표시했습니다.", "success"); setFlash(short + " 항목을 확인 완료로 표시했습니다."); }
      else if (next === "snoozed") toast("내일 다시 알림이 설정되었습니다.", "success");
      else if (next === "false_positive") toast("잘못된 탐지로 표시했습니다. 다시 점검해도 숨겨집니다.", "success");
      else toast("다시 열었습니다.", "success");
      await refreshAll();
    };
    const link = (label, fn) => h("button", {type: "button", class: "link detail-link", onclick: (e) => busy(e.currentTarget, fn)}, label);
    const actions = status === "open"
      ? [link("확인 완료", () => setStatus("dismissed")), link("잘못된 탐지", () => reportFalse(f, setStatus)), link("내일 다시 알림", () => setStatus("snoozed"))]
      : [h("span", {class: "small muted"}, HIDDEN[status] + "로 표시됨" + (status === "snoozed" && f.snooze_until ? " (" + fmtTime(f.snooze_until) + "까지)" : "")),
        link("다시 열기", () => setStatus("open"))];
    const confidence = typeof f.confidence === "number" && f.label !== "UNCERTAIN" ? Math.round(f.confidence * 100) + "%" : "-";
    return [
      h("div", {class: "detail-badges"},
        h("span", {class: "badge state " + {INCONSISTENT: "bad", UNCERTAIN: "attention", CONSISTENT: "ok"}[f.label]},
          icon(f.label === "CONSISTENT" ? "check" : "alert-circle"), DA.LABELS[f.label]),
        isLocal(f) ? localBadge() : null),
      h("h2", {class: "detail-title"}, resultTitle(f)),
      h("p", {class: "detail-caption"}, "변경된 결정"),
      h("p", {class: "detail-decision"}, f.decision_text),
      f.evidence ? h("p", {class: "detail-explain"}, f.evidence) : null,
      h("div", {class: "detail-evidence"},
        h("div", {class: "detail-sources"}, ...left.map(t => h("span", {}, t)), f.action ? h("span", {class: "detail-action"}, "권장 조치: " + f.action) : null),
        h("div", {class: "detail-people"},
          h("span", {}, "담당자  " + (f.owner || "지정되지 않음")),
          related ? h("span", {}, "관련 일정  " + shortDate(related.date) + " " + related.text) : null)),
      draftBox,
      h("div", {class: "detail-links"}, ...actions),
      h("details", {class: "detail-tech"}, h("summary", {}, "자세히 보기"),
        h("dl", {class: "kv"},
          h("dt", {}, "종류"), h("dd", {}, KINDS[f.kind] || f.kind),
          h("dt", {}, "표본 일치율"), h("dd", {title: "같은 판단을 여러 번 했을 때 같은 답이 나온 비율입니다. 보정된 확률이 아닙니다."}, confidence),
          h("dt", {}, "처음 발견"), h("dd", {}, fmtTime(f.first_seen)),
          h("dt", {}, "마지막 확인"), h("dd", {}, fmtTime(f.last_seen)),
          h("dt", {}, "폴더"), h("dd", {class: "mono", title: f.root}, folderName(f.root)))),
      h("p", {class: "detail-foot"}, "AI가 원본 파일을 직접 수정하지 않습니다.", h("br"), "수정 문장만 제안합니다."),
    ];
  }

  // Figma 19:1392: an optional reason, then the item stays hidden on later checks.
  function reportFalse(f, setStatus) {
    const reason = input({placeholder: "사유 입력 (선택)", maxlength: "300", "aria-label": "사유"});
    modal({title: "잘못된 탐지", plain: true, body: h("div", {class: "stack"}, h("p", {}, "해당 항목을 잘못된 탐지로 표시합니다."), reason),
      actions: [{label: "제출", variant: "primary", onClick: () => setStatus("false_positive", {reason: reason.value.trim()})}, {label: "취소"}]});
  }

  // Older entry points (search results, calendar) still call this.
  function openFinding(f) { go("results", {open: keyOf(f)}); }

  // ---------------------------------------------------------------- folders

  // Figma 15:233 and its variants: one card per registered folder with the subfolder tree.
  const FS = {trees: {}, expanded: {}, saving: {}};
  const KIND_NAMES = {MEETING: "회의록", REQUIREMENT: "요구사항", TASK: "작업", TEST: "테스트", SCHEDULE: "일정"};

  ROUTES.folders = {
    title: "폴더 관리", auto: false,
    async load() {
      const cards = await api("/api/folders");
      const trees = await Promise.all(cards.map(c => c.exists
        ? api("/api/folders/tree?root=" + encodeURIComponent(c.root)).catch(() => null) : Promise.resolve(null)));
      cards.forEach((c, i) => { FS.trees[c.root] = trees[i]; });
      return {cards, busy: S.status ? S.status.busy : false};
    },
    render({cards, busy}) {
      const actions = [
        button("파일 가져오기", {onClick: openImport}),
        button("폴더 추가", {variant: cards.length ? "primary" : "", onClick: () => startAddFolder()}),
      ];
      const manual = h("details", {class: "folder-manual"}, h("summary", {}, "고급: 경로 직접 입력"),
        h("div", {class: "folder-manual-body"}, h("p", {class: "small muted"}, "폴더 선택 창을 쓸 수 없을 때만 사용하세요."),
          button("경로 입력", {size: "sm", onClick: () => manualPathDialog()})));
      if (cards.length === 0) {
        return page("폴더 관리", null, actions, h("section", {class: "card folder-empty dashed"},
          icon("folder", "folder-empty-icon"), h("strong", {}, "등록된 폴더가 없습니다."),
          h("p", {}, "문서가 저장된 폴더를 선택하십시오. 하위 폴더는 자동으로 포함됩니다."),
          button("폴더 선택", {variant: "primary", onClick: () => startAddFolder()})), manual);
      }
      return page("폴더 관리", null, actions, ...cards.map(c => folderCard(c, busy)), manual);
    },
  };

  function countIncluded(node, excluded) {
    if (node.rel && excluded.has(node.rel)) return 0;
    return node.readable + node.children.reduce((sum, c) => sum + countIncluded(c, excluded), 0);
  }

  function kindText(kinds) {
    return Object.entries(kinds || {}).filter(([k]) => KIND_NAMES[k])
      .sort((a, b) => b[1] - a[1]).slice(0, 3).map(([k, n]) => KIND_NAMES[k] + " " + n).join(" · ");
  }

  function folderCard(c, busy) {
    if (!c.exists) {
      return h("section", {class: "card folder-error"}, icon("alert-circle", "folder-error-icon"),
        h("strong", {}, "등록된 폴더를 찾을 수 없습니다."),
        h("p", {}, "폴더가 이동되었거나 접근 권한이 변경되었는지 확인하십시오."),
        h("p", {class: "small muted", title: c.root}, folderName(c.root)),
        h("div", {class: "row"}, button("폴더 다시 선택", {variant: "primary", onClick: () => startAddFolder(async () => {
          await api("/api/folders/remove", "POST", {path: c.root});
        })}), button("목록에서 제거", {onClick: () => stopFolder(c)})));
    }
    const data = FS.trees[c.root];
    const excluded = new Set(data ? data.excluded : c.excluded);
    const total = data ? data.tree.total_readable : c.files_ok;
    const included = data ? countIncluded(data.tree, excluded) : c.files_ok;
    const STATE = {watching: busy ? ["busy", "loader", "확인 중"] : ["ok", "check", "정상"], paused: ["paused", "pause-bars", "일시 중지"]};
    const [cls, iconName, text] = STATE[c.watch] || STATE.watching;
    const badgeNode = h("span", {class: "badge state " + cls}, icon(iconName), text);
    if (iconName === "loader") badgeNode.querySelector("svg").classList.add("spin");
    const menuWrap = h("div", {class: "anchor"});
    const menuBtn = h("button", {type: "button", class: "icon-btn", "aria-label": "폴더 메뉴", "aria-haspopup": "menu",
      onclick: () => folderMenu(menuWrap, c)}, icon("more"));
    menuWrap.appendChild(menuBtn);
    const tree = h("div", {class: "folder-tree", role: "tree", "aria-label": "하위 폴더"});
    const summary = h("p", {class: "folder-summary"});
    const drawSummary = () => {
      const inc = data ? countIncluded(data.tree, excluded) : included;
      summary.textContent = "문서 " + total + "개 · 포함 " + inc + "개 · 제외 " + (total - inc) + "개";
    };
    drawSummary();
    if (data) {
      if (!FS.expanded[c.root]) FS.expanded[c.root] = new Set([""]);
      const expanded = FS.expanded[c.root];
      const save = async () => {
        try {
          await api("/api/folders/excluded", "POST", {root: c.root, excluded: [...excluded]});
          toast("포함할 폴더를 저장했습니다. 다음 점검부터 적용됩니다.", "success");
        } catch (e) { toast(e.message, "danger"); }
      };
      const ancestorOff = (rel) => [...excluded].some(e => rel !== e && rel.startsWith(e + "/"));
      const rows = [];
      const walk = (node, depth) => {
        const isRoot = node.rel === "";
        const off = !isRoot && (excluded.has(node.rel) || ancestorOff(node.rel));
        const hasKids = node.children.length > 0;
        const open = expanded.has(node.rel);
        const box = h("input", {type: "checkbox", class: "check-box", checked: !off, disabled: isRoot || ancestorOff(node.rel),
          "aria-label": node.name + " 포함"});
        if (!isRoot) box.addEventListener("change", () => {
          for (const e of [...excluded]) if (e.startsWith(node.rel + "/")) excluded.delete(e);
          if (box.checked) excluded.delete(node.rel); else excluded.add(node.rel);
          drawTree(); drawSummary(); save();
        });
        const kinds = kindText(node.kinds);
        rows.push(h("div", {class: "tree-line" + (off ? " off" : ""), role: "treeitem", style: {paddingLeft: (12 + depth * 24) + "px"},
          "aria-expanded": hasKids ? String(open) : null},
        box,
        hasKids && !isRoot ? h("button", {type: "button", class: "tree-caret", "aria-label": (open ? "접기: " : "펼치기: ") + node.name,
          onclick: () => { open ? expanded.delete(node.rel) : expanded.add(node.rel); drawTree(); }}, icon(open ? "chevron-down" : "chevron-right")) : null,
        icon("folder", "tree-folder"),
        h("span", {class: "tree-name", title: node.rel || c.root}, isRoot ? folderName(c.root) : node.name),
        h("span", {class: "tree-count"}, "문서 " + node.total_readable + "개"),
        kinds ? h("span", {class: "tree-kinds"}, kinds) : null,
        off ? h("span", {class: "tree-off"}, "제외") : null));
        if (hasKids && (open || isRoot)) node.children.forEach(child => walk(child, depth + 1));
      };
      const drawTree = () => { rows.length = 0; walk(data.tree, 0); set(tree, ...rows); };
      drawTree();
    }
    return h("section", {class: "card folder-card"},
      h("div", {class: "folder-head"}, icon("folder", "folder-big"),
        h("div", {class: "folder-title"}, h("h2", {title: c.root}, folderName(c.root)), summary),
        badgeNode, menuWrap),
      c.is_target ? h("p", {class: "folder-target"}, icon("link", "sm"), "메일, 캘린더, 메모, 가져온 파일을 이 폴더와 함께 분석합니다.") : null,
      data ? tree : h("p", {class: "small muted"}, "하위 폴더를 불러오지 못했습니다."),
      data && data.truncated ? h("p", {class: "small muted"}, "폴더가 많아 일부만 표시했습니다.") : null,
      h("p", {class: "folder-note"}, "포함 여부를 변경하면 다음 점검부터 적용됩니다."),
      h("p", {class: "folder-note"}, "다른 위치의 폴더도 추가할 수 있습니다. 폴더를 이 창에 끌어다 놓아도 됩니다."));
  }

  function folderMenu(wrap, c) {
    if (openPopover && wrap.contains(openPopover)) { closePopover(); return; }
    closePopover();
    const item = (label, fn, danger) => h("button", {type: "button", class: "menu-item" + (danger ? " danger" : ""), role: "menuitem",
      onclick: () => { closePopover(); fn(); }}, label);
    const pop = h("div", {class: "popover", role: "menu", style: {width: "220px"}}, h("div", {class: "popover-section"},
      item("탐색기에서 열기", () => openLocation(c.root)),
      c.is_target ? null : item("연결 자료를 이 폴더와 분석", async () => {
        await api("/api/settings", "POST", {connector_target: c.root}); toast("연결 자료를 이 폴더와 함께 분석합니다.", "success"); renderRoute(true);
      }),
      item("폴더 확인 중지", () => stopFolder(c), true)));
    wrap.appendChild(pop);
    openPopover = pop;
  }

  // Figma 16:1612: removing a folder only stops checking it; the files stay untouched.
  function stopFolder(c) {
    modal({title: "폴더 확인을 중지하시겠습니까?", plain: true,
      body: h("p", {class: "muted"}, "원본 파일은 그대로 두고, 확인만 중지합니다."),
      actions: [{label: "확인 중지", variant: "danger-outline", onClick: async () => {
        await api("/api/folders/remove", "POST", {path: c.root});
        toast("폴더 확인을 중지했습니다. 원본 파일은 그대로입니다.", "success");
        await refreshAll();
        renderRoute(true);
      }}, {label: "취소"}]});
  }

  function stat(value, label) {
    return h("div", {class: "stat"}, h("span", {class: "stat-value"}, String(value)), h("span", {class: "stat-label"}, label));
  }

  async function startAddFolder(onAdded) {
    let res;
    try {
      res = await api("/api/folders/pick", "POST", {});
    } catch (e) {
      if (e.status === 400) { manualPathDialog(onAdded); return; }
      toast(e.message, "danger");
      return;
    }
    if (res.path) openPreview(res.path, onAdded);
  }

  function manualPathDialog(onAdded) {
    const path = input({placeholder: "예: C:\\Users\\me\\Documents\\project", class: "input mono", autofocus: true});
    modal({title: "폴더 경로 입력", plain: true,
      body: [field("폴더 경로", path, "선택한 폴더와 그 안의 모든 폴더를 포함합니다.")],
      actions: [{label: "확인", variant: "primary", onClick: () => {
        if (!path.value.trim()) throw new Error("경로를 입력하세요.");
        openPreview(path.value.trim(), onAdded);
      }}, {label: "취소"}]});
  }

  // Details for the onboarding preview and the "자세히 보기" part of the folder dialog.
  function previewBody(p) {
    if (!p.ok) {
      return [callout("danger", "x-circle", "이 폴더는 추가할 수 없습니다", p.reason),
        h("p", {class: "small muted"}, "드라이브 루트, 사용자 폴더 전체, 시스템 폴더, 이 앱의 데이터 폴더는 개인정보 보호를 위해 등록할 수 없습니다. 프로젝트 폴더를 골라 주세요.")];
    }
    const rel = p.relation;
    const unsupported = Object.values(p.unsupported).reduce((a, b) => a + b, 0);
    const skipped = Object.values(p.skipped).reduce((a, b) => a + b, 0);
    const out = [h("div", {class: "pick-folder"}, icon("folder", "pick-folder-icon"), h("strong", {title: p.path}, folderName(p.path)))];
    if (rel.kind === "contained") out.push(callout("danger", "x-circle", "이미 포함되어 있습니다", "등록된 폴더 " + folderName(rel.folders[0]) + " 안에 있어 이미 읽고 있습니다."));
    else if (rel.kind === "duplicate") out.push(callout("info", "info", "이미 등록된 폴더입니다", null));
    else if (rel.kind === "parent") out.push(callout("info", "folder", "이미 등록된 폴더를 포함하는 상위 폴더입니다",
      h("div", {class: "stack-sm"}, h("p", {}, "합치면 아래 폴더가 이 폴더 하나로 바뀝니다. 기존에 제외한 하위 폴더 설정은 유지됩니다."),
        h("ul", {}, ...rel.folders.map(f => h("li", {title: f}, folderName(f)))))));
    out.push(h("p", {class: "muted"}, "선택한 폴더의 하위 폴더를 포함합니다.", h("br"), "포함 여부는 폴더 관리에서 변경할 수 있습니다."));
    out.push(h("p", {class: "small muted"}, "읽을 수 있는 문서 " + p.readable + "개 · 하위 폴더 " + p.subfolders + "개" + (unsupported ? " · 읽지 않는 형식 " + unsupported + "개" : "")));
    for (const w of p.warnings) out.push(callout("warning", "alert", null, w));
    const formats = Object.entries(p.formats);
    const more = h("details", {class: "detail-tech"}, h("summary", {}, "자세히 보기"), h("div", {class: "stack-sm", style: {marginTop: "8px"}},
      h("div", {class: "stats"}, stat(p.readable, "읽을 수 있는 파일"), stat(p.subfolders, "하위 폴더"), stat(unsupported, "지원하지 않는 파일"), stat(skipped, "건너뛰는 파일")),
      formats.length ? h("div", {class: "row"}, ...formats.map(([ext, n]) => h("span", {class: "chip"}, "." + ext + " " + n))) : null,
      unsupported ? h("p", {class: "small muted"}, "hwp, doc, xls, ppt는 hwpx, docx, xlsx, pptx로 저장하면 읽을 수 있습니다.") : null,
      p.excluded_dirs.length ? h("div", {class: "row"}, ...p.excluded_dirs.slice(0, 12).map(d => h("span", {class: "chip mono"}, d))) : null,
      skipped ? h("ul", {class: "small muted"}, ...Object.entries(p.skipped).map(([why, n]) => h("li", {}, why + ": " + n + "개"))) : null));
    out.push(more);
    return out;
  }

  async function addFolder(path, merge) {
    try {
      await api("/api/folders/add", "POST", {path, merge});
    } catch (e) {
      if (e.status === 409 && e.detail && e.detail.code === "merge") {
        const ok = await confirmDialog({title: "폴더를 합칠까요?", message: e.message, confirmLabel: "합쳐서 추가"});
        if (!ok) return false;
        await api("/api/folders/add", "POST", {path, merge: true});
      } else { throw e; }
    }
    toast("폴더를 추가했습니다. 점검을 시작합니다.", "success");
    return true;
  }

  // Figma 19:1023 (폴더 선택) and 19:1472 (선택할 수 없는 위치).
  async function openPreview(path, onAdded) {
    let p;
    try { p = await api("/api/folders/preview", "POST", {path}); } catch (e) { toast(e.message, "danger"); return; }
    if (!p.ok) {
      modal({title: "선택할 수 없는 위치", plain: true, icon: "alert-circle",
        body: h("div", {class: "stack-sm"}, h("p", {class: "muted"}, "드라이브 전체와 시스템 폴더는 등록할 수 없습니다.", h("br"), "프로젝트 문서가 있는 폴더를 선택하십시오."),
          h("p", {class: "small muted"}, p.reason)),
        actions: [{label: "다른 폴더 선택", variant: "primary", onClick: () => { setTimeout(() => startAddFolder(onAdded), 0); }}, {label: "취소"}]});
      return;
    }
    const blocked = p.relation.kind === "contained" || p.relation.kind === "duplicate";
    modal({title: "폴더 선택", plain: true, body: h("div", {class: "stack"}, ...previewBody(p)),
      actions: [{label: p.relation.kind === "parent" ? "합쳐서 선택" : "이 폴더 선택", variant: "primary", disabled: blocked,
        onClick: async () => {
          if (onAdded) await onAdded(p.path);
          const added = await addFolder(p.path, p.relation.kind === "parent");
          if (!added) return false;
          await refreshAll();
          if (parseHash().name === "folders") renderRoute(true);
          return true;
        }}, {label: "취소"}]});
  }

  // File import and notes live here now: a one-off file, or a decision typed in quick check.
  async function openImport() {
    const file = h("input", {type: "file", class: "input", "aria-label": "가져올 파일"});
    const notesBox = h("div", {class: "stack-sm"});
    const drawNotes = async () => {
      let notes = [];
      try { notes = await api("/api/notes"); } catch (e) { set(notesBox, h("p", {class: "small muted"}, e.message)); return; }
      set(notesBox, notes.length === 0 ? h("p", {class: "small muted"}, "빠른 점검 창에서 저장한 메모가 여기에 표시됩니다.")
        : h("div", {class: "list"}, ...notes.map(n => h("div", {class: "list-item"}, h("span", {class: "chip"}, n.date),
          h("div", {class: "list-main"}, n.text),
          button("", {variant: "ghost", size: "sm", icon: "trash", title: "메모 삭제", onClick: async () => {
            await api("/api/notes/" + encodeURIComponent(n.id), "DELETE"); drawNotes();
          }})))));
    };
    drawNotes();
    modal({title: "파일 가져오기", plain: true, wide: true,
      body: h("div", {class: "stack"},
        h("p", {class: "muted"}, "확인 폴더 밖의 회의록이나 일정 파일(.ics 포함)을 한 번 읽어 점검에 포함합니다. 원본은 수정하지 않고 파일 자체도 저장하지 않으며, 읽은 텍스트만 앱 데이터에 남습니다."),
        file,
        h("details", {class: "detail-tech"}, h("summary", {}, "저장한 메모"), notesBox)),
      actions: [{label: "가져오기", variant: "primary", onClick: async () => {
        if (!file.files[0]) throw new Error("파일을 선택하세요.");
        await importFile(file.files[0]);
      }}, {label: "닫기"}]});
  }

  // drag and drop of a folder onto the window

  let dragDepth = 0;
  const hasFiles = (e) => e.dataTransfer && Array.from(e.dataTransfer.types || []).includes("Files");
  document.addEventListener("dragenter", (e) => {
    if (!hasFiles(e)) return;
    dragDepth++;
    document.body.classList.add("dragging");
  });
  document.addEventListener("dragleave", () => {
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) document.body.classList.remove("dragging");
  });
  document.addEventListener("dragover", (e) => { if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener("drop", (e) => {
    if (!hasFiles(e)) return;
    e.preventDefault();
    dragDepth = 0;
    document.body.classList.remove("dragging");
    const dropped = e.dataTransfer.files[0];
    if (dropped && dropped.size > 0 && /\.[a-z0-9]{2,5}$/i.test(dropped.name) && dropped.type !== "") {
      importFile(dropped);
      return;
    }
    handleFolderDrop();
  });

  async function handleFolderDrop() {
    for (let i = 0; i < 10; i++) {
      await new Promise(r => setTimeout(r, 150));
      let r;
      try { r = await api("/api/folders/dropped"); } catch (e) { break; }
      if (r.path) { openPreview(r.path); return; }
    }
    toast(S.status && S.status.ui_mode === "window"
      ? "끌어다 놓은 항목에서 폴더를 찾지 못했습니다. 폴더를 끌어다 놓거나 '폴더 추가'를 누르세요."
      : "브라우저 화면에서는 끌어다 놓은 폴더의 위치를 알 수 없습니다. '폴더 추가'를 눌러 선택하세요.", "warning");
  }

  function buildDropVeil() {
    set(document.getElementById("drop-card"), h("div", {class: "folder-icon"}, icon("folder-plus", "lg")),
      h("strong", {}, "여기에 놓으면 폴더를 추가합니다"), h("span", {class: "small muted"}, "추가하기 전에 읽을 파일을 미리 보여 드립니다."));
  }

  // ---------------------------------------------------------------- accounts

  const SERVICE_LOOK = {
    google_calendar: ["calendar", "#1a73e8"], gmail: ["mail", "#c5221f"], outlook_calendar: ["calendar", "#0f6cbd"],
    outlook_mail: ["mail", "#0f6cbd"], imap_mail: ["inbox", "#475467"], notion: ["book", "#2f3437"],
    jira: ["kanban", "#0052cc"], slack: ["hash", "#611f69"],
  };
  const CREDENTIAL_OPTIONS = new Set(["client_id", "tenant", "host", "port", "username", "base_url", "email"]);
  const NEVER = ["메일 보내기, 삭제, 읽음 표시 바꾸기", "일정, 이슈, 페이지 만들기나 고치기", "메시지 보내기", "비밀 값을 설정 파일이나 로그에 쓰기"];

  function connState(c) {
    if (c.enabled && c.status && !c.status.ok) return ["danger", "x-circle", "오류"];
    if (c.enabled && c.problems.length === 0) return ["success", "check-circle", "연결됨"];
    if (!c.enabled && c.problems.length === 0) return ["neutral", "pause", "설정됨, 자동 가져오기 꺼짐"];
    return ["neutral", "minus", "연결 안 됨"];
  }

  function optionControl(spec, value) {
    if (spec.kind === "list") {
      const t = textarea({rows: "3"});
      t.value = (value || []).join("\n");
      return [t, () => t.value];
    }
    if (spec.kind === "number") {
      const i = input({type: "number", min: "0", value: String(value === undefined || value === "" ? spec.default : value)});
      return [i, () => Number(i.value)];
    }
    const i = input({value: String(value || "")});
    return [i, () => i.value.trim()];
  }

  function connectWizard(c, startAt) {
    const steps = ["준비", "인증 정보"].concat(c.uses_oauth ? ["로그인"] : [], ["범위 선택", "테스트"]);
    let step = 0;
    const readers = {};
    const credFields = [];
    const scopeFields = [];
    for (const spec of c.options) {
      const [control, read] = optionControl(spec, c.values[spec.name]);
      readers[spec.name] = read;
      const f = field(spec.label, control, spec.help, {required: spec.required});
      (CREDENTIAL_OPTIONS.has(spec.name) ? credFields : scopeFields).push(f);
    }
    const secretInputs = c.secrets.map(sec => {
      const saved = c.has_secrets[sec.name];
      const i = input({type: "password", autocomplete: "off", placeholder: saved ? "저장됨 - 바꾸려면 새 값 입력" : sec.label});
      return {sec, input: i, saved, node: field(sec.label, i, (sec.help ? sec.help + " " : "") + "값은 Windows 자격 증명 관리자에만 저장되며 다시 표시되지 않습니다.", {required: sec.required})};
    });
    const periodic = toggle("주기적으로 가져오기", c.enabled || !c.problems.length, "켜 두면 일정 간격으로 새 내용을 읽습니다.");
    const collect = () => Object.fromEntries(Object.entries(readers).map(([k, r]) => [k, r()]));
    const saveConfig = (enabled) => api("/api/connectors/config", "POST", {id: c.id, enabled, options: collect()});
    let connected = Boolean(c.connected);
    const body = h("div", {class: "stack"});
    const stepper = h("div", {class: "stepper", "aria-label": "진행 단계"});
    const close = modal({title: c.label + " 연결", wide: true, plain: true, body: [stepper, body], actions: [], onClose: () => renderRoute(false)});
    if (startAt && steps.includes(startAt)) step = steps.indexOf(startAt);

    async function current() {
      return (await api("/api/connectors")).find(r => r.id === c.id);
    }

    function drawStepper() {
      set(stepper, ...steps.flatMap((name, i) => [
        i ? h("span", {class: "step-sep", "aria-hidden": "true"}) : null,
        h("span", {class: "step" + (i === step ? " active" : "") + (i < step ? " done" : ""), "aria-current": i === step ? "step" : null},
          h("span", {class: "step-num"}, i < step ? icon("check", "sm") : String(i + 1)), name)]));
    }

    function nav(nextLabel, onNext, extra) {
      const list = [];
      if (step > 0) list.push({label: "이전", closes: false, onClick: () => { step--; draw(); return false; }});
      if (extra) list.push(extra);
      list.push({label: nextLabel, variant: "primary", closes: false, onClick: async () => {
        const ok = await onNext();
        if (ok === "done") { close(); renderRoute(true); return false; }
        if (ok !== false) { step++; draw(); }
        return false;
      }});
      close.setActions(list);
    }

    function draw() {
      drawStepper();
      const name = steps[step];
      if (name === "준비") {
        set(body, h("div", {class: "svc-top"}, serviceLogo(c.id), h("div", {class: "stack-sm"}, h("h3", {}, c.label), h("p", {class: "muted"}, c.description))),
          callout("success", "shield-check", "읽기 전용 연결", c.reads),
          h("div", {class: "stack-sm"}, h("h4", {}, "준비 방법"), h("ol", {}, ...c.guide.map(g => h("li", {}, g)))));
        nav("다음", () => true);
      } else if (name === "인증 정보") {
        set(body, h("p", {class: "muted"}, "서비스에서 받은 값을 입력하세요. 이 프로그램에는 어떤 클라이언트 ID나 비밀 값도 들어 있지 않습니다."),
          ...credFields, ...secretInputs.map(s => s.node),
          credFields.length + secretInputs.length === 0 ? callout("neutral", "info", null, "입력할 인증 정보가 없습니다.") : null);
        nav("다음", async () => {
          for (const s of secretInputs) {
            if (!s.input.value && s.sec.required && !s.saved) throw new Error("'" + s.sec.label + "'을(를) 입력하세요.");
          }
          for (const s of secretInputs) {
            if (s.input.value) {
              await api("/api/connectors/secret", "POST", {id: c.id, name: s.sec.name, value: s.input.value});
              s.input.value = "";
              s.saved = true;
              s.input.placeholder = "저장됨 - 바꾸려면 새 값 입력";
            }
          }
          await saveConfig(c.enabled);
          return true;
        });
      } else if (name === "로그인") {
        const state = h("div", {});
        const drawState = () => set(state, connected
          ? callout("success", "check-circle", "계정이 연결되었습니다", "다음 단계에서 읽을 범위를 고르세요.")
          : callout("neutral", "info", "아직 로그인하지 않았습니다", "아래 버튼을 누르면 기본 브라우저에서 서비스 로그인 페이지가 열립니다. 로그인을 마치면 이 화면이 자동으로 바뀝니다."));
        drawState();
        const login = button(connected ? "계정 다시 연결" : "브라우저에서 로그인", {variant: "primary", icon: "external"});
        login.addEventListener("click", () => busy(login, async () => {
          await saveConfig(c.enabled);
          const r = await api("/api/connectors/oauth/start", "POST", {id: c.id, open_browser: S.status && S.status.ui_mode === "window"});
          if (!r.opened) window.open(r.url, "_blank", "noopener");
          for (let i = 0; i < 90 && !connected; i++) {
            await new Promise(res => setTimeout(res, 2000));
            const row = await current();
            if (row && row.connected) { connected = true; }
          }
          drawState();
          if (!connected) throw new Error("로그인을 확인하지 못했습니다. 브라우저에서 로그인을 마쳤는지 확인하고 다시 시도하세요.");
        }));
        set(body, state, h("div", {}, login),
          h("p", {class: "small muted"}, "로그인은 서비스의 공식 페이지에서 이루어지며, 이 앱은 비밀번호를 보지 않습니다. 받은 토큰은 자격 증명 관리자에만 저장됩니다."));
        nav("다음", () => {
          if (!connected) throw new Error("먼저 로그인을 마치세요.");
          return true;
        });
      } else if (name === "범위 선택") {
        set(body, h("p", {class: "muted"}, "지정한 범위만 읽습니다. 범위를 좁힐수록 개인 정보가 덜 들어옵니다."),
          ...scopeFields, scopeFields.length ? null : callout("neutral", "info", null, "따로 고를 범위가 없습니다."), periodic);
        nav("저장하고 테스트", async () => { await saveConfig(periodic.input.checked); return true; });
      } else {
        const result = h("div", {}, callout("info", "loader", "가져오는 중입니다", "처음 연결은 시간이 조금 걸릴 수 있습니다."));
        result.querySelector("svg").classList.add("spin");
        set(body, result);
        const runTest = async () => {
          const before = (await current()).status;
          await api("/api/connectors/run", "POST", {id: c.id});
          for (let i = 0; i < 45; i++) {
            await new Promise(res => setTimeout(res, 2000));
            const row = await current();
            if (row.status && (!before || row.status.at !== before.at)) {
              set(result, row.status.ok
                ? callout("success", "check-circle", "테스트에 성공했습니다", row.status.summary)
                : callout("danger", "x-circle", "가져오지 못했습니다", row.status.message));
              return;
            }
          }
          set(result, callout("warning", "clock", "응답이 늦습니다", "백그라운드에서 계속 시도합니다. 잠시 후 카드의 상태를 확인하세요."));
        };
        runTest().catch(e => set(result, callout("danger", "x-circle", "테스트를 시작하지 못했습니다", e.message)));
        close.setActions([{label: "이전", closes: false, onClick: () => { step--; draw(); return false; }},
          {label: "완료", variant: "primary", onClick: () => { renderRoute(true); }}]);
      }
    }
    draw();
  }

  // ---------------------------------------------------------------- notes and imports

  async function importFile(file) {
    if (!file) throw new Error("파일을 선택하세요.");
    const buf = new Uint8Array(await file.arrayBuffer());
    let bin = "";
    const chunk = 0x8000;
    for (let i = 0; i < buf.length; i += chunk) bin += String.fromCharCode.apply(null, buf.subarray(i, i + chunk));
    try {
      await api("/api/import/file", "POST", {name: file.name, content_base64: btoa(bin)});
      toast("'" + file.name + "'을(를) 읽어 점검에 포함했습니다.", "success");
    } catch (e) {
      toast(e.message, "danger");
      return;
    }
    await refreshAll();
  }

  // ---------------------------------------------------------------- digest

  function markdownView(text) {
    const out = h("div", {class: "digest stack-sm"});
    let list = null;
    for (const line of text.split("\n")) {
      if (/^#{1,3}\s/.test(line)) { list = null; out.appendChild(h(line.startsWith("# ") ? "h2" : "h3", {}, line.replace(/^#+\s/, ""))); }
      else if (/^\s*[-*]\s/.test(line)) { if (!list) { list = h("ul"); out.appendChild(list); } list.appendChild(h("li", {}, line.replace(/^\s*[-*]\s/, ""))); }
      else if (line.trim()) { list = null; out.appendChild(h("p", {}, line)); }
    }
    return out;
  }

  // ---------------------------------------------------------------- activity

  const AS = {tab: "activity", q: ""};

  function filesPanel(files) {
    const STATUS = {ok: ["success", "check", "읽음"], error: ["danger", "x", "읽지 못함"], unsupported: ["neutral", "minus", "지원 안 함"]};
    const q = h("input", {class: "input", type: "search", placeholder: "파일 이름 검색", value: AS.q, "aria-label": "파일 검색"});
    const body = h("tbody");
    const draw = () => {
      const t = AS.q.trim().toLowerCase();
      const rows = files.filter(f => !t || f.path.toLowerCase().includes(t));
      set(body, ...rows.slice(0, 500).map(f => {
        const [k, i, label] = STATUS[f.status] || ["neutral", null, f.status];
        return h("tr", {},
          h("td", {class: "mono"}, f.path.startsWith("@") ? h("span", {}, h("span", {class: "chip"}, "연결/메모"), " ", f.path) : f.path),
          h("td", {}, f.fmt), h("td", {}, badge(k, i, label)),
          h("td", {}, f.unclassified ? badge("warning", null, "분류 안 됨") : f.kinds.map(x => KINDS[x] || x).join(", ")),
          h("td", {}, String(f.meeting_items)), h("td", {}, String(f.artifacts)),
          h("td", {class: "small muted"}, [f.message].concat(f.warnings).filter(Boolean).join(" / ")));
      }));
    };
    q.addEventListener("input", () => { AS.q = q.value; draw(); });
    draw();
    if (files.length === 0) return card(null, null, null, emptyState({icon: "file", title: "읽은 파일이 없습니다", text: "폴더를 추가하면 읽은 파일이 여기에 표시됩니다.", neutral: true}));
    return card(null, null, null, h("div", {class: "stack"},
      h("div", {class: "toolbar"}, h("div", {class: "search-local"}, icon("search", "sm"), q)),
      h("p", {class: "small muted"}, "분류되지 않은 파일은 폴더에 .decision_agent.json을 만들어 kind_overrides로 종류를 지정할 수 있습니다."),
      h("div", {class: "table-wrap"}, h("table", {class: "table"},
        h("thead", {}, h("tr", {}, ...["파일", "형식", "상태", "분류", "회의 항목", "산출물", "참고"].map(x => h("th", {scope: "col"}, x)))), body))));
  }

  function checksPanel(st) {
    if (!st || st.folders.length === 0) return card(null, null, null, emptyState({icon: "folder", title: "감시 중인 폴더가 없습니다", neutral: true}));
    return h("div", {class: "stack"}, ...st.folders.map(f => {
      const c = f.completion, fail = f.failure;
      const judges = Object.entries(f.topics_by_judge || {}).map(([k, v]) => k + " " + v + "건").join(", ");
      const skipped = f.scan_stats ? Object.entries(f.scan_stats.skipped || {}).map(([k, v]) => k + " " + v + "개").join(", ") : "";
      return card(folderName(f.root), f.root, null, h("div", {class: "stack-sm"},
        c ? (c.verified
          ? callout("success", "check-circle", "실제 모델로 끝까지 점검했습니다", fmtTime(c.at) + " · " + c.provider_id + " · 파일 " + c.files_ok + "개, 결정 " + c.topics + "건")
          : callout("warning", "alert", "시연용 모드, 실제 검사가 아닙니다.", fmtTime(c.at) + " · 파일 " + c.files_ok + "개, 결정 " + c.topics + "건"))
          : callout("neutral", "clock", "아직 완료된 점검이 없습니다", null),
        judges ? h("p", {class: "small muted"}, "판정기별 결정 수: " + judges) : null,
        f.scan_stats ? h("p", {class: "small muted"}, "폴더 안 파일 " + f.scan_stats.seen + "개 확인" + (skipped ? " · 건너뜀: " + skipped : "")) : null,
        fail ? callout("danger", "x-circle", "마지막 문제 (" + fmtTime(fail.at) + ")", fail.message) : null));
    }));
  }

  // ---------------------------------------------------------------- settings

  // Figma 15:340 / 16:1185: two tabs, "연결 및 AI" and "알림 및 데이터".
  const SET = {tab: "connect", test: null};
  const CALENDAR_IDS = ["google_calendar", "outlook_calendar"];

  ROUTES.settings = {
    title: "설정", auto: false,
    async load(params) {
      if (params.has("tab")) SET.tab = params.get("tab") === "data" ? "data" : "connect";
      const [s, rows, auto, access] = await Promise.all([api("/api/settings"), api("/api/connectors"), api("/api/autostart"), api("/api/access")]);
      S.settings = s;
      return {s, rows, auto, access, st: S.status || await api("/api/status")};
    },
    render(data, params) {
      const tabs = h("div", {class: "set-tabs", role: "tablist"}, ...[["connect", "연결 및 AI"], ["data", "알림 및 데이터"]].map(([id, label]) =>
        h("button", {type: "button", class: "set-tab", role: "tab", "aria-selected": String(SET.tab === id),
          onclick: () => { SET.tab = id; lastSig = ""; renderRoute(false); }}, label)));
      const body = SET.tab === "data" ? dataTab(data) : connectTab(data);
      const node = h("div", {class: "page settings-page"}, h("div", {class: "page-head"}, h("h1", {}, "설정")), tabs, ...body);
      const focus = params.get("focus") || params.get("section");
      if (focus) setTimeout(() => {
        history.replaceState(null, "", "#/settings");
        const el = document.getElementById("set-" + focus);
        if (el) { el.scrollIntoView({block: "center"}); el.classList.add("flash"); }
      }, 60);
      return node;
    },
  };

  async function saveSettings(payload, message) {
    await api("/api/settings", "POST", payload);
    S.settings = Object.assign({}, S.settings || {}, payload);
    if (message) toast(message, "success");
  }

  // Figma "토글": 36x20 track, 16px knob, "켜짐" / "꺼짐" next to it.
  function switchCtl(checked, onChange, label) {
    const box = h("input", {type: "checkbox", role: "switch", checked, "aria-label": label || "켜기"});
    const text = h("span", {class: "sw-text"}, checked ? "켜짐" : "꺼짐");
    const node = h("label", {class: "sw"}, box, h("span", {class: "sw-track", "aria-hidden": "true"}), text);
    box.addEventListener("change", async () => {
      const want = box.checked;
      text.textContent = want ? "켜짐" : "꺼짐";
      try {
        const ok = await onChange(want);
        if (ok === false) { box.checked = !want; text.textContent = box.checked ? "켜짐" : "꺼짐"; }
      } catch (e) {
        box.checked = !want; text.textContent = box.checked ? "켜짐" : "꺼짐";
        toast(e.message, "danger");
      }
    });
    node.input = box;
    return node;
  }

  function setRow(label, ...controls) {
    return h("div", {class: "set-row"}, h("span", {class: "set-label"}, label), h("div", {class: "set-value"}, ...controls));
  }

  // ---------------------------------------------------------------- tab: connections and AI

  function connectTab({s, rows, st}) {
    const cal = rows.filter(r => CALENDAR_IDS.includes(r.id));
    const others = rows.filter(r => !CALENDAR_IDS.includes(r.id));
    const targetName = s.connector_target || s.allowed_folders[0] || "";
    const intro = h("div", {class: "set-note"}, icon("shield-check", "sm"),
      h("span", {}, "모든 연결은 읽기 전용입니다. 보내기, 삭제, 읽음 표시, 만들기 기능이 없고, 비밀 값은 Windows 자격 증명 관리자에만 저장됩니다."),
      targetName ? h("span", {class: "set-target", title: targetName}, "함께 분석할 폴더: " + folderName(targetName)) : null);
    return [intro, ...cal.map(serviceCard), aiCard(s, st), costCard(s), privacyCard(s, st),
      h("h2", {class: "set-section"}, "다른 연결"), ...others.map(serviceCard)];
  }

  const SERVICE_ICON = {google_calendar: "calendar", outlook_calendar: "calendar", gmail: "mail", outlook_mail: "mail",
    imap_mail: "inbox", notion: "book", jira: "kanban", slack: "hash"};

  // Figma 15:386 (연결됨), 16:1104 (미연동), 18:1045 (서비스 연결 오류).
  function serviceCard(c) {
    const st = c.status;
    const configured = c.problems.length === 0;
    const isCal = CALENDAR_IDS.includes(c.id);
    const last = st && st.at ? stamp(st.at) : null;
    if (c.enabled && st && !st.ok) {
      return h("section", {class: "card svc-card error", id: "set-" + c.id},
        icon("alert-circle", "svc-icon error"),
        h("div", {class: "svc-main"}, h("h2", {}, c.label + " 연결이 끊어졌습니다."),
          h("p", {class: "svc-line"}, (last ? "마지막 시도 " + last + " · " : "") + "이후 변경 사항을 가져오지 못했습니다."),
          h("p", {class: "svc-line small", title: st.message}, st.message)),
        h("div", {class: "svc-actions"}, button("다시 연결", {variant: "primary", onClick: () => connectWizard(c, c.uses_oauth ? "로그인" : "인증 정보")}),
          h("button", {type: "button", class: "link", onclick: () => manageConnector(c)}, "연결 관리")));
    }
    const state = c.enabled && configured ? ["ok", "연결됨"] : (configured ? ["off", "자동 가져오기 꺼짐"] : ["none", "연결 안 됨"]);
    const line = c.enabled && configured
      ? "읽기 범위: " + c.reads + (last ? " · 마지막 가져오기 " + last : "") + (st && st.ok && st.summary ? " (" + st.summary + ")" : "")
      : (configured ? "읽기 범위: " + c.reads : (isCal ? "관련 일정을 확인하려면 캘린더를 연결하십시오." : c.reads));
    return h("section", {class: "card svc-card", id: "set-" + c.id},
      icon(SERVICE_ICON[c.id] || "link", "svc-icon"),
      h("div", {class: "svc-main"}, h("h2", {}, c.label), h("p", {class: "svc-state " + state[0]}, state[1]), h("p", {class: "svc-line"}, line)),
      h("div", {class: "svc-actions"}, configured
        ? button("연결 관리", {onClick: () => manageConnector(c)})
        : button(c.label + " 연결", {variant: "primary", onClick: () => connectWizard(c)})));
  }

  // Figma 16:1538: state, what is read, last fetch, change scope, fetch now, disconnect.
  function manageConnector(c) {
    const st = c.status;
    const ok = c.enabled && (!st || st.ok);
    const result = h("p", {class: "small muted"});
    const run = button("지금 가져오기", {});
    run.addEventListener("click", () => busy(run, async () => {
      const before = st ? st.at : null;
      await api("/api/connectors/run", "POST", {id: c.id});
      result.textContent = "가져오는 중입니다.";
      for (let i = 0; i < 30; i++) {
        await new Promise(r => setTimeout(r, 2000));
        const row = (await api("/api/connectors")).find(r => r.id === c.id);
        if (row && row.status && row.status.at !== before) {
          result.textContent = row.status.ok ? "가져오기 완료: " + row.status.summary + " · 읽은 범위: " + row.reads : "가져오지 못했습니다: " + row.status.message;
          return;
        }
      }
      result.textContent = "응답이 늦습니다. 백그라운드에서 계속 시도합니다.";
    }));
    const close = modal({title: c.label + " 연결 관리", plain: true, onClose: () => renderRoute(false),
      body: h("div", {class: "stack"},
        h("div", {}, ok ? h("span", {class: "badge state ok"}, icon("check"), "정상")
          : (c.enabled ? h("span", {class: "badge state error"}, icon("alert-circle"), "오류") : h("span", {class: "badge state paused"}, icon("pause-bars"), "자동 가져오기 꺼짐"))),
        h("div", {class: "manage-lines"}, h("p", {}, "읽기 범위: " + c.reads),
          h("p", {}, "마지막 가져오기: " + (st && st.at ? stamp(st.at) + (st.ok && st.summary ? " (" + st.summary + ")" : "") : "아직 없음")),
          st && !st.ok ? h("p", {class: "danger-text"}, st.message) : null),
        h("div", {class: "row"}, button("읽기 범위 변경", {onClick: () => { close(); connectWizard(c, "범위 선택"); }}), run),
        result),
      actions: [{label: "연결 해제", variant: "danger-outline", onClick: async () => {
        await api("/api/connectors/disconnect", "POST", {id: c.id});
        toast("연결을 해제했습니다. 저장된 비밀 값과 가져온 사본을 지웠습니다.", "success");
      }}, {label: "닫기"}]});
  }

  // Figma "AI 판정 방식": 시연용 / 외부 AI / 로컬 AI, key "저장됨", connection test.
  function aiCard(s, st) {
    const now = st ? providerKind(st.provider.id) : "demo";
    const chosen = {mock: "demo", anthropic: "external", ollama: "local"}[s.provider] || now;
    const choice = (kind, title, sub) => h("button", {type: "button", class: "ai-choice" + (chosen === kind ? " selected" : ""),
      "aria-pressed": String(chosen === kind), onclick: async (e) => busy(e.currentTarget, async () => {
        const provider = {demo: "mock", external: "anthropic", local: "ollama"}[kind];
        await saveSettings({provider}, kind === "demo" ? "시연용 판정기로 바꿨습니다. 실제 검사가 아닙니다." : "판정 방식을 바꿨습니다. 새 방식으로 다시 점검합니다.");
        SET.test = null;
        await refreshAll();
      })},
    h("span", {class: "ai-title"}, title), h("span", {class: "ai-sub"}, sub));
    const rows = [];
    if (chosen === "external") {
      rows.push(h("div", {class: "ai-row"}, h("span", {}, "외부 AI API 키: " + (s.has_api_key ? "저장됨" : "저장 안 됨")),
        h("button", {type: "button", class: "link", onclick: () => keyDialog()}, s.has_api_key ? "키 변경" : "키 입력"),
        s.has_api_key ? h("button", {type: "button", class: "link", onclick: async () => {
          if (!await confirmDialog({title: "API 키를 삭제할까요?", message: "자격 증명 관리자에서 키를 지웁니다.", confirmLabel: "삭제", danger: true})) return;
          await api("/api/secret/api-key", "DELETE"); toast("API 키를 삭제했습니다.", "success"); renderRoute(true);
        }}, "삭제") : null));
      rows.push(h("div", {class: "ai-row"}, h("span", {}, "모델 이름: " + (s.model_name || "입력 안 됨")),
        h("button", {type: "button", class: "link", onclick: () => textDialog("모델 이름", s.model_name, "예: claude-sonnet-5-5", (v) => saveSettings({model_name: v}, "모델 이름을 저장했습니다."))}, "변경")));
    } else if (chosen === "local") {
      rows.push(h("div", {class: "ai-row"}, h("span", {}, "로컬 AI 모델: " + (s.model_name || "입력 안 됨")),
        h("button", {type: "button", class: "link", onclick: () => textDialog("로컬 AI 모델", s.model_name, "예: qwen2.5:7b", (v) => saveSettings({model_name: v}, "모델 이름을 저장했습니다."))}, "변경")));
      rows.push(h("div", {class: "ai-row"}, h("span", {class: "muted"}, "주소: " + s.ollama_url),
        h("button", {type: "button", class: "link", onclick: () => textDialog("Ollama 주소", s.ollama_url, "http://127.0.0.1:11434", (v) => saveSettings({ollama_url: v}, "주소를 저장했습니다."))}, "변경")));
    } else {
      rows.push(h("div", {class: "ai-row"}, h("span", {class: "warn-text"}, "시연용 판정기는 예시 문서의 어휘만 압니다. 실제 검사가 아닙니다.")));
    }
    const test = button("연결 테스트", {});
    const result = h("div", {class: "ai-result"});
    const drawResult = () => {
      const r = SET.test;
      let line = null;
      if (r) line = h("p", {class: r.ok ? "ok-text" : "danger-text"}, icon(r.ok ? "check" : "alert-circle", "sm"), r.message);
      else if (chosen === "external" && !s.allow_external_send) line = h("p", {class: "warn-text"}, icon("alert-circle", "sm"), "외부 전송 동의 필요");
      else if (st && st.provider && !st.provider.local_only) line = h("p", {class: "ok-text"}, icon("check", "sm"), (chosen === "local" ? "로컬 AI" : "외부 AI") + " 연결 설정됨 · 연결 테스트로 확인하세요");
      else if (chosen !== "demo" && st) line = h("p", {class: "warn-text", title: st.provider.reason}, icon("alert-circle", "sm"), "아직 시연용 판정기로 동작합니다: " + st.provider.reason);
      set(result, line);
    };
    test.addEventListener("click", () => busy(test, async () => { SET.test = await api("/api/provider/test", "POST"); drawResult(); }));
    drawResult();
    return h("section", {class: "card ai-card", id: "set-judge"}, h("h2", {}, "AI 판정 방식"),
      h("div", {class: "ai-choices"},
        choice("demo", "시연용", "실제 문서 검사 안 함"),
        choice("external", "외부 AI", "관련 내용을 외부로 전송"),
        choice("local", "로컬 AI", (chosen === "local" ? "현재 선택 · " : "") + "내 컴퓨터에서 처리")),
      h("div", {class: "ai-bottom"}, h("div", {class: "ai-rows"}, ...rows), test), result);
  }

  function serviceLogo(id) {
    return icon(SERVICE_ICON[id] || "link", "svc-icon");
  }

  function keyDialog() {
    const key = input({type: "password", autocomplete: "off", placeholder: "sk-ant-...", "aria-label": "API 키", autofocus: true});
    modal({title: "외부 AI API 키", plain: true,
      body: h("div", {class: "stack"}, key, h("p", {class: "small muted"}, "키는 Windows 자격 증명 관리자에만 저장되고 화면, 설정 파일, 로그에 나타나지 않습니다. 저장한 뒤에는 '저장됨'만 보입니다.")),
      actions: [{label: "저장", variant: "primary", onClick: async () => {
        if (key.value.trim().length < 8) throw new Error("API 키를 입력하세요.");
        await api("/api/secret/api-key", "POST", {value: key.value.trim()});
        key.value = "";
        toast("API 키를 저장했습니다.", "success");
        renderRoute(true);
      }}, {label: "취소"}]});
  }

  // "AI 사용량 절약": how carefully to judge, an optional cheaper model, and the confirmation limit.
  // Nothing is saved until "저장": each saved profile or model change starts a full re-check,
  // so clicking through the options must not spend calls.
  const COST_OPTIONS = [
    ["precise", "정밀", "모든 줄을 두 번 이상 확인합니다. 호출이 가장 많습니다."],
    ["balanced", "균형 (기본)", "일치로 보이는 줄은 한 번만 확인하고, 에이전트의 도구 선택은 AI에게 묻지 않습니다."],
    ["saver", "절약", "모든 줄을 한 번만 확인합니다. 호출이 가장 적지만 놓치거나 잘못 잡는 경우가 늘 수 있습니다."],
  ];
  function costCard(s) {
    let profile = s.cost_profile;
    const nodes = COST_OPTIONS.map(([v, label, help]) => {
      const radio = h("input", {type: "radio", name: "cost-profile", value: v, checked: v === profile});
      const n = h("label", {class: "choice" + (v === profile ? " selected" : "")}, radio, h("span", {class: "choice-title"}, label), h("span", {class: "small muted"}, help));
      radio.addEventListener("change", () => { profile = v; nodes.forEach(x => x.classList.toggle("selected", x === n)); drawHint(); });
      return n;
    });
    const choices = h("div", {class: "choice-grid", role: "radiogroup", "aria-label": "점검 정밀도"}, ...nodes);
    const cheap = input({value: s.cheap_model_name || "", placeholder: "예: claude-haiku-5-5"});
    const limit = input({type: "number", min: "0", step: "1", value: String(s.confirm_over_calls)});
    const hint = h("p", {class: "warn-text small cost-hint", role: "status"});
    const drawHint = () => {
      const rebuild = profile !== s.cost_profile || cheap.value.trim() !== (s.cheap_model_name || "");
      set(hint, rebuild ? "다음 점검 때 전체를 다시 점검합니다" : null);
    };
    cheap.addEventListener("input", drawHint);
    const save = button("저장", {variant: "primary", size: "sm"});
    save.addEventListener("click", () => busy(save, async () => {
      const n = Number(limit.value);
      if (!Number.isInteger(n) || n < 0) { toast("확인 기준 호출 수는 0 이상의 정수로 입력하세요.", "danger"); return; }
      const payload = {};
      if (profile !== s.cost_profile) payload.cost_profile = profile;
      if (cheap.value.trim() !== (s.cheap_model_name || "")) payload.cheap_model_name = cheap.value.trim();
      if (n !== s.confirm_over_calls) payload.confirm_over_calls = n;
      if (!Object.keys(payload).length) { toast("바뀐 설정이 없습니다.", "info"); return; }
      await saveSettings(payload, "AI 사용량 설정을 저장했습니다.");
      await refreshAll(); renderRoute(true);
    }));
    drawHint();
    return h("section", {class: "card set-card", id: "set-cost"}, h("h2", {}, "AI 사용량 절약"),
      h("div", {class: "stack"},
        field("점검 정밀도", choices),
        field("저가 모델 이름 (선택)", cheap, "비워 두면 위의 모델 하나로 전부 처리합니다. 값을 넣으면 읽기와 분류는 이 모델이, 일치 판정은 위의 모델이 처리합니다."),
        field("확인 기준 호출 수", limit, "예상 호출 수가 이 값을 넘으면 점검 전에 확인을 받습니다. 0이면 묻지 않습니다."),
        hint, h("div", {}, save)));
  }

  function textDialog(title, value, placeholder, save) {
    const field_ = input({value: value || "", placeholder, autofocus: true, "aria-label": title});
    modal({title, plain: true, body: field_, actions: [{label: "저장", variant: "primary", onClick: async () => {
      await save(field_.value.trim()); SET.test = null; await refreshAll(); renderRoute(true);
    }}, {label: "취소"}]});
  }

  // Figma "개인정보" with 19:1068 (동의 필요) and 19:1149 (허용): off by default, on only after consent.
  function privacyCard(s, st) {
    const external = s.provider === "anthropic" || (s.provider === "auto" && st && providerKind(st.provider.id) === "external");
    const sw = switchCtl(s.allow_external_send, async (want) => {
      if (want) {
        const ok = await consentDialog(s);
        if (!ok) return false;
        await saveSettings({allow_external_send: true}, "외부 AI 전송을 켰습니다.");
      } else {
        await saveSettings({allow_external_send: false}, "외부 AI 전송을 껐습니다. 문서가 컴퓨터 밖으로 나가지 않습니다.");
      }
      SET.test = null;
      await refreshAll();
      renderRoute(false);
      return true;
    }, "문서를 외부 AI로 보내기");
    const notes = s.allow_external_send
      ? [h("p", {class: "set-help"}, "현재 전송 범위: 선택한 문서의 관련 내용"), h("p", {class: "set-help"}, "전체 문서와 관련 없는 파일은 전송하지 않습니다.")]
      : [h("p", {class: "set-help"}, external ? "외부 AI를 사용하려면 전송 범위를 확인하고 동의해야 합니다." : "기본값: 꺼짐 · 외부 AI를 선택한 경우에도 별도 동의가 필요합니다.")];
    return h("section", {class: "card privacy-card", id: "set-privacy"}, h("h2", {}, "개인정보"),
      h("div", {class: "privacy-row"}, h("span", {class: "set-label"}, "문서를 외부 AI로 보내기"), sw), ...notes);
  }

  function consentDialog(s) {
    return new Promise((resolve) => {
      let answered = false;
      modal({title: "외부 AI 전송 동의", plain: true, onClose: () => { if (!answered) resolve(false); },
        body: h("div", {class: "stack-sm consent"},
          h("p", {}, "켜면 점검할 때 아래 내용이 외부 AI 서비스로 전송됩니다."),
          h("ul", {},
            h("li", {}, "받는 곳: Anthropic API" + (s.model_name ? " (" + s.model_name + ")" : "")),
            h("li", {}, "보내는 내용: 점검할 결정 문장과 관련 문서 항목의 문장, 연결에서 가져온 관련 내용"),
            h("li", {}, "보내지 않는 것: 관련 없는 파일 전체, 비밀번호와 토큰 같은 비밀 값")),
          h("p", {class: "small muted"}, "API 키와 모델 이름까지 있어야 실제로 전송됩니다. 언제든지 다시 끌 수 있습니다.")),
        actions: [{label: "동의하고 켜기", variant: "primary", onClick: () => { answered = true; resolve(true); }},
          {label: "취소", onClick: () => { answered = true; resolve(false); }}]});
    });
  }

  // ---------------------------------------------------------------- tab: notifications and data

  function dataTab({s, auto, access, st}) {
    const nameValue = h("span", {class: "set-text"}, s.display_name || "이름 없음");
    const profile = h("section", {class: "card set-card"}, h("h2", {}, "프로필"),
      setRow("표시 이름", nameValue, h("button", {type: "button", class: "link", onclick: () => textDialog("표시 이름", s.display_name, "표시 이름",
        (v) => saveSettings({display_name: v}, "표시 이름을 저장했습니다."))}, "변경")),
      h("p", {class: "set-help"}, "이 정보는 이 컴퓨터에만 저장됩니다."));

    const hk = (st && st.hotkey) || {};
    const digestTime = input({type: "time", value: s.digest_time, class: "input set-time", "aria-label": "하루 요약 시각", disabled: !s.digest_enabled});
    digestTime.addEventListener("change", () => saveSettings({digest_time: digestTime.value}, "하루 요약 시각을 저장했습니다.").catch(e => toast(e.message, "danger")));
    const quietStart = input({type: "time", value: s.quiet_start, class: "input set-time", "aria-label": "방해 금지 시작", disabled: !s.quiet_hours_enabled});
    const quietEnd = input({type: "time", value: s.quiet_end, class: "input set-time", "aria-label": "방해 금지 끝", disabled: !s.quiet_hours_enabled});
    const saveQuiet = () => saveSettings({quiet_start: quietStart.value, quiet_end: quietEnd.value}, "방해 금지 시간을 저장했습니다.").catch(e => toast(e.message, "danger"));
    quietStart.addEventListener("change", saveQuiet);
    quietEnd.addEventListener("change", saveQuiet);
    const reminder = select([["5", "5분 전"], ["10", "10분 전"], ["15", "15분 전"], ["30", "30분 전"]], String(s.meeting_reminder_minutes), {class: "select set-select", "aria-label": "회의 전 알림 시점", disabled: !s.meeting_reminder_enabled});
    reminder.addEventListener("change", () => saveSettings({meeting_reminder_minutes: Number(reminder.value)}, "회의 전 알림 시점을 저장했습니다."));
    const hotkeyLine = h("div", {class: "set-value"},
      h("span", {class: "set-text"}, hk.display || s.hotkey),
      s.hotkey_enabled ? (hk.active ? h("span", {class: "ok-text small"}, "등록됨") : (hk.reason ? h("span", {class: "warn-text small", title: hk.reason}, "등록 안 됨") : null)) : h("span", {class: "small muted"}, "꺼짐"),
      h("button", {type: "button", class: "link", onclick: () => hotkeyDialog(s, hk)}, "변경"));
    const alerts = h("section", {class: "card set-card"}, h("h2", {}, "알림"),
      setRow("알림 받기", switchCtl(s.notify, (v) => saveSettings({notify: v}, v ? "알림을 켰습니다." : "알림을 껐습니다."), "알림 받기")),
      setRow("하루 요약", switchCtl(s.digest_enabled, async (v) => { await saveSettings({digest_enabled: v}); digestTime.disabled = !v; }, "하루 요약"), digestTime),
      h("div", {class: "set-row", id: "set-hotkey"}, h("span", {class: "set-label"}, "빠른 점검 단축키"), hotkeyLine),
      hk.enabled && !hk.active && hk.reason ? h("div", {class: "hotkey-help"}, h("p", {class: "warn-text small"}, hk.reason),
        hk.suggestions && hk.suggestions.length ? h("div", {class: "row"}, h("span", {class: "small muted"}, "다른 조합:"),
          ...hk.suggestions.map(x => h("button", {type: "button", class: "chip-btn", onclick: async () => {
            await saveSettings({hotkey: x, hotkey_enabled: true}, "단축키를 바꿨습니다."); await refreshAll(); renderRoute(true);
          }}, x.split("+").map(k => k.length <= 3 ? k.toUpperCase() : k[0].toUpperCase() + k.slice(1)).join(" + ")))) : null) : null,
      setRow("방해 금지 시간", switchCtl(s.quiet_hours_enabled, async (v) => { await saveSettings({quiet_hours_enabled: v}); quietStart.disabled = quietEnd.disabled = !v; }, "방해 금지 시간"),
        quietStart, h("span", {class: "muted"}, "–"), quietEnd),
      h("p", {class: "set-help"}, "방해 금지 시간에는 알림을 띄우지 않고 도우미 버튼도 흔들리지 않습니다. 생긴 일은 받은 알림 목록과 배지 숫자에 남습니다. 같은 오류는 반복해서 알리지 않습니다."),
      setRow("회의 전 알림", switchCtl(s.meeting_reminder_enabled, async (v) => { await saveSettings({meeting_reminder_enabled: v}); reminder.disabled = !v; }, "회의 전 알림"), reminder),
      h("p", {class: "set-help"}, "곧 시작할 회의와 연결된 반영 안 됨 항목이 있으면 알려 줍니다. 캘린더 연결이 필요합니다."));

    const interval = select(["1", "5", "10", "15", "30", "60"].map(v => [v, v + "분마다"]), String(s.connector_interval_minutes), {class: "select set-select", "aria-label": "메일과 일정 가져오기 간격"});
    interval.addEventListener("change", () => saveSettings({connector_interval_minutes: Number(interval.value)}, "가져오기 간격을 저장했습니다."));
    const autoSw = switchCtl(auto.enabled, async (v) => {
      await api("/api/autostart", "POST", {enabled: v});
      toast(v ? "Windows 시작 시 자동으로 실행합니다." : "자동 실행을 껐습니다.", "success");
    }, "Windows 시작 시 자동 실행");
    autoSw.input.disabled = !auto.supported;
    const running = h("section", {class: "card set-card"}, h("h2", {}, "실행"),
      setRow("Windows 시작 시 자동 실행", autoSw, auto.supported ? null : h("span", {class: "small muted"}, "Windows에서만 지원합니다.")),
      setRow("빠른 점검(묶음 판정)", switchCtl(s.batch_size > 1, (v) => saveSettings({batch_size: v ? 8 : 1}, v ? "여러 항목을 한 번에 판정합니다." : "항목을 한 건씩 판정합니다."), "빠른 점검(묶음 판정)")),
      h("p", {class: "set-help"}, "켜면 같은 결정에 대한 항목을 묶어서 한 번에 판정해 호출 수가 줄어듭니다. 결과가 의심되면 끄고 다시 점검해 비교하세요."),
      setRow("바탕화면 도우미 버튼", switchCtl(s.helper_enabled, (v) => saveSettings({helper_enabled: v}, v ? "도우미 버튼을 표시합니다." : "도우미 버튼을 숨겼습니다. 트레이에서 계속 확인합니다."), "도우미 버튼")),
      setRow("메일과 일정 가져오기", interval),
      h("p", {class: "set-help"}, "창을 닫아도 도우미 버튼과 트레이에서 계속 확인합니다. 완전히 끄려면 트레이 메뉴의 '종료'를 누르세요."));

    const data = h("section", {class: "card set-card"}, h("h2", {}, "데이터"),
      h("div", {class: "set-links"},
        h("button", {type: "button", class: "link big", onclick: () => openLocation("data")}, "데이터 폴더 열기"),
        h("button", {type: "button", class: "link big", onclick: async () => {
          try { await copyText((await api("/api/diagnostics")).text, "진단 정보를 복사했습니다. 비밀 값과 문서 내용은 들어 있지 않습니다."); } catch (e) { toast(e.message, "danger"); }
        }}, "진단 정보 복사")),
      h("button", {type: "button", class: "link big danger-text strong", onclick: purgeFlow}, "모든 데이터 삭제"));

    const advanced = h("details", {class: "card set-advanced"}, h("summary", {}, "고급 설정", icon("chevron-right", "sm")), advancedBody(s, access));
    return [profile, alerts, running, data, advanced];
  }

  function hotkeyDialog(s, hk) {
    const on = toggle("단축키 사용", s.hotkey_enabled);
    const spec = input({value: s.hotkey, class: "input mono", "aria-label": "단축키", placeholder: "ctrl+shift+space"});
    modal({title: "빠른 점검 단축키", plain: true,
      body: h("div", {class: "stack"}, on, spec,
        h("p", {class: "small muted"}, "ctrl, alt, shift, win 중 하나 이상과 글자, 숫자, F1-F24, space, enter, tab, 방향키를 + 로 이어 적습니다. 예: ctrl+shift+space"),
        hk && hk.reason ? h("p", {class: "warn-text small"}, hk.reason) : null),
      actions: [{label: "저장", variant: "primary", onClick: async () => {
        await saveSettings({hotkey: spec.value.trim(), hotkey_enabled: on.input.checked}, "단축키를 저장했습니다.");
        await refreshAll(); renderRoute(true);
      }}, {label: "취소"}]});
  }

  function advancedBody(s, access) {
    const save = (payload, message) => saveSettings(payload, message || "설정을 저장했습니다.");
    const saveBtn = (fn) => { const b = button("저장", {variant: "primary", size: "sm"}); b.addEventListener("click", () => busy(b, fn)); return b; };
    const mode = select([["agent", "Agent: 상황을 보고 다음 단계를 고름"], ["pipeline", "Pipeline: 고정 순서"]], s.analysis_mode);
    const debounce = input({type: "number", min: "0", value: String(s.debounce_seconds)});
    const debounceMax = input({type: "number", min: "1", value: String(s.debounce_max_seconds)});
    const rescan = input({type: "number", min: "0", value: String(s.rescan_minutes)});
    const release = input({value: s.current_release || "", placeholder: "예: MVP"});
    const maxMb = input({type: "number", min: "1", value: String(s.max_file_mb)});
    const maxCalls = input({type: "number", min: "1", value: String(s.max_llm_calls_per_cycle)});
    const excludes = textarea({rows: "6", class: "textarea mono"});
    excludes.value = s.exclude_patterns.join("\n");
    const embed = input({value: s.embedding_model, placeholder: "예: nomic-embed-text"});
    const theme = segmented([["system", "시스템"], ["light", "라이트"], ["dark", "다크"]], s.theme, (v) => saveTheme(v), "테마");
    const list = (items) => h("ul", {}, ...items.map(i => h("li", {}, i)));
    return h("div", {class: "stack set-advanced-body"},
      h("h3", {}, "점검 동작"),
      h("div", {class: "field-row"}, field("점검 방식", mode), field("현재 릴리스", release, "결정의 적용 범위를 판단할 때 씁니다."),
        field("저장이 멈춘 뒤 기다릴 시간(초)", debounce, "쓰는 동안은 기다렸다가 저장이 멈추면 반영합니다."),
        field("최대 대기 시간(초)", debounceMax, "계속 저장되어도 이 시간이 지나면 반영합니다."),
        field("주기 확인 간격(분)", rescan, "변경 알림이 없어도 이 간격으로 폴더를 확인합니다. 바뀐 것이 없으면 AI를 호출하지 않습니다. 0이면 끕니다."),
        field("파일 최대 크기(MB)", maxMb), field("점검 1회 AI 호출 한도", maxCalls)),
      h("div", {}, saveBtn(() => save({analysis_mode: mode.value, debounce_seconds: Number(debounce.value || 0), debounce_max_seconds: Number(debounceMax.value || 120),
        rescan_minutes: Number(rescan.value || 0), current_release: release.value.trim() || null, max_file_mb: Number(maxMb.value || 25),
        max_llm_calls_per_cycle: Number(maxCalls.value || 400)}))),
      h("h3", {}, "제외 규칙"),
      field("제외 패턴 (한 줄에 하나)", excludes, "모든 폴더에 적용됩니다. 특정 하위 폴더만 빼려면 폴더 관리에서 체크를 해제하세요."),
      h("div", {}, saveBtn(() => save({exclude_patterns: excludes.value.split("\n").map(x => x.trim()).filter(Boolean)}))),
      h("h3", {}, "의미 검색 보강 (선택)"),
      field("임베딩 모델", embed, "로컬 AI(Ollama)로 글자 유사도가 놓친 후보를 보충합니다."),
      h("div", {}, saveBtn(() => save({embedding_model: embed.value.trim()}))),
      h("h3", {}, "화면"), h("div", {}, theme),
      h("h3", {}, "접근 범위"),
      h("div", {class: "stack-sm small"},
        h("p", {class: "strong"}, "읽는 폴더"), access.folders.length ? list(access.folders.map(f => folderName(f.root) + " (읽은 파일 " + (f.files.ok || 0) + "개)")) : h("p", {class: "muted"}, "없음"),
        h("p", {class: "strong"}, "켜 둔 연결"), access.connectors.length ? list(access.connectors.map(c => c.label + ": " + c.reads)) : h("p", {class: "muted"}, "없음"),
        h("p", {class: "strong"}, "절대 하지 않는 일"), list(access.never),
        h("p", {class: "strong"}, "컴퓨터에 남는 것"), list(access.stored_locally)),
      h("div", {class: "row"}, button("로그 폴더 열기", {size: "sm", onClick: () => openLocation("logs")})));
  }

  // Figma 16:1691 then 16:1745: two steps, the second needs the word "삭제".
  function purgeFlow() {
    modal({title: "모든 데이터를 삭제하시겠습니까?", plain: true,
      body: h("div", {class: "stack-sm"}, h("p", {class: "muted"}, "다음 항목이 이 컴퓨터에서 영구히 삭제됩니다."),
        h("ul", {class: "muted"}, h("li", {}, "API 키와 모든 연결의 토큰, 비밀번호"), h("li", {}, "점검 결과와 읽은 내용의 사본"),
          h("li", {}, "설정, 메모, 하루 요약, 점검 기록")),
        h("p", {class: "muted"}, "확인 폴더의 원본 파일은 삭제하거나 바꾸지 않습니다.")),
      actions: [{label: "계속", variant: "danger-outline", onClick: () => { setTimeout(purgeConfirm, 0); }}, {label: "취소"}]});
  }

  function purgeConfirm() {
    const confirmInput = input({placeholder: PURGE_CONFIRM, autofocus: true, "aria-label": "확인 문구"});
    return modal({title: "데이터 삭제 최종 확인", plain: true,
      body: h("div", {class: "stack"}, h("p", {class: "muted"}, "삭제를 진행하려면 '" + PURGE_CONFIRM + "'를 입력하십시오."), confirmInput),
      actions: [{label: "모든 데이터 삭제", variant: "danger-outline", onClick: async () => {
        if (confirmInput.value.trim() !== PURGE_CONFIRM) throw new Error("'" + PURGE_CONFIRM + "'를 정확히 입력하세요.");
        await api("/api/data/purge", "POST", {confirm: confirmInput.value.trim()});
        toast("모든 데이터를 삭제했습니다.", "success");
        try { localStorage.clear(); } catch (e) { /* ignore */ }
        S.settings = await api("/api/settings");
        applyTheme(S.settings.theme);
        await tick();
        go("home");
      }}, {label: "취소"}]});
  }

  // ---------------------------------------------------------------- onboarding

  const ONB = {active: false, step: 0, folder: null, node: null};

  function maybeOnboarding() {
    if (ONB.active || !S.status || !S.status.onboarding_needed) return;
    try { if (sessionStorage.getItem("da.onb.skip")) return; } catch (e) { /* ignore */ }
    ONB.active = true;
    ONB.step = 0;
    ONB.node = h("div", {class: "onboarding", role: "dialog", "aria-modal": "true", "aria-label": "처음 설정"});
    document.body.appendChild(ONB.node);
    drawOnboarding();
  }

  async function finishOnboarding() {
    try { await api("/api/settings", "POST", {onboarding_done: true}); } catch (e) { toast(e.message, "danger"); }
    ONB.active = false;
    if (ONB.node) ONB.node.remove();
    ONB.node = null;
    await refreshAll();
    go("home");
  }

  function onbStepper() {
    const names = ["폴더 선택", "판정 방식", "완료"];
    return h("div", {class: "stepper"}, ...names.flatMap((n, i) => [i ? h("span", {class: "step-sep"}) : null,
      h("span", {class: "step" + (ONB.step === i + 1 ? " active" : "") + (ONB.step > i + 1 ? " done" : "")},
        h("span", {class: "step-num"}, ONB.step > i + 1 ? icon("check", "sm") : String(i + 1)), n)]));
  }

  function drawOnboarding() {
    if (!ONB.node) return;
    const cardNode = h("div", {class: "onb-card"});
    if (ONB.step === 0) {
      const value = (iconName, title, text) => h("div", {class: "value"}, icon(iconName, "lg"), h("strong", {}, title), h("span", {class: "small muted"}, text));
      DA.append(cardNode, [
        h("div", {class: "row"}, h("img", {src: "/static/icon.svg", alt: "", width: "48", height: "48"})),
        h("div", {class: "stack-sm"}, h("div", {class: "onb-title"}, "Decision Agent에 오신 것을 환영합니다"),
          h("p", {class: "muted"}, "회의에서 결정이 바뀌었는데 요구사항, 작업 표, 테스트 계획, 일정에 반영되지 않은 항목을 찾아 알려 드립니다. 세 단계면 시작할 수 있습니다.")),
        h("div", {class: "value-list"},
          value("eye", "읽기만 합니다", "고른 폴더의 파일을 읽기만 하고 수정, 삭제, 새로 만들기를 하지 않습니다."),
          value("lock", "이 컴퓨터에만 저장", "설정, 결과, 키는 이 컴퓨터에만 남습니다. 회원 가입이 없습니다."),
          value("shield-check", "외부 전송 기본 꺼짐", "직접 켜기 전까지 문서 내용이 밖으로 나가지 않습니다.")),
        h("div", {class: "onb-foot"},
          button("나중에 하기", {variant: "ghost", onClick: () => { try { sessionStorage.setItem("da.onb.skip", "1"); } catch (e) { /* ignore */ } ONB.active = false; ONB.node.remove(); ONB.node = null; }}),
          button("시작하기", {variant: "primary", size: "lg", icon: "arrow-right", onClick: () => { ONB.step = 1; drawOnboarding(); }}))]);
    } else if (ONB.step === 1) {
      const area = h("div", {class: "stack"});
      const pick = button("폴더 선택", {variant: "primary", size: "lg", icon: "folder-plus"});
      const showPreview = async (path) => {
        set(area, skeleton(4));
        let p;
        try { p = await api("/api/folders/preview", "POST", {path}); } catch (e) { set(area, errorState(e.message)); return; }
        const blocked = !p.ok || p.relation.kind === "contained" || p.relation.kind === "duplicate";
        const use = button("이 폴더로 시작", {variant: "primary", icon: "check", disabled: blocked});
        use.addEventListener("click", () => busy(use, async () => {
          if (await addFolder(p.path, p.relation.kind === "parent")) {
            ONB.folder = p.path;
            ONB.step = 2;
            await tick();
            drawOnboarding();
          }
        }));
        set(area, ...previewBody(p), h("div", {class: "row"}, use));
      };
      pick.addEventListener("click", () => busy(pick, async () => {
        let r;
        try { r = await api("/api/folders/pick", "POST", {}); } catch (e) {
          if (e.status === 400) { manual.hidden = false; manualPath.focus(); return; }
          throw e;
        }
        if (r.path) await showPreview(r.path);
      }));
      const manualPath = input({class: "input mono", placeholder: "예: D:\\팀\\프로젝트"});
      const manual = h("div", {class: "row", hidden: true, style: {flexWrap: "nowrap"}}, manualPath,
        button("확인", {onClick: () => { if (manualPath.value.trim()) showPreview(manualPath.value.trim()); }}));
      DA.append(cardNode, [onbStepper(),
        h("div", {class: "stack-sm"}, h("h2", {}, "감시할 프로젝트 폴더를 고르세요"),
          h("p", {class: "muted"}, "가장 위의 폴더 하나만 고르면 그 안의 모든 하위 폴더가 자동으로 포함됩니다. 폴더를 고르면 바로 점검을 시작합니다.")),
        h("div", {class: "row"}, pick, button("경로 직접 입력", {variant: "ghost", onClick: () => { manual.hidden = false; manualPath.focus(); }})),
        manual, area,
        h("div", {class: "onb-foot"}, button("이전", {variant: "ghost", onClick: () => { ONB.step = 0; drawOnboarding(); }}), h("span", {class: "small muted"}, "다른 폴더는 나중에 '폴더' 화면에서 더 추가할 수 있습니다."))]);
    } else if (ONB.step === 2) {
      let provider = "auto";
      const extra = h("div", {class: "stack"});
      const model = input({placeholder: "모델 이름"});
      const key = input({type: "password", autocomplete: "off", placeholder: "Anthropic API 키"});
      const ext = toggle("외부 LLM으로 문서 내용 전송 허용", false, "켜야만 문서가 Anthropic으로 전송됩니다.");
      const drawExtra = () => set(extra, 
        provider === "anthropic" ? field("API 키", key, "Windows 자격 증명 관리자에만 저장됩니다.") : null,
        provider === "anthropic" || provider === "ollama" ? field("모델 이름", model) : null,
        provider === "anthropic" ? ext : null,
        provider === "auto" || provider === "mock" ? callout("warning", "alert", "데모 판정기는 실제 검사가 아닙니다", "먼저 둘러보기에는 충분하지만, 실제 문서를 검사하려면 나중에 '내 정보'에서 판정 방식을 바꾸세요.") : null);
      const choices = h("div", {class: "choice-grid"});
      const opts = [["auto", "데모로 먼저 둘러보기", "외부 전송 없음. 나중에 바꿀 수 있습니다.", "eye"],
        ["anthropic", "Anthropic", "API 키와 모델 이름 필요. 문서가 외부로 전송됩니다.", "cloud"],
        ["ollama", "Ollama (로컬)", "이 컴퓨터의 모델. 문서가 밖으로 나가지 않습니다.", "monitor"]];
      const nodes = opts.map(([v, label, help, iconName]) => {
        const radio = h("input", {type: "radio", name: "onb-provider", value: v, checked: v === provider});
        const n = h("label", {class: "choice" + (v === provider ? " selected" : "")}, radio, h("span", {class: "choice-title"}, icon(iconName, "sm"), label), h("span", {class: "small muted"}, help));
        radio.addEventListener("change", () => { provider = v; nodes.forEach(x => x.classList.toggle("selected", x === n)); drawExtra(); });
        return n;
      });
      DA.append(choices, nodes);
      drawExtra();
      const next = button("다음", {variant: "primary", icon: "arrow-right"});
      next.addEventListener("click", () => busy(next, async () => {
        if (provider === "anthropic") {
          if (key.value.trim().length >= 8) await api("/api/secret/api-key", "POST", {value: key.value.trim()});
          await api("/api/settings", "POST", {provider: "anthropic", model_name: model.value.trim(), allow_external_send: ext.input.checked});
        } else if (provider === "ollama") {
          await api("/api/settings", "POST", {provider: "ollama", model_name: model.value.trim()});
        } else {
          await api("/api/settings", "POST", {provider: "auto"});
        }
        ONB.step = 3;
        await tick();
        drawOnboarding();
      }));
      DA.append(cardNode, [onbStepper(),
        h("div", {class: "stack-sm"}, h("h2", {}, "판정 방식을 고르세요"),
          h("p", {class: "muted"}, "실제 모델이 끝까지 점검했을 때만 '검사 완료'로 표시합니다. 데모 판정기 결과는 '실제 검사가 아닙니다'로 구분해 보여 드립니다.")),
        choices, extra,
        h("div", {class: "onb-foot"}, h("span", {class: "small muted"}, "선택한 폴더: " + (ONB.folder || "-")), next)]);
    } else {
      const st = S.status;
      const p = st && st.folders[0] ? st.folders[0].progress : null;
      DA.append(cardNode, [onbStepper(),
        h("div", {class: "stack-sm"}, h("h2", {}, "준비가 끝났습니다"),
          h("p", {class: "muted"}, "폴더를 점검하고 있습니다. 창을 닫아도 트레이 아이콘에서 계속 감시하고, 결정이 바뀌었는데 반영되지 않은 항목이 생기면 알려 드립니다.")),
        st ? h("div", {class: "row"}, stateBadge(st.overall, "lg"), transmissionBadge(st.provider)) : null,
        p && (p.files_total || p.topics_total) ? h("div", {class: "grid grid-2"}, progressBar(p.files_done, p.files_total, "읽은 파일"), progressBar(p.topics_done, p.topics_total, "점검한 결정")) : null,
        h("div", {class: "onb-foot"}, h("span", {class: "small muted"}, "단축키 Ctrl+Alt+D로 회의 중 빠른 점검 창을 열 수 있습니다."),
          button("홈으로 이동", {variant: "primary", size: "lg", icon: "arrow-right", onClick: finishOnboarding}))]);
    }
    set(ONB.node, cardNode);
    const focusTarget = cardNode.querySelector(".btn.primary");
    if (focusTarget && !cardNode.contains(document.activeElement)) focusTarget.focus();
  }

  // ---------------------------------------------------------------- refresh loop and boot

  async function tick() {
    try {
      S.status = await api("/api/status");
      S.offline = false;
    } catch (e) {
      S.offline = true;
      renderNav();
      return;
    }
    renderNav();
    if (ONB.active && ONB.step === 3) drawOnboarding();
    maybeOnboarding();
  }

  async function loop() {
    await tick();
    const route = ROUTES[parseHash().name];
    if (!S.offline && route.auto && !anyLayerOpen() && !isTyping() && !openPopover && !ONB.active) {
      await renderRoute(false);
    }
  }

  async function boot() {
    buildDropVeil();
    try {
      S.settings = await api("/api/settings");
      applyTheme(S.settings.theme);
    } catch (e) {
      S.settings = null;
    }
    await tick();
    await renderRoute(true);
    window.addEventListener("hashchange", () => {
      if (parseHash().name === "home") resetCalendar();
      closePopover(); renderRoute(true); document.getElementById("view").focus({preventScroll: true}); });
    setInterval(loop, REFRESH_MS);
  }

  DA.initTheme();
  boot();
})();
