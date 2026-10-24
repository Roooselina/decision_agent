/* Dashboard: layout, router, and every screen. Uses the helpers in common.js. */
"use strict";

(() => {
  const {h, set, icon, api, button, busy, badge, field, input, textarea, select, toggle, segmented, callout,
    progressBar, skeleton, emptyState, errorState, toast, modal, confirmDialog, drawer, anyLayerOpen,
    copyText, applyTheme, fmtTime, relTime, stateInfo, stateBadge, transmissionBadge, labelBadge, KINDS,
    providerName, providerKind, agentState} = DA;

  const S = {status: null, settings: null, offline: false};
  const REFRESH_MS = 4000;
  const PURGE_CONFIRM = "모두 삭제";

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
      if (params.has("week")) HS.week = params.get("week");
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
      return page("홈", null, [h("span", {class: "page-date"}, longDate(new Date()))],
        agentCard(st),
        h("div", {class: "home-grid"}, calendarCard(week, st), todoCard(st, findings, questions)),
        recentCard(st, activity));
    },
  };

  const HS = {week: null, day: null, flash: null};
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

  // Running numbers for the thin bar: files while reading, decisions while checking.
  function runningProgress(st) {
    let done = 0, total = 0, text = "";
    for (const f of st.folders) {
      const p = f.progress;
      if (p.phase === "checking" && p.topics_total) { done += p.topics_done; total += p.topics_total; text = "결정"; }
      else if (p.files_total) { done += p.files_done; total += p.files_total; text = text || "문서"; }
    }
    if (!total) return {pct: 0, text: "문서를 확인하고 있습니다."};
    return {pct: Math.round(done * 100 / total), text: text === "결정" ? "결정 " + total + "건 중 " + done + "건 확인" : "문서 " + total + "개 중 " + done + "개 확인"};
  }

  // The "문서 확인 AI AGENT" card (Figma 15:44 and the 문서 확인 상태 카드 variants).
  function agentCard(st) {
    const state = agentState(st);
    const info = stateInfo(st.overall, st.paused);
    const watch = st.watch || {};
    const demo = state === "demo";
    const ICON = {verified: ["shield-check", "ok"], running: ["loader", "busy"], paused: ["pause-bars", "paused"],
      failed: ["alert-circle", "error"], demo: ["shield-check", "demo"], retrying: ["alert-circle", "attention"],
      stale: ["alert-circle", "attention"], incomplete: ["clock", "busy"]};
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
        state === "retrying" || state === "stale" ? h("span", {class: "agent-note warn", title: info.detail || ""}, info.label) : null,
        state === "incomplete" ? h("span", {class: "agent-note"}, "첫 확인을 준비하고 있습니다.") : null,
        h("span", {class: "agent-note" + (sends ? " info" : "")}, sends ? "문서 처리: 외부 AI로 전송 중 (" + st.provider.id + ")" : "문서 처리: 내 컴퓨터 · 외부 전송 안 함"));
    }
    const metas = demo
      ? h("div", {class: "agent-meta-line"}, "마지막 확인 ", stamp(watch.last_checked), "   ·   확인 중인 폴더 " + folders)
      : h("div", {class: "agent-metas"},
        metaBlock("마지막 확인", stamp(watch.last_checked), watch.last_checked ? relSpan(watch.last_checked, "agent-rel") : null),
        metaBlock("확인 중인 폴더", folders),
        h("div", {class: "agent-side"}, right));
    return h("section", {class: "card agent-card " + tone + (demo ? " demo" : ""), "aria-label": "문서 확인 상태"},
      head, sub, metas, action ? h("div", {class: "agent-action"}, action) : null, bar);
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
    const move = (n) => { HS.week = addDays(start, 7 * n); HS.day = null; renderRoute(false); };
    const list = h("div", {class: "cal-list"});
    const strip = h("div", {class: "cal-strip", role: "tablist", "aria-label": "요일"});
    const drawDay = () => {
      set(strip, ...days.map(d => {
        const date = toDate(d);
        return h("button", {type: "button", class: "cal-day" + (d === HS.day ? " selected" : "") + (d === week.today ? " today" : ""),
          role: "tab", "aria-selected": String(d === HS.day), "aria-label": longDate(date) + " 일정 " + byDay.get(d).length + "건",
          onclick: () => { HS.day = d; drawDay(); }},
        h("span", {class: "cal-wd"}, WEEKDAYS[date.getDay()]), h("span", {class: "cal-dn"}, String(date.getDate())),
        byDay.get(d).length ? h("span", {class: "cal-dot", "aria-hidden": "true"}) : null);
      }));
      const items = byDay.get(HS.day);
      if (items.length) {
        set(list, ...items.map(eventRow));
      } else if (week.events.length === 0 && week.nearest_week) {
        set(list, h("div", {class: "cal-none"}, h("span", {}, "이번 주에는 일정이 없습니다."),
          h("button", {type: "button", class: "link", onclick: () => { HS.week = week.nearest_week; HS.day = null; renderRoute(false); }}, "일정이 있는 주로 이동")));
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

  const RS = {q: "", label: "all", folder: "all", owner: "all", status: "open", sort: "severity"};
  const HIDDEN = {dismissed: "확인함", false_positive: "잘못된 탐지", snoozed: "미룸"};

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

  ROUTES.results = {
    title: "점검 결과", icon: "results", auto: true,
    lead: "결정별로 묶은 점검 결과입니다. 항목을 누르면 근거와 수정 초안을 볼 수 있습니다.",
    async load(params) {
      if (params.has("label")) { RS.label = params.get("label"); }
      if (params.has("q")) { RS.q = params.get("q"); }
      return {rows: await api("/api/findings?all=1"), overall: S.status ? S.status.overall.state : ""};
    },
    render({rows, overall}, params) {
      const listBox = h("div", {class: "stack"});
      const folders = [...new Set(rows.map(r => r.root))];
      const owners = [...new Set(rows.map(r => r.owner).filter(Boolean))].sort();
      const counts = {all: 0, INCONSISTENT: 0, UNCERTAIN: 0, CONSISTENT: 0};
      rows.filter(r => RS.status === "all" || effectiveStatus(r) === RS.status).forEach(r => { counts.all++; counts[r.label] = (counts[r.label] || 0) + 1; });
      const q = h("input", {class: "input", type: "search", placeholder: "내용, 파일, 담당자, 결정 검색", value: RS.q, "aria-label": "결과 검색"});
      q.addEventListener("input", () => { RS.q = q.value; draw(); });
      const labelSeg = segmented([["all", "전체 " + counts.all], ["INCONSISTENT", "불일치 " + counts.INCONSISTENT],
        ["UNCERTAIN", "불확실 " + counts.UNCERTAIN], ["CONSISTENT", "일치 " + counts.CONSISTENT]], RS.label, (v) => { RS.label = v; draw(); }, "판정 필터");
      const folderSel = select([["all", "모든 폴더"], ...folders.map(f => [f, folderName(f)])], RS.folder, {"aria-label": "폴더 필터"});
      folderSel.addEventListener("change", () => { RS.folder = folderSel.value; draw(); });
      const ownerSel = select([["all", "모든 담당자"], ...owners.map(o => [o, o])], RS.owner, {"aria-label": "담당자 필터"});
      ownerSel.addEventListener("change", () => { RS.owner = ownerSel.value; draw(); });
      const statusSel = select([["open", "열린 항목"], ["dismissed", "확인함"], ["false_positive", "잘못된 탐지"], ["snoozed", "미룸"], ["all", "모든 상태"]], RS.status, {"aria-label": "상태 필터"});
      statusSel.addEventListener("change", () => { RS.status = statusSel.value; lastSig = ""; renderRoute(false); });
      const sortSel = select([["severity", "심각한 순"], ["recent", "최근 발견 순"], ["name", "결정 이름 순"]], RS.sort, {"aria-label": "정렬"});
      sortSel.addEventListener("change", () => { RS.sort = sortSel.value; draw(); });

      function draw() {
        const terms = RS.q.trim().toLowerCase().split(/\s+/).filter(Boolean);
        const shown = rows.filter(r =>
          (RS.status === "all" || effectiveStatus(r) === RS.status) &&
          (RS.label === "all" || r.label === RS.label) &&
          (RS.folder === "all" || r.root === RS.folder) &&
          (RS.owner === "all" || r.owner === RS.owner) &&
          terms.every(t => [r.text, r.evidence, r.source_doc, r.owner, r.topic_label, r.action].join(" ").toLowerCase().includes(t)));
        if (rows.length === 0) {
          set(listBox, h("section", {class: "card"}, emptyState({icon: "results", title: "아직 점검 결과가 없습니다",
            text: S.status && S.status.registered_folders ? "점검이 끝나면 결정별 결과가 여기에 표시됩니다." : "먼저 감시할 폴더를 추가하세요.",
            action: S.status && S.status.registered_folders ? null : button("폴더 추가", {variant: "primary", icon: "folder-plus", onClick: () => startAddFolder()})})));
          return;
        }
        if (shown.length === 0) {
          set(listBox, h("section", {class: "card"}, emptyState({icon: "filter", title: "조건에 맞는 항목이 없습니다",
            text: "필터나 검색어를 바꿔 보세요.", neutral: true,
            action: button("필터 초기화", {onClick: () => { Object.assign(RS, {q: "", label: "all", folder: "all", owner: "all", status: "open"}); lastSig = ""; renderRoute(true); }})})));
          return;
        }
        const groups = new Map();
        for (const r of shown) {
          const key = r.root + "|" + r.topic;
          if (!groups.has(key)) groups.set(key, {label: r.topic_label, root: r.root, items: []});
          groups.get(key).items.push(r);
        }
        const list = [...groups.values()];
        const score = (g) => g.items.filter(i => i.label === "INCONSISTENT").length * 100 + g.items.filter(i => i.label === "UNCERTAIN").length;
        const recent = (g) => g.items.map(i => i.last_seen || i.first_seen || "").sort().pop() || "";
        if (RS.sort === "severity") list.sort((a, b) => score(b) - score(a));
        else if (RS.sort === "recent") list.sort((a, b) => recent(b).localeCompare(recent(a)));
        else list.sort((a, b) => a.label.localeCompare(b.label, "ko"));
        set(listBox, ...list.map(groupCard));
      }
      draw();
      const notice = overall === "demo" ? callout("warning", "alert", "데모 판정기 결과입니다", "실제 검사가 아니므로 결과를 근거로 판단하지 마세요.") : null;
      const node = page(this.title, this.lead, null, notice,
        h("div", {class: "card tight"}, h("div", {class: "toolbar"},
          h("div", {class: "search-local"}, icon("search", "sm"), q), labelSeg, folderSel, ownerSel, statusSel, sortSel)),
        listBox);
      const target = params.get("open");
      if (target) {
        const [root, topic, artifact] = target.split("|");
        const row = rows.find(r => r.root === root && r.topic === topic && r.artifact_id === artifact);
        if (row) setTimeout(() => openFinding(row), 50);
        history.replaceState(null, "", "#/results");
      }
      return node;
    },
  };

  function groupCard(g) {
    const bad = g.items.filter(i => i.label === "INCONSISTENT").length;
    const unsure = g.items.filter(i => i.label === "UNCERTAIN").length;
    return h("section", {class: "card group-card"},
      h("div", {class: "group-head"},
        h("div", {class: "group-title"}, h("span", {class: "small muted"}, "결정"), h("h3", {}, g.label)),
        h("div", {class: "row"},
          bad ? badge("danger", "x-circle", "불일치 " + bad) : null,
          unsure ? badge("warning", "help", "불확실 " + unsure) : null,
          h("span", {class: "chip", title: g.root}, icon("folder", "sm"), folderName(g.root)))),
      ...g.items.map(r => h("button", {type: "button", class: "finding-row", onclick: () => openFinding(r)},
        h("div", {}, labelBadge(r.label)),
        h("div", {class: "stack-sm", style: {gap: "4px", minWidth: "0"}},
          h("span", {class: "finding-text"}, r.text),
          h("span", {class: "list-sub"},
            h("span", {class: "chip"}, KINDS[r.kind] || r.kind),
            h("span", {}, r.source_doc + (r.location ? " : " + r.location : "")),
            r.owner ? h("span", {}, "· 담당 " + r.owner) : null,
            effectiveStatus(r) !== "open" ? badge("neutral", null, HIDDEN[effectiveStatus(r)]) : null)),
        icon("chevron-right", "chev"))));
  }

  function openFinding(f) {
    const draftBox = h("div", {class: "stack-sm"});
    const setStatus = async (status, hours) => {
      await api("/api/finding/status", "POST", {root: f.root, topic: f.topic, artifact_id: f.artifact_id, status, snooze_hours: hours || 0});
      close();
      toast({dismissed: "확인함으로 표시했습니다.", false_positive: "잘못된 탐지로 표시했습니다. 다시 점검해도 숨겨집니다.",
        snoozed: "내일 다시 보여 드립니다.", open: "다시 열었습니다."}[status], "success");
      await refreshAll();
    };
    const actionBtn = (label, variant, iconName, status, hours) => {
      const b = button(label, {variant, icon: iconName});
      b.addEventListener("click", () => busy(b, () => setStatus(status, hours)));
      return b;
    };
    const draftBtn = button("수정 초안 만들기", {icon: "note"});
    draftBtn.addEventListener("click", () => busy(draftBtn, async () => {
      const d = await api("/api/finding/draft", "POST", {root: f.root, topic: f.topic, artifact_id: f.artifact_id});
      const message = textarea({readonly: true, rows: "6", "aria-label": "담당자에게 보낼 메시지"});
      message.value = d.message;
      set(draftBox, 
        d.revised_text ? h("div", {class: "detail-block"},
          h("div", {class: "row-between"}, h("span", {class: "detail-label"}, "고친 문장 제안"),
            button("복사", {size: "sm", variant: "ghost", icon: "copy", onClick: () => copyText(d.revised_text)})),
          h("div", {class: "detail-box"}, d.revised_text)) : null,
        d.note ? h("p", {class: "small muted"}, d.note) : null,
        !d.model_used ? callout("neutral", "info", null, "데모 판정기라 문장 제안은 없고 담당자에게 보낼 메시지만 만듭니다.") : null,
        h("div", {class: "detail-block"},
          h("div", {class: "row-between"}, h("span", {class: "detail-label"}, "담당자에게 보낼 메시지"),
            button("복사", {size: "sm", variant: "ghost", icon: "copy", onClick: () => copyText(d.message)})),
          message),
        h("p", {class: "small muted"}, "이 프로그램은 파일을 고치거나 메시지를 보내지 않습니다. 복사해서 직접 사용하세요."));
    }));
    const status = effectiveStatus(f);
    const block = (label, iconName, text) => text ? h("div", {class: "detail-block"},
      h("span", {class: "detail-label"}, icon(iconName, "sm"), label), h("div", {class: "detail-box"}, text)) : null;
    const confidence = typeof f.confidence === "number" && f.label !== "UNCERTAIN"
      ? h("span", {class: "chip", title: "같은 판단을 여러 번 했을 때 같은 답이 나온 비율입니다. 보정된 확률이 아닙니다."}, "표본 일치율 " + Math.round(f.confidence * 100) + "%") : null;
    const close = drawer({
      title: f.text,
      subtitle: h("div", {class: "row"}, labelBadge(f.label), confidence, status !== "open" ? badge("neutral", null, HIDDEN[status]) : null),
      body: [
        block("결정", "results", f.topic_label),
        block("근거", "info", f.evidence),
        block("권장 조치", "arrow-right", f.action),
        h("dl", {class: "kv"},
          h("dt", {}, "종류"), h("dd", {}, KINDS[f.kind] || f.kind),
          h("dt", {}, "출처 파일"), h("dd", {class: "mono"}, f.source_doc),
          h("dt", {}, "위치"), h("dd", {}, f.location || "-"),
          h("dt", {}, "폴더"), h("dd", {class: "mono"}, f.root),
          h("dt", {}, "담당자"), h("dd", {}, f.owner || "지정되지 않음"),
          h("dt", {}, "처음 발견"), h("dd", {}, fmtTime(f.first_seen)),
          h("dt", {}, "마지막 확인"), h("dd", {}, fmtTime(f.last_seen))),
        f.label === "INCONSISTENT" ? h("div", {class: "stack-sm"}, h("h3", {}, "수정 초안"), draftBox, h("div", {}, draftBtn)) : null,
      ],
      foot: status === "open"
        ? [actionBtn("확인함", "primary", "check", "dismissed"), actionBtn("잘못된 탐지", "", "x", "false_positive"),
          actionBtn("내일 다시", "ghost", "clock", "snoozed", 24)]
        : [actionBtn("다시 열기", "primary", "refresh", "open")],
    });
  }

  // ---------------------------------------------------------------- folders

  const FS = {open: null, trees: {}};

  ROUTES.folders = {
    title: "폴더 관리", icon: "folder", auto: false,
    lead: "선택한 폴더와 그 안의 모든 하위 폴더를 읽습니다. 파일은 읽기만 하고 수정하거나 새로 만들지 않습니다.",
    async load() { return api("/api/folders"); },
    render(cards) {
      const actions = [
        button("경로 직접 입력", {variant: "ghost", onClick: manualPathDialog}),
        button("폴더 추가", {variant: "primary", icon: "folder-plus", onClick: () => startAddFolder()}),
      ];
      const drop = h("div", {class: "dropzone"}, h("div", {class: "folder-icon"}, icon("upload")),
        h("div", {class: "stack-sm", style: {gap: "2px"}}, h("strong", {}, "폴더를 이 창으로 끌어다 놓아도 추가할 수 있습니다"),
          h("span", {class: "small muted"}, "가장 위의 폴더 하나만 고르면 그 아래 폴더는 자동으로 포함됩니다. 필요 없는 하위 폴더는 '하위 폴더 선택'에서 뺄 수 있습니다.")));
      if (cards.length === 0) {
        return page(this.title, this.lead, actions, h("section", {class: "card"}, emptyState({icon: "folder-plus", title: "아직 등록한 폴더가 없습니다",
          text: "프로젝트 문서가 있는 가장 위의 폴더를 고르세요. 드라이브 전체, 사용자 폴더 전체, 시스템 폴더는 등록할 수 없습니다.",
          action: button("폴더 추가", {variant: "primary", icon: "folder-plus", onClick: () => startAddFolder()})})), drop);
      }
      return page(this.title, this.lead, actions, ...cards.map(folderCard), drop);
    },
  };

  function folderCard(c) {
    const WATCH = {watching: ["success", "eye", "감시 중"], paused: ["neutral", "pause", "일시 중지"], missing: ["danger", "x-circle", "폴더 없음"]};
    const [kind, iconName, text] = WATCH[c.watch] || WATCH.watching;
    const treeBox = h("div");
    const treeBtn = button(FS.open === c.root ? "하위 폴더 닫기" : "하위 폴더 선택", {icon: "filter", disabled: !c.exists});
    treeBtn.addEventListener("click", () => {
      if (FS.open === c.root) { FS.open = null; set(treeBox, ); treeBtn.lastChild.textContent = "하위 폴더 선택"; return; }
      FS.open = c.root;
      treeBtn.lastChild.textContent = "하위 폴더 닫기";
      loadTree(c.root, treeBox);
    });
    if (FS.open === c.root) setTimeout(() => loadTree(c.root, treeBox), 0);
    const remove = button("제거", {variant: "danger-outline", icon: "trash"});
    remove.addEventListener("click", async () => {
      const ok = await confirmDialog({title: "폴더를 목록에서 제거할까요?", danger: true, confirmLabel: "제거",
        message: h("div", {class: "stack-sm"}, h("p", {class: "mono"}, c.root),
          h("p", {}, "폴더와 그 안의 파일은 그대로 남습니다. 이 앱이 저장한 읽은 내용의 사본과 점검 결과만 지웁니다."))});
      if (!ok) return;
      await busy(remove, async () => {
        await api("/api/folders/remove", "POST", {path: c.root});
        toast("폴더를 목록에서 제거했습니다. 원본 파일은 그대로입니다.", "success");
        await refreshAll();
        renderRoute(true);
      });
    });
    return h("section", {class: "card folder-card"},
      h("div", {class: "folder-top"},
        h("div", {class: "folder-icon" + (c.exists ? "" : " missing")}, icon("folder", "lg")),
        h("div", {class: "stack-sm", style: {flex: "1 1 auto", minWidth: "0"}},
          h("div", {class: "row"}, h("strong", {}, folderName(c.root)), badge(kind, iconName, text),
            c.is_target ? h("span", {class: "chip", title: "메일, 캘린더, 메모, 가져온 파일을 이 폴더와 함께 분석합니다."}, "연결 자료 분석 대상") : null),
          h("span", {class: "folder-path truncate", title: c.root}, c.root),
          !c.exists ? callout("danger", "alert", "폴더를 찾을 수 없습니다", "옮겨졌거나 이름이 바뀌었을 수 있습니다. 다시 추가하거나 목록에서 제거하세요.") : null),
        h("div", {class: "btn-group"}, treeBtn,
          button("탐색기에서 열기", {icon: "external", disabled: !c.exists, onClick: () => openLocation(c.root)}), remove)),
      h("div", {class: "stats"},
        stat(c.files_ok, "읽은 파일"), stat(c.files_unsupported, "지원하지 않는 파일"), stat(c.files_error, "읽지 못한 파일"),
        stat(c.excluded.length, "제외한 하위 폴더"), stat(relTime(c.last_checked), "마지막 점검")),
      treeBox);
  }

  function stat(value, label) {
    return h("div", {class: "stat"}, h("span", {class: "stat-value"}, String(value)), h("span", {class: "stat-label"}, label));
  }

  async function loadTree(root, box) {
    set(box, skeleton(4));
    let data;
    try { data = await api("/api/folders/tree?root=" + encodeURIComponent(root)); } catch (e) {
      set(box, errorState(e.message, () => loadTree(root, box)));
      return;
    }
    const excluded = new Set(data.excluded);
    const original = JSON.stringify([...excluded].sort());
    const expanded = new Set([""]);
    const container = h("div", {class: "stack-sm"});
    const under = (rel, parent) => parent === "" ? true : rel === parent || rel.startsWith(parent + "/");
    const ancestorExcluded = (rel) => [...excluded].some(e => e !== rel && under(rel, e) && rel !== e);
    const saveBtn = button("변경 저장", {variant: "primary", icon: "check"});
    const summary = h("span", {class: "small muted"});

    function includedCount(node) {
      if (excluded.has(node.rel) && node.rel !== "") return 0;
      return node.readable + node.children.reduce((sum, c) => sum + includedCount(c), 0);
    }

    function nodeView(node) {
      const isRoot = node.rel === "";
      const off = !isRoot && (excluded.has(node.rel) || ancestorExcluded(node.rel));
      const hasKids = node.children.length > 0;
      const open = expanded.has(node.rel);
      const toggleBtn = hasKids ? h("button", {type: "button", class: "tree-toggle", "aria-expanded": String(open),
        "aria-label": (open ? "접기: " : "펼치기: ") + node.name, onclick: () => { open ? expanded.delete(node.rel) : expanded.add(node.rel); draw(); }},
      icon("chevron-right")) : h("span", {class: "tree-toggle", "aria-hidden": "true"});
      let box = null;
      if (!isRoot) {
        box = h("input", {type: "checkbox", "aria-label": node.name + " 포함", checked: !off, disabled: ancestorExcluded(node.rel)});
        const partial = !off && [...excluded].some(e => e.startsWith(node.rel + "/"));
        box.indeterminate = partial;
        box.addEventListener("change", () => {
          for (const e of [...excluded]) if (e.startsWith(node.rel + "/")) excluded.delete(e);
          if (box.checked) excluded.delete(node.rel); else excluded.add(node.rel);
          draw();
        });
      }
      const row = h("div", {class: "tree-row" + (off ? " off" : ""), role: "treeitem", "aria-expanded": hasKids ? String(open) : null},
        toggleBtn, box || icon("folder", "sm"), h("span", {class: "name", title: node.rel || node.name}, isRoot ? node.name + " (선택한 폴더)" : node.name),
        h("span", {class: "tree-counts"}, "문서 " + node.total_readable + (node.total_unsupported ? " · 지원 안 함 " + node.total_unsupported : "")));
      return h("li", {}, row, hasKids && open ? h("ul", {role: "group"}, ...node.children.map(nodeView)) : null);
    }

    function draw() {
      const dirty = JSON.stringify([...excluded].sort()) !== original;
      saveBtn.disabled = !dirty;
      summary.textContent = "제외 " + excluded.size + "개 · 포함되는 문서 " + includedCount(data.tree) + "개" + (dirty ? " · 저장하지 않은 변경이 있습니다" : "");
      set(container, 
        h("div", {class: "tree", role: "tree", "aria-label": "하위 폴더"}, h("ul", {}, nodeView(data.tree))),
        data.truncated ? h("p", {class: "small muted"}, "폴더가 많아 일부만 표시했습니다.") : null);
    }

    saveBtn.addEventListener("click", () => busy(saveBtn, async () => {
      await api("/api/folders/excluded", "POST", {root, excluded: [...excluded]});
      toast("하위 폴더 설정을 저장했습니다. 다시 점검합니다.", "success");
      FS.open = null;
      await refreshAll();
      renderRoute(true);
    }));
    draw();
    set(box, h("div", {class: "card-section stack-sm"},
      h("div", {class: "row-between"}, h("div", {}, h("h3", {}, "포함할 하위 폴더"),
        h("p", {class: "small muted"}, "기본은 전부 포함입니다. 체크를 해제한 폴더와 그 안의 폴더는 읽지 않습니다. .git, node_modules 같은 폴더는 제외 규칙으로 이미 빠져 있습니다.")),
      h("div", {class: "btn-group"}, button("취소", {variant: "ghost", onClick: () => { FS.open = null; renderRoute(true); }}), saveBtn)),
      container, summary));
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
    modal({title: "폴더 경로 입력", icon: "folder",
      body: [field("폴더 경로", path, "선택한 폴더와 그 안의 모든 폴더를 포함합니다.")],
      actions: [{label: "취소"}, {label: "미리보기", variant: "primary", onClick: () => {
        if (!path.value.trim()) throw new Error("경로를 입력하세요.");
        openPreview(path.value.trim(), onAdded);
      }}]});
  }

  function previewBody(p) {
    if (!p.ok) {
      return [callout("danger", "x-circle", "이 폴더는 추가할 수 없습니다", p.reason),
        h("p", {class: "small muted"}, "드라이브 루트, 사용자 폴더 전체, 시스템 폴더, 이 앱의 데이터 폴더는 개인정보 보호를 위해 등록할 수 없습니다. 프로젝트 폴더를 골라 주세요.")];
    }
    const rel = p.relation;
    const unsupported = Object.values(p.unsupported).reduce((a, b) => a + b, 0);
    const skipped = Object.values(p.skipped).reduce((a, b) => a + b, 0);
    const out = [h("div", {class: "folder-top"}, h("div", {class: "folder-icon"}, icon("folder", "lg")),
      h("div", {class: "stack-sm", style: {minWidth: "0", gap: "2px"}}, h("strong", {}, folderName(p.path)), h("span", {class: "folder-path"}, p.path)))];
    if (rel.kind === "contained") out.push(callout("danger", "x-circle", "이미 포함되어 있습니다", "등록된 폴더 " + rel.folders[0] + " 안에 있어 이미 읽고 있습니다."));
    else if (rel.kind === "duplicate") out.push(callout("info", "info", "이미 등록된 폴더입니다", null));
    else if (rel.kind === "parent") out.push(callout("info", "folder", "이미 등록된 폴더를 포함하는 상위 폴더입니다",
      h("div", {class: "stack-sm"}, h("p", {}, "합치면 아래 폴더가 이 폴더 하나로 바뀝니다. 기존에 제외한 하위 폴더 설정은 유지됩니다."),
        h("ul", {}, ...rel.folders.map(f => h("li", {class: "mono"}, f))))));
    out.push(callout("neutral", "info", null, "선택한 폴더와 그 안의 모든 하위 폴더(" + p.subfolders + "개)를 포함합니다. 필요 없는 하위 폴더는 추가한 뒤 뺄 수 있습니다."));
    out.push(h("div", {class: "stats"}, stat(p.readable, "읽을 수 있는 파일"), stat(p.subfolders, "하위 폴더"),
      stat(unsupported, "지원하지 않는 파일"), stat(skipped, "건너뛰는 파일")));
    const formats = Object.entries(p.formats);
    if (formats.length) out.push(h("div", {class: "stack-sm"}, h("h4", {}, "형식별 파일 수"),
      h("div", {class: "row"}, ...formats.map(([ext, n]) => h("span", {class: "chip"}, "." + ext + " " + n)))));
    if (unsupported) out.push(h("div", {class: "stack-sm"}, h("h4", {}, "읽지 않는 형식"),
      h("div", {class: "row"}, ...Object.entries(p.unsupported).map(([ext, n]) => h("span", {class: "chip"}, "." + ext + " " + n))),
      h("p", {class: "small muted"}, "hwp, doc, xls, ppt는 hwpx, docx, xlsx, pptx로 저장하면 읽을 수 있습니다.")));
    if (p.excluded_dirs.length) out.push(h("div", {class: "stack-sm"}, h("h4", {}, "자동으로 제외되는 폴더"),
      h("div", {class: "row"}, ...p.excluded_dirs.slice(0, 12).map(d => h("span", {class: "chip mono"}, d)))));
    if (skipped) out.push(h("details", {}, h("summary", {class: "small"}, "건너뛰는 파일 이유 보기"),
      h("ul", {class: "small muted"}, ...Object.entries(p.skipped).map(([why, n]) => h("li", {}, why + ": " + n + "개")))));
    for (const w of p.warnings) out.push(callout("warning", "alert", null, w));
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

  async function openPreview(path, onAdded) {
    const body = h("div", {class: "stack"}, skeleton(5));
    const close = modal({title: "폴더 추가 미리보기", icon: "folder-plus", wide: true, body, actions: [{label: "취소"}]});
    let p;
    try { p = await api("/api/folders/preview", "POST", {path}); } catch (e) {
      set(body, errorState(e.message, () => { close(); openPreview(path, onAdded); }));
      return;
    }
    set(body, ...previewBody(p));
    const blocked = !p.ok || p.relation.kind === "contained" || p.relation.kind === "duplicate";
    close.setActions([
      {label: "다른 폴더 선택", icon: "folder", onClick: () => { close(); startAddFolder(onAdded); }},
      {label: p.ok && p.relation.kind === "parent" ? "합쳐서 추가" : "이 폴더 추가", variant: "primary", icon: "check", disabled: blocked,
        onClick: async () => {
          const added = await addFolder(p.path, p.relation.kind === "parent");
          if (!added) return false;
          if (onAdded) onAdded(p.path);
          await refreshAll();
          if (parseHash().name === "folders") renderRoute(true);
          return true;
        }},
    ]);
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
    if (parseHash().name === "notes" && e.dataTransfer.files.length && e.dataTransfer.files[0].size > 0) {
      importFile(e.dataTransfer.files[0]);
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

  ROUTES.accounts = {
    title: "연결 계정", icon: "link", auto: false,
    lead: "메일, 캘린더, 협업 도구의 지정한 범위만 읽어 폴더 문서와 함께 점검합니다.",
    async load() {
      const [rows, settings] = await Promise.all([api("/api/connectors"), api("/api/settings")]);
      return {rows, settings};
    },
    render({rows, settings}) {
      const target = select(settings.allowed_folders.map(f => [f, f]), settings.connector_target || settings.allowed_folders[0] || "", {"aria-label": "분석 대상 폴더"});
      const saveTarget = button("저장", {});
      saveTarget.addEventListener("click", () => busy(saveTarget, async () => {
        await api("/api/settings", "POST", {connector_target: target.value});
        toast("분석 대상 폴더를 저장했습니다.", "success");
      }));
      const intro = h("section", {class: "card stack"},
        callout("success", "shield-check", "모든 연결은 읽기 전용입니다",
          "보내기, 삭제, 읽음 표시, 만들기 기능은 없습니다. 토큰과 비밀번호는 Windows 자격 증명 관리자에만 저장되고, 화면에서는 저장 여부만 보입니다. 이 앱에는 서버나 회원 가입이 없어 모든 정보는 이 컴퓨터에만 남습니다."),
        settings.allowed_folders.length === 0
          ? callout("warning", "folder", "먼저 폴더를 추가하세요", "가져온 메일과 일정은 감시 폴더의 문서와 함께 분석됩니다.", [button("폴더 추가", {size: "sm", onClick: () => startAddFolder()})])
          : h("div", {class: "row"}, h("span", {class: "strong"}, "가져온 내용을 함께 분석할 폴더"), h("div", {style: {flex: "1 1 280px"}}, target), saveTarget));
      return page(this.title, this.lead, null, intro, h("div", {class: "svc-grid"}, ...rows.map(serviceCard)));
    },
  };

  function serviceLogo(id) {
    const [iconName, color] = SERVICE_LOOK[id] || ["link", "#475467"];
    return h("div", {class: "svc-logo", style: {background: color}, "aria-hidden": "true"}, icon(iconName, "lg"));
  }

  function serviceCard(c) {
    const [kind, iconName, text] = connState(c);
    const st = c.status;
    const configured = c.problems.length === 0;
    const run = button("지금 가져오기", {icon: "refresh", disabled: !configured});
    run.addEventListener("click", () => busy(run, async () => {
      await api("/api/connectors/run", "POST", {id: c.id});
      toast(c.label + "에서 가져오기를 시작했습니다.", "info");
    }));
    const disconnect = button("연결 해제", {variant: "danger-outline", icon: "x"});
    disconnect.addEventListener("click", async () => {
      const ok = await confirmDialog({title: c.label + " 연결을 해제할까요?", danger: true, confirmLabel: "연결 해제",
        message: "저장된 토큰과 비밀번호, 가져온 사본, 상태가 모두 지워집니다. 서비스 쪽 데이터는 바뀌지 않습니다."});
      if (!ok) return;
      await busy(disconnect, async () => {
        await api("/api/connectors/disconnect", "POST", {id: c.id});
        toast("연결을 해제했습니다.", "success");
        renderRoute(true);
      });
    });
    return h("section", {class: "card svc-card"},
      h("div", {class: "svc-top"}, serviceLogo(c.id),
        h("div", {class: "svc-meta"}, h("div", {class: "row-between"}, h("h3", {}, c.label), badge(kind, iconName, text)),
          h("span", {class: "small muted"}, c.reads))),
      h("div", {class: "small " + (st && !st.ok ? "" : "muted"), style: st && !st.ok ? {color: "var(--danger)"} : null},
        st ? (st.ok ? "마지막 가져오기 " + relTime(st.at) + " · " + st.summary : "마지막 시도 " + relTime(st.at) + " · " + st.message)
          : "아직 가져온 적이 없습니다."),
      h("div", {class: "btn-group"},
        button(configured ? "설정" : "연결", {variant: configured ? "" : "primary", icon: configured ? "settings" : "link", onClick: () => connectWizard(c)}),
        configured ? run : null, configured || c.enabled ? disconnect : null),
      h("details", {}, h("summary", {}, icon("chevron-right", "sm"), "이 앱이 읽는 것 / 절대 하지 않는 것"),
        h("div", {class: "does"},
          h("div", {}, h("h4", {}, "읽는 것"), h("ul", {}, h("li", {}, c.reads))),
          h("div", {}, h("h4", {}, "절대 하지 않는 것"), h("ul", {}, ...NEVER.map(n => h("li", {}, n)))))));
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

  function connectWizard(c) {
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
    const close = modal({title: c.label + " 연결", wide: true, body: [stepper, body], actions: []});

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

  ROUTES.notes = {
    title: "메모와 가져오기", icon: "note", auto: false,
    lead: "회의 중에 정한 내용을 바로 적거나, 감시 폴더 밖의 파일을 한 번 읽어 점검에 포함합니다.",
    async load() { return api("/api/notes"); },
    render(notes) {
      const text = textarea({rows: "3", placeholder: "예: 발표는 다음 주 월요일로 연기한다"});
      const date = input({type: "date", "aria-label": "날짜"});
      const add = button("메모 추가", {variant: "primary", icon: "plus"});
      add.addEventListener("click", () => busy(add, async () => {
        if (text.value.trim().length < 2) throw new Error("메모 내용을 입력하세요.");
        await api("/api/notes", "POST", {text: text.value.trim(), date: date.value || null});
        toast("메모를 저장했고 다음 점검에 포함합니다.", "success");
        renderRoute(true);
      }));
      const file = h("input", {type: "file", class: "input", "aria-label": "가져올 파일"});
      const imp = button("가져오기", {variant: "primary", icon: "upload"});
      imp.addEventListener("click", () => busy(imp, async () => { await importFile(file.files[0]); file.value = ""; }));
      return page(this.title, this.lead, null,
        card("결정 메모", "파일에 쓰기 전에도 점검에 포함됩니다. 메모는 이 컴퓨터의 앱 데이터에만 저장됩니다.", null,
          h("div", {class: "stack"}, field("내용", text), h("div", {class: "row-between"}, h("div", {style: {width: "220px"}}, field("날짜 (비우면 오늘)", date)), add)),
          h("div", {class: "card-section"},
            notes.length === 0 ? emptyState({icon: "note", title: "메모가 없습니다", text: "회의 중 단축키(기본 Ctrl+Alt+D)로 빠른 점검 창을 열어도 메모를 남길 수 있습니다.", neutral: true})
              : h("div", {class: "list"}, ...notes.map(n => h("div", {class: "list-item"},
                h("span", {class: "chip"}, n.date), h("div", {class: "list-main"}, n.text),
                button("", {variant: "ghost", size: "sm", icon: "trash", title: "메모 삭제", onClick: async () => {
                  if (!await confirmDialog({title: "메모를 삭제할까요?", message: n.text, confirmLabel: "삭제", danger: true})) return;
                  await api("/api/notes/" + encodeURIComponent(n.id), "DELETE");
                  renderRoute(true);
                }})))))),
        card("파일 가져오기", "md, docx, xlsx, pdf, pptx, ics 등. 원본은 수정하지 않고 파일 자체도 저장하지 않으며, 읽은 텍스트만 앱 데이터에 남습니다.", null,
          h("div", {class: "dropzone"}, h("div", {class: "folder-icon"}, icon("upload")),
            h("div", {class: "stack-sm", style: {flex: "1 1 auto"}}, h("strong", {}, "파일을 이 화면에 끌어다 놓거나 선택하세요"), file), imp)));
    },
  };

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
          : callout("warning", "alert", "데모 판정기로만 점검했습니다. 실제 검사가 아닙니다.", fmtTime(c.at) + " · 파일 " + c.files_ok + "개, 결정 " + c.topics + "건"))
          : callout("neutral", "clock", "아직 완료된 점검이 없습니다", null),
        judges ? h("p", {class: "small muted"}, "판정기별 결정 수: " + judges) : null,
        f.scan_stats ? h("p", {class: "small muted"}, "폴더 안 파일 " + f.scan_stats.seen + "개 확인" + (skipped ? " · 건너뜀: " + skipped : "")) : null,
        fail ? callout("danger", "x-circle", "마지막 문제 (" + fmtTime(fail.at) + ")", fail.message) : null));
    }));
  }

  // ---------------------------------------------------------------- settings

  ROUTES.settings = {
    title: "설정", icon: "settings", auto: false,
    lead: "점검 동작, 제외 규칙, 시작 방법, 접근 범위를 정합니다.",
    async load() {
      const [s, access, auto] = await Promise.all([api("/api/settings"), api("/api/access"), api("/api/autostart")]);
      return {s, access, auto};
    },
    render({s, access, auto}, params) {
      const save = (payload, message) => api("/api/settings", "POST", payload).then(() => toast(message || "설정을 저장했습니다.", "success"));
      const saveBtn = (fn) => { const b = button("저장", {variant: "primary"}); b.addEventListener("click", () => busy(b, fn)); return b; };

      const mode = select([["agent", "Agent: 상황을 보고 다음 단계를 고름"], ["pipeline", "Pipeline: 고정 순서"]], s.analysis_mode);
      const debounce = input({type: "number", min: "0", value: String(s.debounce_seconds)});
      const rescan = input({type: "number", min: "0", value: String(s.rescan_minutes)});
      const release = input({value: s.current_release || "", placeholder: "예: MVP"});
      const maxMb = input({type: "number", min: "1", value: String(s.max_file_mb)});
      const maxCalls = input({type: "number", min: "1", value: String(s.max_llm_calls_per_cycle)});
      const behaviour = card("점검 동작", null, null, h("div", {class: "stack"},
        h("div", {class: "field-row"},
          field("점검 방식", mode), field("현재 릴리스", release, "결정의 적용 범위를 판단할 때 씁니다."),
          field("변경 후 대기(초)", debounce, "저장이 이어질 때 모아서 한 번에 점검합니다."),
          field("전체 다시 점검 간격(분)", rescan, "0이면 변경이 있을 때만 점검합니다."),
          field("파일 최대 크기(MB)", maxMb), field("점검 1회 LLM 호출 한도", maxCalls)),
        h("div", {}, saveBtn(() => save({analysis_mode: mode.value, debounce_seconds: Number(debounce.value || 0),
          rescan_minutes: Number(rescan.value || 0), current_release: release.value.trim() || null,
          max_file_mb: Number(maxMb.value || 25), max_llm_calls_per_cycle: Number(maxCalls.value || 400)})))));

      const excludes = textarea({rows: "8", class: "textarea mono"});
      excludes.value = s.exclude_patterns.join("\n");
      const rules = card("제외 규칙", "모든 감시 폴더에 적용되는 이름 패턴입니다. 특정 하위 폴더만 빼려면 폴더 화면의 '하위 폴더 선택'을 쓰세요.", null,
        h("div", {class: "stack"}, field("제외 패턴 (한 줄에 하나)", excludes, "예: node_modules, *.tmp, 보관함/*"),
          h("div", {}, saveBtn(() => save({exclude_patterns: excludes.value.split("\n").map(x => x.trim()).filter(Boolean)})))));

      const target = select(s.allowed_folders.map(f => [f, f]), s.connector_target || s.allowed_folders[0] || "");
      const interval = input({type: "number", min: "1", value: String(s.connector_interval_minutes)});
      const imports = card("연결 가져오기", null, null, h("div", {class: "stack"},
        h("div", {class: "field-row"}, field("가져온 내용을 분석할 폴더", target), field("가져오기 간격(분)", interval)),
        h("div", {}, saveBtn(() => save(Object.assign({connector_interval_minutes: Number(interval.value || 15)},
          s.allowed_folders.length ? {connector_target: target.value} : {}))))));

      const autoToggle = toggle("Windows 시작 시 자동 실행", auto.enabled, auto.supported ? "로그인하면 조용히 시작해 트레이에서 감시합니다." : "Windows에서만 지원합니다.");
      autoToggle.input.disabled = !auto.supported;
      autoToggle.input.addEventListener("change", async () => {
        try {
          await api("/api/autostart", "POST", {enabled: autoToggle.input.checked});
          toast(autoToggle.input.checked ? "자동 실행을 켰습니다." : "자동 실행을 껐습니다.", "success");
        } catch (e) { autoToggle.input.checked = !autoToggle.input.checked; toast(e.message, "danger"); }
      });
      const startup = card("시작", null, null, autoToggle);

      const ollama = input({value: s.ollama_url, class: "input mono"});
      const embed = input({value: s.embedding_model, placeholder: "예: nomic-embed-text"});
      const advanced = card("고급 판정", "Ollama 같은 로컬 모델을 쓸 때만 바꾸세요.", null, h("div", {class: "stack"},
        h("div", {class: "field-row"}, field("Ollama 주소", ollama, "이 컴퓨터 주소가 아니면 외부 전송 스위치가 켜져 있어야 합니다."),
          field("임베딩 모델 (선택)", embed, "글자 유사도가 놓친 후보를 보충합니다.")),
        h("div", {}, saveBtn(() => save({ollama_url: ollama.value.trim(), embedding_model: embed.value.trim()})))));

      const list = (items) => h("ul", {}, ...items.map(i => h("li", {}, i)));
      const scope = card("접근 범위", "이 프로그램이 읽는 곳과 절대 하지 않는 일입니다.", null, h("div", {class: "stack"},
        h("div", {class: "stack-sm"}, h("h4", {}, "읽는 폴더"), access.folders.length === 0 ? h("p", {class: "muted"}, "등록된 폴더가 없습니다.")
          : list(access.folders.map(f => f.root + " (읽은 파일 " + (f.files.ok || 0) + "개, 가져온 외부 문서 " + f.external_documents + "개)"))),
        h("div", {class: "stack-sm"}, h("h4", {}, "켜 둔 연결"), access.connectors.length === 0 ? h("p", {class: "muted"}, "켜 둔 연결이 없습니다.")
          : list(access.connectors.map(c => c.label + ": " + c.reads))),
        h("div", {class: "stack-sm"}, h("h4", {}, "문서 내용이 나가는 곳"),
          access.provider.sends_externally ? callout("danger", "cloud", "외부 서비스로 전송됩니다: " + access.provider.id, access.provider.reason)
            : callout("success", "shield-check", "이 컴퓨터 밖으로 나가지 않습니다", access.provider.reason)),
        h("div", {class: "stack-sm"}, h("h4", {}, "절대 하지 않는 일"), list(access.never)),
        h("div", {class: "stack-sm"}, h("h4", {}, "컴퓨터에 남는 것"), list(access.stored_locally),
          h("p", {class: "small muted mono"}, "데이터 폴더: " + access.storage.app_home))));
      scope.id = "section-access";

      const sections = [["behaviour", "점검 동작", behaviour], ["rules", "제외 규칙", rules], ["imports", "연결 가져오기", imports],
        ["startup", "시작", startup], ["advanced", "고급 판정", advanced], ["access", "접근 범위", scope]];
      sections.forEach(([id, , node]) => { node.id = "section-" + id; });
      const jump = (id) => { const el = document.getElementById("section-" + id); if (el) el.scrollIntoView({behavior: "smooth", block: "start"}); };
      const navBox = h("nav", {class: "settings-nav", "aria-label": "설정 항목"}, ...sections.map(([id, label]) =>
        h("button", {type: "button", class: "nav-item", onclick: () => jump(id)}, h("span", {class: "nav-label"}, label))));
      const node = page(this.title, this.lead, null, h("div", {class: "settings-layout"}, navBox, h("div", {class: "stack"}, ...sections.map(x => x[2]))));
      if (params.get("section")) setTimeout(() => jump(params.get("section")), 60);
      return node;
    },
  };

  // ---------------------------------------------------------------- profile

  const COLORS = ["#4f46e5", "#0891b2", "#059669", "#d97706", "#dc2626", "#db2777", "#7c3aed", "#475569"];
  const PROVIDERS = [
    ["auto", "자동", "API 키, 모델 이름, 외부 전송 스위치가 모두 있으면 Anthropic, 아니면 데모 판정기", "cpu"],
    ["mock", "데모 판정기", "내장 규칙. 예시 문서만 알며 실제 검사가 아닙니다. 외부 전송 없음", "alert"],
    ["anthropic", "Anthropic", "문서 내용이 Anthropic API로 전송됩니다. 키, 모델, 전송 스위치가 필요합니다", "cloud"],
    ["ollama", "Ollama (로컬)", "이 컴퓨터에서 도는 모델. 문서가 밖으로 나가지 않습니다", "monitor"],
  ];

  function providerChoices(value, onChange) {
    let current = value;
    const name = "provider-" + Math.random().toString(36).slice(2, 6);
    const grid = h("div", {class: "choice-grid", role: "radiogroup", "aria-label": "판정 방식"});
    const cards = PROVIDERS.map(([v, label, help, iconName]) => {
      const radio = h("input", {type: "radio", name, value: v, checked: v === value});
      const node = h("label", {class: "choice" + (v === value ? " selected" : "")}, radio,
        h("span", {class: "choice-title"}, icon(iconName, "sm"), label), h("span", {class: "small muted"}, help));
      radio.addEventListener("change", () => {
        current = v;
        cards.forEach(c => c.classList.toggle("selected", c === node));
        if (onChange) onChange(v);
      });
      return node;
    });
    DA.append(grid, cards);
    grid.value = () => current;
    return grid;
  }

  function apiKeyBlock(hasKey, onChange) {
    const keyInput = input({type: "password", autocomplete: "off", placeholder: hasKey ? "저장됨 - 바꾸려면 새 키 입력" : "sk-ant-..."});
    const state = h("span");
    const drawState = (saved) => set(state, saved ? badge("success", "lock", "저장됨") : badge("neutral", "minus", "저장 안 됨"));
    drawState(hasKey);
    const saveKey = button("키 저장", {icon: "key"});
    saveKey.addEventListener("click", () => busy(saveKey, async () => {
      if (keyInput.value.trim().length < 8) throw new Error("API 키를 입력하세요.");
      await api("/api/secret/api-key", "POST", {value: keyInput.value.trim()});
      keyInput.value = "";
      keyInput.placeholder = "저장됨 - 바꾸려면 새 키 입력";
      drawState(true);
      toast("API 키를 자격 증명 관리자에 저장했습니다.", "success");
      if (onChange) onChange(true);
    }));
    const delKey = button("키 삭제", {variant: "danger-outline", icon: "trash"});
    delKey.addEventListener("click", () => busy(delKey, async () => {
      await api("/api/secret/api-key", "DELETE");
      drawState(false);
      toast("API 키를 삭제했습니다.", "success");
      if (onChange) onChange(false);
    }));
    return h("div", {class: "stack-sm"},
      h("div", {class: "row"}, h("span", {class: "field-label"}, "Anthropic API 키"), state),
      h("div", {class: "row", style: {flexWrap: "nowrap"}}, keyInput, saveKey, delKey),
      h("span", {class: "field-help"}, "키는 Windows 자격 증명 관리자에만 저장되고 화면, 설정 파일, 로그에 나타나지 않습니다."));
  }

  ROUTES.profile = {
    title: "내 정보", icon: "user", auto: false,
    lead: "이 컴퓨터에만 저장되는 로컬 프로필과 판정 방식, 알림, 데이터 관리입니다. 서버나 회원 가입은 없습니다.",
    async load() {
      const [s, access] = await Promise.all([api("/api/settings"), api("/api/access")]);
      return {s, access, st: S.status};
    },
    render({s, access, st}, params) {
      // profile
      const name = input({value: s.display_name, maxlength: "40", placeholder: "표시 이름"});
      let color = s.profile_color;
      const preview = h("span", {class: "avatar lg", style: {background: color}}, (s.display_name || "").trim() ? Array.from(s.display_name.trim())[0] : icon("user", "lg"));
      const swatches = h("div", {class: "swatches", role: "radiogroup", "aria-label": "프로필 색상"}, ...COLORS.map(cv => {
        const sw = h("button", {type: "button", class: "swatch", role: "radio", "aria-checked": String(cv === color), "aria-label": "색상 " + cv,
          style: {background: cv}, onclick: () => {
            color = cv;
            swatches.querySelectorAll(".swatch").forEach(x => x.setAttribute("aria-checked", String(x === sw)));
            preview.style.background = cv;
          }});
        return sw;
      }));
      name.addEventListener("input", () => { const t = name.value.trim(); set(preview, t ? Array.from(t)[0] : icon("user", "lg")); });
      const saveProfile = button("저장", {variant: "primary"});
      saveProfile.addEventListener("click", () => busy(saveProfile, async () => {
        await api("/api/settings", "POST", {display_name: name.value, profile_color: color});
        S.settings = await api("/api/settings");
        toast("프로필을 저장했습니다.", "success");
      }));
      const profile = card("프로필", null, null, h("div", {class: "row", style: {gap: "24px", alignItems: "flex-start"}}, preview,
        h("div", {class: "stack", style: {flex: "1 1 320px"}}, field("표시 이름", name), h("div", {class: "field"}, h("span", {class: "field-label"}, "프로필 색상"), swatches),
          callout("neutral", "lock", null, "이 정보는 이 컴퓨터의 설정 파일에만 저장됩니다. 계정이나 서버로 보내지 않습니다."), h("div", {}, saveProfile))));

      // judge
      let provider = s.provider;
      const extFields = h("div", {class: "stack"});
      const model = input({value: s.model_name, placeholder: "사용할 모델 이름"});
      const ext = toggle("외부 LLM으로 문서 내용 전송 허용", s.allow_external_send, "켜면 점검할 때 문서 내용이 Anthropic API로 전송됩니다. 기본은 꺼짐입니다.");
      const ollamaUrl = input({value: s.ollama_url, class: "input mono"});
      const keyBlock = apiKeyBlock(s.has_api_key);
      const drawExt = () => {
        set(extFields, 
          provider === "anthropic" || provider === "auto" ? keyBlock : null,
          provider !== "mock" ? field("모델 이름", model, provider === "ollama" ? "예: qwen2.5:7b" : "Anthropic 모델 이름. 코드에 들어 있지 않으며 직접 입력합니다.") : null,
          provider === "ollama" ? field("Ollama 주소", ollamaUrl) : null,
          provider === "anthropic" || provider === "auto" ? ext : null,
          provider === "anthropic" || provider === "auto" ? callout(ext.input.checked ? "danger" : "neutral", ext.input.checked ? "cloud" : "shield-check", null,
            ext.input.checked ? "외부 전송이 켜져 있습니다. 키와 모델 이름이 있으면 문서 내용이 외부로 나갑니다. 민감한 문서는 로컬 모델을 쓰세요."
              : "외부 전송이 꺼져 있어 문서가 외부로 나가지 않습니다. 키와 모델이 있어도 전송 스위치를 켜기 전까지는 데모 판정기로 동작합니다.") : null);
      };
      ext.input.addEventListener("change", drawExt);
      const choices = providerChoices(provider, (v) => { provider = v; drawExt(); });
      drawExt();
      const testResult = h("div");
      const saveJudge = button("판정 방식 저장", {variant: "primary"});
      saveJudge.addEventListener("click", () => busy(saveJudge, async () => {
        await api("/api/settings", "POST", {provider, model_name: model.value.trim(), allow_external_send: ext.input.checked,
          ollama_url: ollamaUrl.value.trim()});
        toast("판정 방식을 저장했습니다. 새 방식으로 다시 점검합니다.", "success");
        await refreshAll();
        renderRoute(true);
      }));
      const test = button("연결 테스트", {icon: "zap"});
      test.addEventListener("click", () => busy(test, async () => {
        const r = await api("/api/provider/test", "POST");
        set(testResult, callout(r.ok ? "success" : "danger", r.ok ? "check-circle" : "x-circle", r.ok ? "연결에 성공했습니다" : "연결하지 못했습니다", r.message));
      }));
      const now = st ? st.provider : null;
      const judge = card("판정 방식", "실제 모델이 끝까지 점검했을 때만 '검사 완료'로 표시합니다.", null, h("div", {class: "stack"},
        now ? callout(now.sends_externally ? "danger" : (now.local_only ? "warning" : "success"), now.sends_externally ? "cloud" : (now.local_only ? "alert" : "shield-check"),
          "현재: " + providerName(now.id), now.reason) : null,
        choices, extFields, h("div", {class: "row"}, saveJudge, test, h("span", {class: "small muted"}, "저장한 뒤 연결 테스트를 눌러 보세요.")), testResult));
      judge.id = "section-judge";

      // notifications and shortcut
      const notify = toggle("알림 사용", s.notify, "새 불일치, 확인 질문, 점검 완료를 Windows 알림으로 알립니다.");
      const digestOn = toggle("일일 요약", s.digest_enabled, "정한 시각 이후 하루 한 번 요약을 알립니다.");
      const digestTime = input({type: "time", value: s.digest_time, "aria-label": "요약 시각"});
      const hotOn = toggle("전역 단축키로 빠른 점검 열기", s.hotkey_enabled, "회의 중에 작은 창을 바로 엽니다.");
      const hotkey = input({value: s.hotkey, class: "input mono", "aria-label": "단축키"});
      const hk = st && st.hotkey;
      const saveNotify = button("저장", {variant: "primary"});
      saveNotify.addEventListener("click", () => busy(saveNotify, async () => {
        await api("/api/settings", "POST", {notify: notify.input.checked, digest_enabled: digestOn.input.checked,
          digest_time: digestTime.value, hotkey_enabled: hotOn.input.checked, hotkey: hotkey.value.trim()});
        toast("알림과 단축키를 저장했습니다.", "success");
        await refreshAll();
      }));
      const alerts = card("알림과 단축키", null, null, h("div", {class: "stack"}, notify,
        h("div", {class: "row-between"}, digestOn, h("div", {style: {width: "160px"}}, field("요약 시각", digestTime))),
        h("div", {class: "row-between"}, hotOn, h("div", {class: "row"}, h("div", {style: {width: "180px"}}, field("단축키", hotkey, "예: ctrl+alt+d")),
          hk && hk.enabled ? (hk.active ? badge("success", "keyboard", "등록됨") : badge("warning", "alert", hk.reason || "등록 안 됨")) : null)),
        h("div", {}, saveNotify)));

      // appearance
      const theme = segmented([["system", "시스템", "monitor"], ["light", "라이트", "sun"], ["dark", "다크", "moon"]], s.theme, (v) => saveTheme(v), "테마");
      const lang = select([["ko", "한국어"]], s.language, {"aria-label": "언어"});
      const look = card("화면", null, null, h("div", {class: "field-row"},
        h("div", {class: "field"}, h("span", {class: "field-label"}, "테마"), theme), field("언어", lang, "현재는 한국어만 지원합니다.")));

      // data
      const diag = button("진단 정보 복사", {icon: "copy"});
      diag.addEventListener("click", () => busy(diag, async () => copyText((await api("/api/diagnostics")).text)));
      const purge = button("모든 앱 데이터 삭제", {variant: "danger", icon: "trash", onClick: purgeFlow});
      const data = card("데이터", "모든 데이터는 이 컴퓨터에만 있습니다.", null, h("div", {class: "stack"},
        h("dl", {class: "kv"}, h("dt", {}, "데이터 폴더"), h("dd", {class: "mono"}, access.storage.app_home),
          h("dt", {}, "로그 파일"), h("dd", {class: "mono"}, access.storage.log)),
        h("div", {class: "btn-group"}, button("데이터 폴더 열기", {icon: "external", onClick: () => openLocation("data")}),
          button("로그 폴더 열기", {icon: "terminal", onClick: () => openLocation("logs")}), diag),
        h("p", {class: "small muted"}, "진단 정보에는 버전, 판정 방식, 폴더 수와 상태가 들어가며 API 키, 토큰, 문서 내용은 들어가지 않습니다."),
        h("div", {class: "card-section"}, h("div", {class: "callout danger"}, icon("alert"),
          h("div", {class: "callout-body"}, h("strong", {}, "모든 앱 데이터 삭제"),
            h("p", {}, "API 키와 연결 비밀 값, 점검 결과, 읽은 내용의 사본, 설정과 메모를 지웁니다. 감시 폴더의 원본 파일은 건드리지 않습니다."),
            h("div", {style: {marginTop: "8px"}}, purge))))));

      const node = page(this.title, this.lead, null, profile, judge, alerts, look, data);
      if (params.get("section")) setTimeout(() => { const el = document.getElementById("section-" + params.get("section")); if (el) el.scrollIntoView({block: "start"}); }, 60);
      return node;
    },
  };

  function purgeFlow() {
    modal({title: "모든 앱 데이터를 삭제할까요?", icon: "alert",
      body: h("div", {class: "stack-sm"}, h("p", {}, "다음 항목이 이 컴퓨터에서 영구히 삭제됩니다."),
        h("ul", {}, h("li", {}, "API 키와 모든 연결의 토큰, 비밀번호 (자격 증명 관리자)"), h("li", {}, "점검 결과와 읽은 내용의 사본 (상태 DB)"),
          h("li", {}, "설정, 메모, 일일 요약, 창 위치")),
        callout("success", "shield-check", null, "감시 폴더의 원본 파일은 읽기만 했으므로 삭제하거나 바꾸지 않습니다.")),
      actions: [{label: "취소"}, {label: "계속", variant: "danger", onClick: () => { setTimeout(purgeConfirm, 0); }}]});
  }

  function purgeConfirm() {
    const confirmInput = input({placeholder: PURGE_CONFIRM, autofocus: true, "aria-label": "확인 문구"});
    const close = modal({title: "마지막 확인", icon: "trash",
      body: [h("p", {}, "되돌릴 수 없습니다. 계속하려면 '" + PURGE_CONFIRM + "'를 입력하세요."), confirmInput],
      actions: [{label: "취소"}, {label: "영구 삭제", variant: "danger", onClick: async () => {
        if (confirmInput.value.trim() !== PURGE_CONFIRM) throw new Error("'" + PURGE_CONFIRM + "'를 정확히 입력하세요.");
        await api("/api/data/purge", "POST", {confirm: confirmInput.value.trim()});
        toast("모든 앱 데이터를 삭제했습니다.", "success");
        try { localStorage.clear(); } catch (e) { /* ignore */ }
        S.settings = await api("/api/settings");
        applyTheme(S.settings.theme);
        await tick();
        go("home");
      }}]});
    return close;
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
    window.addEventListener("hashchange", () => { closePopover(); renderRoute(true); document.getElementById("view").focus({preventScroll: true}); });
    setInterval(loop, REFRESH_MS);
  }

  DA.initTheme();
  boot();
})();
