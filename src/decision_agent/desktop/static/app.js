/* Dashboard: layout, router, and every screen. Uses the helpers in common.js. */
"use strict";

(() => {
  const {h, set, icon, api, button, busy, badge, field, input, textarea, select, toggle, segmented, callout,
    progressBar, skeleton, emptyState, errorState, toast, modal, confirmDialog, drawer, anyLayerOpen,
    copyText, applyTheme, fmtTime, relTime, stateInfo, stateBadge, transmissionBadge, labelBadge, KINDS,
    providerName} = DA;

  const S = {status: null, settings: null, offline: false};
  const REFRESH_MS = 4000;
  const PURGE_CONFIRM = "모두 삭제";

  // ---------------------------------------------------------------- routing

  const NAV = [
    {group: "점검", items: ["home", "results", "questions"]},
    {group: "자료", items: ["folders", "accounts", "notes"]},
    {group: "기록", items: ["digest", "activity"]},
  ];
  const FOOT = ["settings", "profile"];
  const ROUTES = {};

  function parseHash() {
    const raw = (location.hash || "#/home").slice(2);
    const [name, query] = raw.split("?");
    return {name: ROUTES[name] ? name : "home", params: new URLSearchParams(query || "")};
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

  function renderNav() {
    const nav = document.getElementById("nav");
    const current = parseHash().name;
    const st = S.status;
    const counts = {
      results: st ? st.inconsistent : 0,
      questions: st ? st.questions : 0,
    };
    const item = (name) => {
      const r = ROUTES[name];
      const count = counts[name] || 0;
      return h("a", {class: "nav-item", href: "#/" + name, "aria-current": name === current ? "page" : null,
        title: r.title},
      icon(r.icon), h("span", {class: "nav-label"}, r.title),
      count ? h("span", {class: "count-pill", "aria-label": count + "건"}, String(count)) : null,
      count ? h("span", {class: "dot", "aria-hidden": "true"}) : null);
    };
    set(nav, ...NAV.flatMap(g => [h("div", {class: "nav-group-label"}, g.group), ...g.items.map(item)]));
    const foot = document.getElementById("sidebar-foot");
    const collapsed = document.getElementById("app").classList.contains("collapsed");
    set(foot, ...FOOT.map(item),
      h("button", {type: "button", class: "nav-item", title: collapsed ? "메뉴 펼치기" : "메뉴 접기",
        "aria-label": collapsed ? "메뉴 펼치기" : "메뉴 접기", "aria-expanded": String(!collapsed),
        onclick: toggleSidebar}, icon(collapsed ? "chevron-right" : "chevron-left"),
      h("span", {class: "nav-label"}, "메뉴 접기")),
      h("div", {class: "version"}, "버전 " + (st ? st.version : "")));
  }

  function toggleSidebar() {
    const app = document.getElementById("app");
    app.classList.toggle("collapsed");
    try { localStorage.setItem("da.sidebar", app.classList.contains("collapsed") ? "1" : "0"); } catch (e) { /* ignore */ }
    renderNav();
  }

  let openPopover = null;
  function closePopover() { if (openPopover) { openPopover.remove(); openPopover = null; } }
  document.addEventListener("mousedown", (e) => {
    if (openPopover && !openPopover.parentNode.contains(e.target)) closePopover();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") closePopover();
    if ((e.ctrlKey && e.key.toLowerCase() === "k") || (e.key === "/" && !isTyping() && !anyLayerOpen())) {
      const box = document.getElementById("global-search");
      if (box) { e.preventDefault(); box.focus(); box.select(); }
    }
  });

  let topbarBuilt = false;
  const topRefs = {};

  function buildTopbar() {
    const bar = document.getElementById("topbar");
    topRefs.status = h("div", {class: "status-zone", "aria-live": "polite"});
    topRefs.search = h("input", {id: "global-search", class: "input", type: "search", placeholder: "결정, 점검 결과, 파일, 메모 검색",
      "aria-label": "검색", autocomplete: "off"});
    const searchWrap = h("div", {class: "search anchor", role: "search"}, icon("search", "sm"), topRefs.search, h("kbd", {}, "Ctrl K"));
    let timer = null;
    topRefs.search.addEventListener("input", () => {
      clearTimeout(timer);
      timer = setTimeout(() => runSearch(searchWrap, topRefs.search.value.trim()), 220);
    });
    topRefs.search.addEventListener("focus", () => { if (topRefs.search.value.trim()) runSearch(searchWrap, topRefs.search.value.trim()); });
    topRefs.bellDot = h("span", {class: "count-pill bell-dot", hidden: true});
    const bellWrap = h("div", {class: "anchor"});
    const bell = h("button", {type: "button", class: "btn ghost icon-only", "aria-label": "알림", title: "알림",
      onclick: () => toggleBell(bellWrap)}, icon("bell"), topRefs.bellDot);
    bellWrap.appendChild(bell);
    const profileWrap = h("div", {class: "anchor"});
    topRefs.avatar = h("button", {type: "button", class: "avatar-btn", "aria-label": "프로필 메뉴", "aria-haspopup": "menu",
      onclick: () => toggleProfileMenu(profileWrap)});
    profileWrap.appendChild(topRefs.avatar);
    set(bar, topRefs.status, searchWrap, bellWrap, profileWrap);
    topbarBuilt = true;
  }

  function avatar(size) {
    const s = S.settings || {};
    const name = (s.display_name || "").trim();
    return h("span", {class: "avatar" + (size ? " " + size : ""), style: {background: s.profile_color || "#4f46e5"},
      "aria-hidden": "true"}, name ? Array.from(name)[0] : icon("user", "sm"));
  }

  function renderTopbar() {
    if (!topbarBuilt) buildTopbar();
    const st = S.status;
    if (S.offline || !st) {
      set(topRefs.status, badge("danger", "x-circle", "프로그램과 연결이 끊겼습니다", "lg"));
    } else {
      set(topRefs.status, 
        stateBadge(st.overall, "lg"),
        (() => { const b = transmissionBadge(st.provider); b.classList.add("lg"); return b; })(),
        st.paused ? badge("neutral", "pause", "일시 중지됨", "lg optional") : null);
    }
    set(topRefs.avatar, avatar());
    updateBell();
  }

  async function runSearch(wrap, q) {
    closePopover();
    if (!q) return;
    let r;
    try { r = await api("/api/search?q=" + encodeURIComponent(q)); } catch (e) { return; }
    const pop = h("div", {class: "popover", role: "listbox", "aria-label": "검색 결과"});
    const section = (title, items) => items.length ? h("div", {class: "popover-section"}, h("div", {class: "popover-title"}, title), ...items) : null;
    const item = (iconName, text, sub, onClick) => h("button", {type: "button", class: "popover-item", onclick: () => { closePopover(); onClick(); }},
      icon(iconName, "sm"), h("div", {class: "list-main"}, h("span", {class: "truncate"}, text), sub ? h("span", {class: "list-sub"}, sub) : null));
    const parts = [
      section("결정", r.decisions.slice(0, 5).map(d => item("results", d.latest || d.topic, d.date || "", () => go("results", {q: d.latest || d.topic})))),
      section("점검 결과", r.findings.slice(0, 6).map(f => item(f.label === "INCONSISTENT" ? "x-circle" : "help", f.text,
        (DA.LABELS[f.label] || f.label) + " · " + f.source_doc, () => go("results", {open: [f.root, f.topic, f.artifact_id].join("|")})))),
      section("파일", r.files.slice(0, 5).map(p => item("file", p, "", () => go("activity", {tab: "files", q: p})))),
      section("메모", r.notes.slice(0, 4).map(n => item("note", n.text, n.date, () => go("notes")))),
    ].filter(Boolean);
    append(pop, parts.length ? parts : [h("div", {class: "popover-empty"}, "'" + q + "'에 대한 결과가 없습니다.")]);
    wrap.appendChild(pop);
    openPopover = pop;
  }

  function append(node, children) { DA.append(node, children); return node; }

  let inbox = [];
  function lastSeen() { try { return localStorage.getItem("da.seen") || ""; } catch (e) { return ""; } }
  function updateBell() {
    const unread = inbox.filter(i => i.time > lastSeen()).length;
    topRefs.bellDot.hidden = unread === 0;
    topRefs.bellDot.textContent = unread > 9 ? "9+" : String(unread);
  }
  async function refreshInbox() {
    try { inbox = await api("/api/notifications"); } catch (e) { return; }
    updateBell();
  }

  function toggleBell(wrap) {
    if (openPopover && wrap.contains(openPopover)) { closePopover(); return; }
    closePopover();
    const seen = lastSeen();
    const pop = h("div", {class: "popover", style: {width: "380px"}},
      h("div", {class: "popover-section row-between"}, h("strong", {}, "알림"),
        h("span", {class: "small muted"}, "이 컴퓨터에서만 보관")),
      inbox.length === 0 ? h("div", {class: "popover-empty"}, "새 알림이 없습니다.") :
        h("div", {class: "popover-section"}, ...inbox.slice(0, 20).map(n => h("div", {class: "popover-item"},
          icon(n.level === "error" ? "x-circle" : (n.level === "attention" ? "alert" : "info"), "sm"),
          h("div", {class: "list-main"}, h("span", {class: n.time > seen ? "strong" : ""}, n.text),
            h("span", {class: "list-sub"}, relTime(n.time)))))));
    wrap.appendChild(pop);
    openPopover = pop;
    if (inbox.length) { try { localStorage.setItem("da.seen", inbox[0].time); } catch (e) { /* ignore */ } }
    updateBell();
  }

  function toggleProfileMenu(wrap) {
    if (openPopover && wrap.contains(openPopover)) { closePopover(); return; }
    closePopover();
    const s = S.settings || {};
    const menu = (iconName, text, fn) => h("button", {type: "button", class: "menu-item", role: "menuitem",
      onclick: () => { closePopover(); fn(); }}, icon(iconName, "sm"), text);
    const dark = document.documentElement.getAttribute("data-theme") === "dark";
    const pop = h("div", {class: "popover", role: "menu", style: {width: "260px"}},
      h("div", {class: "popover-section row"}, avatar(), h("div", {class: "list-main"},
        h("strong", {}, s.display_name || "이름 없음"), h("span", {class: "list-sub"}, "이 컴퓨터의 로컬 프로필"))),
      h("div", {class: "popover-section"},
        menu("user", "내 정보", () => go("profile")),
        menu("settings", "설정", () => go("settings")),
        menu(dark ? "sun" : "moon", dark ? "라이트 테마로 전환" : "다크 테마로 전환", () => saveTheme(dark ? "light" : "dark")),
        menu("shield", "이 앱이 읽는 범위", () => go("settings", {section: "access"})),
        menu("database", "데이터 폴더 열기", () => openLocation("data"))));
    wrap.appendChild(pop);
    openPopover = pop;
    const first = pop.querySelector(".menu-item");
    if (first) first.focus();
  }

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
    title: "홈", icon: "home", auto: true,
    async load() {
      const [findings, activity] = await Promise.all([api("/api/findings"), api("/api/activity")]);
      const st = S.status || await api("/api/status");
      return {st, findings: findings.filter(f => f.label === "INCONSISTENT").slice(0, 5), activity: activity.slice(0, 8)};
    },
    render({st, findings, activity}) {
      if (st.registered_folders === 0) {
        return page("홈", null, null, h("section", {class: "card"}, emptyState({
          icon: "folder-plus", title: "감시할 폴더를 추가하세요",
          text: "회의록, 요구사항, 작업 표, 테스트 계획, 일정이 있는 프로젝트 폴더를 고르면 결정이 바뀌었는데 반영되지 않은 항목을 찾아 알려 줍니다. 선택한 폴더와 그 안의 모든 폴더를 포함합니다.",
          action: button("폴더 추가", {variant: "primary", icon: "folder-plus", onClick: () => startAddFolder()}),
        })));
      }
      return page("홈", null, null, statusHero(st), progressCard(st), metrics(st),
        h("div", {class: "grid grid-2"},
          card("최근 불일치", "결정과 맞지 않는 것으로 보이는 항목", [button("모두 보기", {variant: "ghost", size: "sm", icon: "arrow-right", onClick: () => go("results", {label: "INCONSISTENT"})})],
            findings.length === 0
              ? emptyState({icon: "check-circle", title: "불일치 항목이 없습니다", text: st.overall.state === "demo" ? "데모 판정기 결과입니다. 실제 검사가 아니므로 놓친 항목이 있을 수 있습니다." : "새 불일치가 생기면 여기와 알림으로 알려 드립니다.", neutral: true})
              : h("div", {class: "list"}, ...findings.map(f => h("div", {class: "list-item clickable", tabindex: "0", role: "button",
                onclick: () => openFinding(f), onkeydown: (e) => { if (e.key === "Enter") openFinding(f); }},
              labelBadge(f.label),
              h("div", {class: "list-main"}, h("span", {class: "strong"}, f.text),
                h("span", {class: "list-sub"}, f.topic_label, h("span", {}, "·"), f.source_doc, f.owner ? h("span", {}, "· 담당 " + f.owner) : null)),
              icon("chevron-right", "sm"))))),
          card("최근 활동", "이 프로그램이 한 일", [button("전체 기록", {variant: "ghost", size: "sm", icon: "arrow-right", onClick: () => go("activity")})],
            activity.length === 0 ? emptyState({icon: "activity", title: "아직 기록이 없습니다", neutral: true}) : timeline(activity))));
    },
  };

  function statusHero(st) {
    const o = st.overall;
    const info = stateInfo(o);
    const heroIcon = icon(info.icon, "xl" + (o.state === "running" ? " spin" : ""));
    const pauseBtn = button(st.paused ? "다시 시작" : "일시 중지", {icon: st.paused ? "play" : "pause"});
    pauseBtn.addEventListener("click", () => busy(pauseBtn, async () => {
      await api("/api/pause", "POST", {paused: !st.paused});
      await refreshAll();
    }));
    const rescanBtn = button("지금 다시 점검", {variant: "primary", icon: "refresh"});
    rescanBtn.addEventListener("click", () => busy(rescanBtn, async () => {
      await api("/api/rescan", "POST", {});
      toast("다시 점검을 시작했습니다.", "info");
      await refreshAll();
    }));
    const sends = st.provider.sends_externally;
    const extras = [];
    if (o.state === "demo") {
      extras.push(callout("warning", "alert", "데모 판정기 결과는 실제 검사가 아닙니다",
        "내장 규칙 판정기는 예시 문서의 어휘만 알아 실제 문서에서는 대부분을 놓칩니다. 실제 문서를 검사하려면 판정 방식을 설정하세요.",
        [button("판정 방식 설정", {size: "sm", icon: "arrow-right", onClick: () => go("profile", {section: "judge"})})]));
    } else if (o.state === "failed") {
      extras.push(callout("danger", "x-circle", "설정을 고쳐야 점검할 수 있습니다", o.detail,
        [button("판정 방식 확인", {size: "sm", onClick: () => go("profile", {section: "judge"})})]));
    } else if (o.state === "stale") {
      extras.push(callout("warning", "refresh", "판정 방식이 바뀌었습니다", "이전 결과는 예전 판정 방식으로 만든 것입니다. 다시 점검하면 새 방식으로 바뀝니다."));
    }
    if (st.last_error && o.state !== "failed") extras.push(callout("danger", "alert", "최근 문제", st.last_error));
    return h("section", {class: "card"},
      h("div", {class: "hero"},
        h("div", {class: "hero-icon " + info.kind}, heroIcon),
        h("div", {class: "hero-main"},
          h("div", {class: "row"}, stateBadge(o), transmissionBadge(st.provider)),
          h("div", {class: "hero-title"}, o.label),
          o.detail && o.state !== "failed" ? h("p", {class: "muted"}, o.detail.replace(/(\d{4}-\d\d-\d\d)T(\d\d:\d\d):\d\d/g, "$1 $2")) : null),
        h("div", {class: "btn-group"}, pauseBtn, rescanBtn)),
      h("div", {class: "meta-grid"},
        meta("clock", "마지막 점검", relTime(lastCompletion(st) || st.last_cycle)),
        meta("cpu", "판정 방식", providerName(st.provider.id)),
        meta(sends ? "cloud" : "shield-check", "문서 외부 전송", sends ? "예. " + st.provider.id + "로 전송됩니다" : "아니요. 이 컴퓨터 안에서만 처리합니다"),
        meta("eye", "감시 상태", st.paused ? "일시 중지됨" : (st.busy ? "점검 중" : "폴더 " + st.registered_folders + "개 감시 중"))),
      extras.length ? h("div", {class: "stack-sm", style: {marginTop: "16px"}}, extras) : null);
  }

  function meta(iconName, label, value) {
    return h("div", {class: "meta-item"}, h("span", {class: "meta-label"}, icon(iconName, "sm"), label),
      h("span", {class: "meta-value"}, value));
  }

  function progressCard(st) {
    const rows = st.folders.map(f => {
      const p = f.progress;
      const active = st.busy && p.phase !== "idle" && p.phase !== "done";
      const c = f.completion;
      const head = h("div", {class: "row-between"},
        h("div", {class: "row", style: {minWidth: "0"}}, icon("folder", "sm"), h("strong", {class: "truncate", title: f.root}, folderName(f.root))),
        active ? badge("info", "loader", p.phase_label) : (p.phase === "error" ? badge("danger", "x-circle", p.phase_label) : badge("neutral", "clock", "대기 중")));
      if (!active && !p.files_total && !p.topics_total) {
        return h("div", {class: "stack-sm"}, head, h("p", {class: "small muted"},
          c ? "마지막 완료 " + fmtTime(c.at) + " · 파일 " + c.files_ok + "개, 결정 " + c.topics + "건" + (c.verified ? "" : " · 데모 판정기") : "아직 완료된 점검이 없습니다."));
      }
      return h("div", {class: "stack-sm"}, head,
        h("div", {class: "grid grid-2", style: {gap: "12px"}},
          progressBar(p.files_done, p.files_total, "읽은 파일"),
          progressBar(p.topics_done, p.topics_total, "점검한 결정" + (p.topics_unchanged ? " (변경 없어 건너뜀 " + p.topics_unchanged + ")" : ""))),
        h("div", {class: "row small muted"},
          h("span", {}, "LLM 호출 " + p.llm_calls + "회"),
          p.elapsed_seconds !== null ? h("span", {}, "· 경과 " + p.elapsed_seconds + "초") : null,
          p.current ? h("span", {class: "truncate", style: {maxWidth: "480px"}, title: p.current}, "· 처리 중: " + p.current) : null));
    });
    if (rows.length === 0) return null;
    return card("진행도", st.busy ? "지금 점검하고 있습니다" : "폴더별 최근 점검", null, h("div", {class: "stack"}, rows));
  }

  function metrics(st) {
    const m = (kind, iconName, label, value, foot, onClick) => h("button", {type: "button", class: "card clickable metric " + kind, onclick: onClick},
      h("div", {class: "metric-top"}, h("span", {}, label), icon(iconName, "sm")),
      h("div", {class: "metric-value"}, String(value)), h("div", {class: "metric-foot"}, foot));
    return h("div", {class: "metrics"},
      m(st.inconsistent ? "danger" : "", "x-circle", "불일치", st.inconsistent, "반영이 필요해 보이는 항목", () => go("results", {label: "INCONSISTENT"})),
      m(st.uncertain ? "warning" : "", "help", "불확실", st.uncertain, "근거가 부족한 항목", () => go("results", {label: "UNCERTAIN"})),
      m("", "help", "확인 질문", st.questions, "답하면 다시 점검합니다", () => go("questions")),
      m("", "folder", "감시 중인 폴더", st.registered_folders, "하위 폴더 포함", () => go("folders")),
      m("", "link", "연결된 계정", st.connected_accounts, "읽기 전용", () => go("accounts")));
  }

  function timeline(items) {
    return h("div", {class: "timeline"}, ...items.map(a => h("div", {class: "tl-item"},
      h("span", {class: "tl-dot " + (a.level || "")}, icon(a.level === "error" ? "x" : (a.level === "attention" ? "alert" : "check"))),
      h("div", {class: "stack-sm", style: {gap: "2px"}}, h("span", {}, a.text), h("span", {class: "tl-time"}, relTime(a.time) + " · " + fmtTime(a.time))))));
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

  // ---------------------------------------------------------------- questions

  ROUTES.questions = {
    title: "확인 질문", icon: "help", auto: true,
    lead: "근거가 부족해 추측하지 않고 남겨 둔 질문입니다. 답하면 그 결정만 다시 점검합니다.",
    async load() { return api("/api/questions"); },
    render(rows) {
      if (rows.length === 0) {
        return page(this.title, this.lead, null, h("section", {class: "card"}, emptyState({icon: "check-circle",
          title: "답변이 필요한 질문이 없습니다", text: "결정의 적용 범위가 불분명한 항목이 생기면 여기에 질문이 쌓입니다.",
          action: button("점검 결과 보기", {onClick: () => go("results")})})));
      }
      return page(this.title, this.lead, null, ...rows.map(q => {
        const answer = textarea({rows: "3", placeholder: "답변을 입력하세요", "aria-label": "답변"});
        const quick = ["예", "아니오", "일부만 해당합니다"].map(t => button(t, {size: "sm", variant: "ghost", onClick: () => { answer.value = t; answer.focus(); }}));
        const send = button("답변 보내기", {variant: "primary", icon: "arrow-right"});
        send.addEventListener("click", () => busy(send, async () => {
          if (!answer.value.trim()) throw new Error("답변을 입력하세요.");
          await api("/api/answer", "POST", {root: q.root, topic: q.topic, text: answer.value.trim()});
          toast("답변을 반영해 이 결정을 다시 점검합니다.", "success");
          await refreshAll();
        }));
        return h("section", {class: "card stack"},
          h("div", {class: "row-between"}, h("div", {class: "row"}, badge("info", "help", "확인 필요"), h("span", {class: "muted small"}, "결정: " + q.topic_label)),
            h("span", {class: "small muted"}, relTime(q.created_at))),
          h("h3", {}, q.text),
          answer, h("div", {class: "row-between"}, h("div", {class: "row"}, quick), send));
      }));
    },
  };

  // ---------------------------------------------------------------- folders

  const FS = {open: null, trees: {}};

  ROUTES.folders = {
    title: "폴더", icon: "folder", auto: false,
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

  ROUTES.digest = {
    title: "오늘 요약", icon: "calendar", auto: false,
    lead: "하루 한 번 불일치, 질문, 점검 상태를 요약합니다. 요약 파일은 앱 데이터 폴더의 digests에 저장됩니다.",
    async load() {
      const [d, s] = await Promise.all([api("/api/digest"), api("/api/settings")]);
      return {d, s};
    },
    render({d, s}) {
      const on = toggle("매일 요약 받기", s.digest_enabled, "정한 시각 이후 프로그램이 켜져 있을 때 하루 한 번 알립니다.");
      const time = input({type: "time", value: s.digest_time, "aria-label": "요약 시각"});
      const save = button("저장", {variant: "primary"});
      save.addEventListener("click", () => busy(save, async () => {
        await api("/api/settings", "POST", {digest_enabled: on.input.checked, digest_time: time.value});
        toast("요약 설정을 저장했습니다.", "success");
      }));
      const now = button("지금 요약 만들기", {icon: "refresh"});
      now.addEventListener("click", () => busy(now, async () => { await api("/api/digest/now", "POST"); renderRoute(true); }));
      return page(this.title, this.lead, [now],
        card("요약 설정", null, null, h("div", {class: "row-between"}, on, h("div", {class: "row"}, h("div", {style: {width: "160px"}}, field("시각", time)), save))),
        card(d.name ? "최근 요약" : null, d.name || null, d.markdown ? [button("복사", {variant: "ghost", size: "sm", icon: "copy", onClick: () => copyText(d.markdown)})] : null,
          d.markdown ? markdownView(d.markdown) : emptyState({icon: "calendar", title: "아직 만든 요약이 없습니다", text: "'지금 요약 만들기'를 누르면 바로 만들어 볼 수 있습니다.", neutral: true})));
    },
  };

  // ---------------------------------------------------------------- activity

  const AS = {tab: "activity", q: ""};

  ROUTES.activity = {
    title: "활동 기록", icon: "activity", auto: true,
    lead: "이 프로그램이 읽은 파일, 점검 기록, 시스템 로그입니다.",
    async load(params) {
      if (params.has("tab")) AS.tab = params.get("tab");
      if (params.has("q")) AS.q = params.get("q");
      const [activity, files, log] = await Promise.all([api("/api/activity"), api("/api/files"), api("/api/log")]);
      return {activity, files, log, st: S.status};
    },
    render({activity, files, log, st}) {
      const tabs = [["activity", "활동"], ["files", "읽은 파일 " + files.length], ["checks", "점검 기록"], ["log", "시스템 로그"]];
      const panel = h("div");
      const tabBar = h("div", {class: "tabs", role: "tablist"}, ...tabs.map(([id, label]) => h("button", {type: "button", class: "tab", role: "tab",
        "aria-selected": String(AS.tab === id), onclick: () => { AS.tab = id; lastSig = ""; renderRoute(false); }}, label)));
      if (AS.tab === "files") panel.appendChild(filesPanel(files));
      else if (AS.tab === "checks") panel.appendChild(checksPanel(st));
      else if (AS.tab === "log") {
        panel.appendChild(card(null, null, null, h("div", {class: "row-between", style: {marginBottom: "12px"}},
          h("span", {class: "muted small"}, "decision-agent.log 최근 " + log.lines.length + "줄 (비밀 값은 기록하지 않습니다)"),
          h("div", {class: "btn-group"}, button("복사", {size: "sm", icon: "copy", onClick: () => copyText(log.lines.join("\n"))}),
            button("로그 폴더 열기", {size: "sm", icon: "external", onClick: () => openLocation("logs")}))),
        h("pre", {class: "log"}, log.lines.join("\n") || "로그가 없습니다.")));
      } else {
        panel.appendChild(card(null, null, null, activity.length === 0 ? emptyState({icon: "activity", title: "아직 기록이 없습니다", neutral: true}) : timeline(activity)));
      }
      return page(this.title, this.lead, null, tabBar, panel);
    },
  };

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
        renderTopbar();
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
      renderTopbar();
      return;
    }
    renderTopbar();
    renderNav();
    if (ONB.active && ONB.step === 3) drawOnboarding();
    maybeOnboarding();
  }

  async function loop() {
    await tick();
    refreshInbox();
    const route = ROUTES[parseHash().name];
    if (!S.offline && route.auto && !anyLayerOpen() && !isTyping() && !openPopover && !ONB.active) {
      await renderRoute(false);
    }
  }

  async function boot() {
    try { if (localStorage.getItem("da.sidebar") === "1") document.getElementById("app").classList.add("collapsed"); } catch (e) { /* ignore */ }
    buildDropVeil();
    try {
      S.settings = await api("/api/settings");
      applyTheme(S.settings.theme);
    } catch (e) {
      S.settings = null;
    }
    await tick();
    refreshInbox();
    await renderRoute(true);
    window.addEventListener("hashchange", () => { closePopover(); renderRoute(true); document.getElementById("view").focus({preventScroll: true}); });
    setInterval(loop, REFRESH_MS);
  }

  DA.initTheme();
  boot();
})();
