/* Desktop helper (always on top, bottom right): a 56px round button that opens a mini panel.
   State arrives from Python through DAHelper.update(); actions go through window.pywebview.api.
   Text is always set with textContent. */
"use strict";

(() => {
  const SVG = "http://www.w3.org/2000/svg";
  const HEARTBEAT_LIMIT = 90;
  const root = document.getElementById("helper");
  let data = null;
  let receivedAt = 0;
  let mode = "icon";
  let heartbeatTimer = null;
  let press = null;

  function el(tag, attrs, ...kids) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(attrs || {})) {
      if (v === null || v === undefined || v === false) continue;
      if (k === "class") node.className = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else node.setAttribute(k, v === true ? "" : String(v));
    }
    for (const kid of kids.flat()) {
      if (kid === null || kid === undefined || kid === false) continue;
      node.appendChild(typeof kid === "string" ? document.createTextNode(kid) : kid);
    }
    return node;
  }

  function glyph(name) {
    const svg = document.createElementNS(SVG, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("class", "glyph");
    const paths = {
      check: ["M5 12.5l4.5 4.5L19 7.5"],
      alert: ["M12 7v6", "M12 16.5v.5"],
      pause: ["M9 7v10", "M15 7v10"],
      error: ["M8 8l8 8", "M16 8l-8 8"],
    }[name] || [];
    for (const d of paths) {
      const p = document.createElementNS(SVG, "path");
      p.setAttribute("d", d);
      svg.appendChild(p);
    }
    return svg;
  }

  function call(name, ...args) {
    const api = window.pywebview && window.pywebview.api;
    if (!api || typeof api[name] !== "function") return Promise.resolve(null);
    return api[name](...args).catch(() => null);
  }

  function clock(iso) {
    if (!iso) return "아직 없음";
    const text = String(iso);
    const today = new Date();
    const pad = (n) => String(n).padStart(2, "0");
    const day = today.getFullYear() + "-" + pad(today.getMonth() + 1) + "-" + pad(today.getDate());
    const time = text.slice(11, 16);
    if (text.slice(0, 10) === day) return time;
    return Number(text.slice(5, 7)) + "월 " + Number(text.slice(8, 10)) + "일 " + time;
  }

  function alive() {
    if (!data || data.tick_age === null || data.tick_age === undefined) return false;
    return data.tick_age + (Date.now() - receivedAt) / 1000 <= HEARTBEAT_LIMIT;
  }

  // ------------------------------------------------------------ views

  function orb() {
    const state = data ? data.state : "normal";
    const icons = {normal: "check", busy: "check", attention: "alert", paused: "pause", error: "error"};
    const node = el("button", {type: "button", class: "orb pywebview-drag-region " + state,
      "aria-label": "도우미: " + (data ? data.state_label : "불러오는 중"), title: data ? data.state_label : ""},
    state === "busy" ? el("span", {class: "ring", "aria-hidden": "true"}) : null,
    glyph(icons[state] || "check"),
    data && data.badge ? el("span", {class: "badge", "aria-label": "확인 필요 " + data.badge + "건"}, data.badge > 99 ? "99+" : String(data.badge)) : null);
    if (data && data.color) node.style.backgroundColor = data.color;
    node.addEventListener("mousedown", (e) => { if (e.button === 0) press = {x: e.screenX, y: e.screenY}; });
    // A drag ends in the window-level mouseup below and clears press, so no click follows.
    node.addEventListener("click", () => {
      if (!press) return;
      press = null;
      open("panel");
    });
    node.addEventListener("contextmenu", (e) => { e.preventDefault(); open("menu"); });
    return node;
  }

  function panel() {
    const d = data || {todos: [], state_label: "불러오는 중", doing: "", badge: 0};
    const beat = alive();
    const head = el("div", {class: "p-head"},
      el("span", {class: "p-dot " + (d.state || "normal")}),
      el("strong", {class: "p-title"}, (data && data.product) || ""),
      el("span", {class: "p-state " + (d.state || "normal")}, d.state_label),
      el("button", {type: "button", class: "p-close", "aria-label": "접기", title: "접기", onclick: close}, "닫기"));
    const meta = el("div", {class: "p-meta"},
      el("span", {class: "p-beat " + (beat ? "ok" : "bad")}, beat ? "작동 중" : "응답 없음"),
      el("span", {class: "p-muted"}, "마지막 확인 " + clock(d.last_checked)));
    // While a costly check waits for the user's answer, the line opens the home screen with the banner.
    const doing = d.awaiting_confirm
      ? el("button", {type: "button", class: "p-doing p-confirm", title: "앱 홈에서 계속할지 선택합니다", onclick: () => act("home")}, d.doing || "확인 대기")
      : el("p", {class: "p-doing"}, d.doing || "");
    const demo = d.demo ? el("p", {class: "p-demo"}, "데모 모드입니다. 실제 검사가 아닙니다.") : null;
    const todoHead = el("div", {class: "p-row"}, el("strong", {}, "내가 할 일" + (d.badge ? " " + d.badge + "건" : "")),
      el("button", {type: "button", class: "p-link", onclick: () => act("details")}, "자세히"));
    const todos = d.todos.length
      ? el("ul", {class: "p-todos"}, ...d.todos.map(t => el("li", {class: "p-todo " + t.kind},
        el("span", {class: "p-todo-title"}, t.title), t.sub ? el("span", {class: "p-todo-sub"}, t.sub) : null)))
      : el("p", {class: "p-muted p-empty"}, "지금 확인할 항목이 없습니다.");
    const buttons = el("div", {class: "p-buttons"},
      el("button", {type: "button", class: "p-btn primary", onclick: () => act("rescan")}, "지금 점검"),
      el("button", {type: "button", class: "p-btn", onclick: () => act("pause")}, d.paused ? "재개" : "일시 중지"),
      el("button", {type: "button", class: "p-btn", onclick: () => act("quick")}, "빠른 점검"),
      el("button", {type: "button", class: "p-btn", onclick: () => act("open_app")}, "앱 열기"));
    return el("section", {class: "panel", role: "dialog", "aria-label": "도우미 패널"}, head, meta, doing, demo, todoHead, todos, buttons);
  }

  function menu() {
    const d = data || {};
    const item = (label, name, danger) => el("button", {type: "button", role: "menuitem", class: "m-item" + (danger ? " danger" : ""),
      onclick: () => act(name)}, label);
    return el("nav", {class: "menu", role: "menu", "aria-label": "도우미 메뉴"},
      item("앱 열기", "open_app"), item("빠른 점검", "quick"), item("지금 점검", "rescan"),
      item(d.paused ? "재개" : "일시 중지", "pause"), el("hr", {}), item("도우미 숨기기", "hide_helper"),
      item("종료", "quit", true));
  }

  function render() {
    root.className = "helper mode-" + mode;
    root.replaceChildren(mode === "panel" ? panel() : mode === "menu" ? menu() : orb());
    if (mode !== "icon") {
      const first = root.querySelector("button");
      if (first) first.focus();
    }
  }

  // ------------------------------------------------------------ behaviour

  async function open(kind) {
    await call("expand", kind);
    mode = kind;
    render();
    clearInterval(heartbeatTimer);
    // Only while the panel is open: re-evaluate "작동 중" against the last heartbeat.
    if (kind === "panel") heartbeatTimer = setInterval(() => { if (mode === "panel") render(); }, 15000);
  }

  async function close() {
    if (mode === "icon") return;
    clearInterval(heartbeatTimer);
    mode = "icon";
    render();
    await call("collapse");
  }

  async function act(name) {
    clearInterval(heartbeatTimer);
    mode = "icon";
    render();
    await call("action", name);
  }

  function shake() {
    const node = root.querySelector(".orb");
    if (!node) return;
    node.classList.remove("shake");
    void node.offsetWidth;
    node.classList.add("shake");
    setTimeout(() => node.classList.remove("shake"), 2000);
  }

  // pywebview moves the window while the button is dragged; report the final spot once.
  window.addEventListener("mouseup", (e) => {
    if (!press) return;
    if (Math.abs(e.screenX - press.x) > 3 || Math.abs(e.screenY - press.y) > 3) {
      press = null;
      call("dragged");
    }
  });
  window.addEventListener("blur", () => { close(); });
  window.addEventListener("keydown", (e) => { if (e.key === "Escape") close(); });

  window.DAHelper = {
    update(next) {
      data = next;
      receivedAt = Date.now();
      if (next.mode === "icon" && mode !== "icon") mode = "icon";
      render();
      if (next.alert && mode === "icon") shake();
    },
  };

  window.addEventListener("pywebviewready", async () => {
    const state = await call("state");
    if (state) window.DAHelper.update(state);
  });
  render();
})();
