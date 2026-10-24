/* Shared helpers for the dashboard and the quick-check window.
   Every DOM node is built with createElement / createElementNS and text is always inserted
   as text nodes, so document text can never become markup. */
"use strict";

const DA = (() => {
  const TOKEN = window.DA_BOOT.token;
  const SVG_NS = "http://www.w3.org/2000/svg";

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value === undefined || value === null || value === false) continue;
      if (key === "class") node.className = value;
      else if (key === "style" && typeof value === "object") Object.assign(node.style, value);
      else if (key === "value") node.value = value;
      else if (key === "checked") node.checked = Boolean(value);
      else if (key.startsWith("on") && typeof value === "function") node.addEventListener(key.slice(2), value);
      else node.setAttribute(key, value === true ? "" : String(value));
    }
    append(node, children);
    return node;
  }

  // replaceChildren that skips null, undefined, and false instead of printing them as text.
  function set(node, ...children) {
    node.replaceChildren();
    return append(node, children);
  }

  function append(node, children) {
    for (const child of children.flat(Infinity)) {
      if (child === null || child === undefined || child === false) continue;
      node.appendChild(typeof child === "string" || typeof child === "number"
        ? document.createTextNode(String(child)) : child);
    }
    return node;
  }

  // 24x24 stroke icons. Strings are path data; objects describe circles, lines, rects, polylines.
  const ICONS = {
    home: ["M3 10.5 12 3l9 7.5", "M5 9.5V20h14V9.5", "M10 20v-6h4v6"],
    results: [{r: [8, 2, 8, 4, 1]}, "M16 4h2a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2h2", "m9 14 2 2 4-4"],
    alert: ["M10.3 3.9 1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z", {l: [12, 9, 12, 13]}, {l: [12, 17, 12.01, 17]}],
    help: [{c: [12, 12, 10]}, "M9.1 9a3 3 0 0 1 5.8 1c0 2-3 3-3 3", {l: [12, 17, 12.01, 17]}],
    folder: ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z"],
    "folder-plus": ["M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2Z", {l: [12, 10, 12, 16]}, {l: [9, 13, 15, 13]}],
    link: ["M10 13a5 5 0 0 0 7.5.5l3-3a5 5 0 0 0-7-7l-1.7 1.7", "M14 11a5 5 0 0 0-7.5-.5l-3 3a5 5 0 0 0 7 7l1.7-1.7"],
    note: ["M12 20h9", "M16.5 3.5a2.1 2.1 0 0 1 3 3L7 19l-4 1 1-4Z"],
    calendar: [{r: [3, 4, 18, 18, 2]}, {l: [16, 2, 16, 6]}, {l: [8, 2, 8, 6]}, {l: [3, 10, 21, 10]}],
    activity: [{p: "22 12 18 12 15 21 9 3 6 12 2 12"}],
    settings: [{l: [4, 21, 4, 14]}, {l: [4, 10, 4, 3]}, {l: [12, 21, 12, 12]}, {l: [12, 8, 12, 3]}, {l: [20, 21, 20, 16]},
      {l: [20, 12, 20, 3]}, {l: [1, 14, 7, 14]}, {l: [9, 8, 15, 8]}, {l: [17, 16, 23, 16]}],
    user: [{c: [12, 8, 4]}, "M4 21a8 8 0 0 1 16 0"],
    search: [{c: [11, 11, 7]}, {l: [21, 21, 16.65, 16.65]}],
    bell: ["M18 8a6 6 0 0 0-12 0c0 7-3 9-3 9h18s-3-2-3-9", "M13.7 21a2 2 0 0 1-3.4 0"],
    "chevron-left": [{p: "15 18 9 12 15 6"}],
    "chevron-right": [{p: "9 18 15 12 9 6"}],
    "chevron-down": [{p: "6 9 12 15 18 9"}],
    check: [{p: "20 6 9 17 4 12"}],
    "check-circle": [{c: [12, 12, 10]}, {p: "8 12 11 15 16 9"}],
    x: [{l: [18, 6, 6, 18]}, {l: [6, 6, 18, 18]}],
    "x-circle": [{c: [12, 12, 10]}, {l: [15, 9, 9, 15]}, {l: [9, 9, 15, 15]}],
    info: [{c: [12, 12, 10]}, {l: [12, 16, 12, 12]}, {l: [12, 8, 12.01, 8]}],
    "alert-circle": [{c: [12, 12, 9]}, {l: [12, 7.5, 12, 12.5]}, {l: [12, 16, 12.01, 16]}],
    refresh: ["M21 12a9 9 0 0 1-15.5 6.2L3 16", "M3 12a9 9 0 0 1 15.5-6.2L21 8", {p: "21 3 21 8 16 8"}, {p: "3 21 3 16 8 16"}],
    pause: [{r: [6, 4, 4, 16, 1]}, {r: [14, 4, 4, 16, 1]}],
    play: [{p: "6 4 20 12 6 20 6 4"}],
    plus: [{l: [12, 5, 12, 19]}, {l: [5, 12, 19, 12]}],
    trash: [{p: "3 6 5 6 21 6"}, "M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6", "M10 11v6", "M14 11v6", "M9 6V4a1 1 0 0 1 1-1h4a1 1 0 0 1 1 1v2"],
    external: ["M18 13v6a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h6", {p: "15 3 21 3 21 9"}, {l: [10, 14, 21, 3]}],
    copy: [{r: [9, 9, 13, 13, 2]}, "M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1"],
    upload: ["M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4", {p: "17 8 12 3 7 8"}, {l: [12, 3, 12, 15]}],
    shield: ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z"],
    "shield-check": ["M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10Z", {p: "9 12 11 14 15 10"}],
    lock: [{r: [4, 11, 16, 10, 2]}, "M8 11V7a4 4 0 0 1 8 0v4"],
    cloud: ["M17.5 19H8a6 6 0 1 1 5.6-8.2A4.5 4.5 0 1 1 17.5 19Z"],
    "cloud-off": ["M17.5 19H8a6 6 0 0 1-3.2-11", "M9.5 5.3A6 6 0 0 1 13.6 10.8 4.5 4.5 0 0 1 20.7 16", {l: [3, 3, 21, 21]}],
    mail: [{r: [2, 4, 20, 16, 2]}, {p: "22 6 12 13 2 6"}],
    book: ["M4 19.5A2.5 2.5 0 0 1 6.5 17H20", "M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2Z"],
    kanban: [{r: [3, 3, 18, 18, 2]}, "M8 7v7", "M12 7v4", "M16 7v9"],
    hash: [{l: [4, 9, 20, 9]}, {l: [4, 15, 20, 15]}, {l: [10, 3, 8, 21]}, {l: [16, 3, 14, 21]}],
    inbox: [{p: "22 12 16 12 14 15 10 15 8 12 2 12"}, "M5.5 5.1 2 12v6a2 2 0 0 0 2 2h16a2 2 0 0 0 2-2v-6l-3.5-6.9A2 2 0 0 0 16.8 4H7.2a2 2 0 0 0-1.7 1.1Z"],
    key: [{c: [7.5, 15.5, 5.5]}, "M21 2l-9.6 9.6", "M15.5 7.5l3 3L22 7l-3-3"],
    file: ["M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8Z", {p: "14 2 14 8 20 8"}],
    moon: ["M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z"],
    sun: [{c: [12, 12, 4]}, {l: [12, 2, 12, 4]}, {l: [12, 20, 12, 22]}, {l: [4.9, 4.9, 6.3, 6.3]}, {l: [17.7, 17.7, 19.1, 19.1]},
      {l: [2, 12, 4, 12]}, {l: [20, 12, 22, 12]}, {l: [4.9, 19.1, 6.3, 17.7]}, {l: [17.7, 6.3, 19.1, 4.9]}],
    monitor: [{r: [2, 3, 20, 14, 2]}, {l: [8, 21, 16, 21]}, {l: [12, 17, 12, 21]}],
    sidebar: [{r: [3, 3, 18, 18, 2]}, {l: [9, 3, 9, 21]}],
    loader: ["M21 12a9 9 0 1 1-6.2-8.6"],
    clock: [{c: [12, 12, 10]}, {p: "12 6 12 12 16 14"}],
    database: ["M3 5c0-1.7 4-3 9-3s9 1.3 9 3-4 3-9 3-9-1.3-9-3Z", "M3 5v14c0 1.7 4 3 9 3s9-1.3 9-3V5", "M3 12c0 1.7 4 3 9 3s9-1.3 9-3"],
    filter: [{p: "22 3 2 3 10 12.5 10 19 14 21 14 12.5 22 3"}],
    "arrow-right": [{l: [5, 12, 19, 12]}, {p: "12 5 19 12 12 19"}],
    terminal: [{p: "4 17 10 11 4 5"}, {l: [12, 19, 20, 19]}],
    zap: [{p: "13 2 3 14 12 14 11 22 21 10 12 10 13 2"}],
    keyboard: [{r: [2, 6, 20, 12, 2]}, {l: [7, 14, 17, 14]}, {l: [7, 10, 7.01, 10]}, {l: [11, 10, 11.01, 10]}, {l: [15, 10, 15.01, 10]}],
    globe: [{c: [12, 12, 10]}, {l: [2, 12, 22, 12]}, "M12 2a15 15 0 0 1 0 20a15 15 0 0 1 0-20"],
    cpu: [{r: [5, 5, 14, 14, 2]}, {r: [9, 9, 6, 6, 1]}, "M9 2v3", "M15 2v3", "M9 19v3", "M15 19v3", "M2 9h3", "M2 15h3", "M19 9h3", "M19 15h3"],
    eye: ["M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12Z", {c: [12, 12, 3]}],
    minus: [{l: [5, 12, 19, 12]}],
    list: [{r: [5, 3, 14, 18, 2]}, {l: [9, 8, 15, 8]}, {l: [9, 12, 15, 12]}, {l: [9, 16, 13, 16]}],
    more: [{c: [5, 12, 1]}, {c: [12, 12, 1]}, {c: [19, 12, 1]}],
    "pause-bars": [{l: [9, 6, 9, 18]}, {l: [15, 6, 15, 18]}],
  };

  function icon(name, cls) {
    const svg = document.createElementNS(SVG_NS, "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("class", "icon" + (cls ? " " + cls : ""));
    svg.setAttribute("aria-hidden", "true");
    svg.setAttribute("focusable", "false");
    for (const part of ICONS[name] || ICONS.info) {
      let el;
      if (typeof part === "string") {
        el = document.createElementNS(SVG_NS, "path");
        el.setAttribute("d", part);
      } else if (part.c) {
        el = document.createElementNS(SVG_NS, "circle");
        ["cx", "cy", "r"].forEach((k, i) => el.setAttribute(k, part.c[i]));
      } else if (part.l) {
        el = document.createElementNS(SVG_NS, "line");
        ["x1", "y1", "x2", "y2"].forEach((k, i) => el.setAttribute(k, part.l[i]));
      } else if (part.r) {
        el = document.createElementNS(SVG_NS, "rect");
        ["x", "y", "width", "height", "rx"].forEach((k, i) => el.setAttribute(k, part.r[i]));
      } else if (part.p) {
        el = document.createElementNS(SVG_NS, "polyline");
        el.setAttribute("points", part.p);
      }
      svg.appendChild(el);
    }
    return svg;
  }

  class ApiError extends Error {
    constructor(message, status, detail) {
      super(message);
      this.status = status;
      this.detail = detail;
    }
  }

  async function api(path, method = "GET", body = null) {
    let res;
    try {
      res = await fetch(path, {
        method,
        headers: {"X-DA-Token": TOKEN, "Content-Type": "application/json"},
        body: body === null ? undefined : JSON.stringify(body),
      });
    } catch (e) {
      throw new ApiError("프로그램과 연결할 수 없습니다. 프로그램이 실행 중인지 확인하세요.", 0, null);
    }
    let data = null;
    try { data = await res.json(); } catch (e) { data = null; }
    if (!res.ok) {
      const detail = data ? data.detail : null;
      let message = "요청을 처리하지 못했습니다. 잠시 후 다시 시도하세요.";
      if (typeof detail === "string") message = detail;
      else if (detail && typeof detail.message === "string") message = detail.message;
      throw new ApiError(message, res.status, detail);
    }
    return data;
  }

  // buttons, badges, inputs

  function button(label, opts = {}) {
    const cls = ["btn", opts.variant || "", opts.size || "", label ? "" : "icon-only"].filter(Boolean).join(" ");
    const node = h("button", {
      type: "button", class: cls, title: opts.title, "aria-label": opts.ariaLabel || (label ? null : opts.title),
      disabled: opts.disabled, onclick: opts.onClick,
    }, opts.icon ? icon(opts.icon, opts.size === "sm" ? "sm" : "") : null, label || null);
    return node;
  }

  // Runs an async action with the button disabled and a spinner, reporting errors as toasts.
  async function busy(btn, fn) {
    const original = Array.from(btn.childNodes);
    btn.disabled = true;
    set(btn, icon("loader", "spin sm"), ...original.filter(n => n.nodeType === 3));
    try {
      return await fn();
    } catch (e) {
      toast(e.message || String(e), "danger");
      return undefined;
    } finally {
      btn.disabled = false;
      set(btn, ...original);
    }
  }

  function badge(kind, iconName, text, extra) {
    return h("span", {class: "badge " + kind + (extra ? " " + extra : "")}, iconName ? icon(iconName) : null, text);
  }

  function field(label, input, help, opts = {}) {
    const id = input.id || ("f" + Math.random().toString(36).slice(2, 9));
    input.id = id;
    return h("div", {class: "field"},
      h("label", {class: "field-label", for: id}, label, opts.required ? h("span", {class: "muted"}, "(필수)") : null),
      input,
      help ? h("div", {class: "field-help"}, help) : null);
  }

  function input(attrs) { return h("input", Object.assign({class: "input", type: "text"}, attrs)); }
  function textarea(attrs) { return h("textarea", Object.assign({class: "textarea"}, attrs)); }
  function select(options, value, attrs) {
    const node = h("select", Object.assign({class: "select"}, attrs),
      ...options.map(([v, label]) => h("option", {value: v}, label)));
    if (value !== undefined && value !== null) node.value = value;
    return node;
  }

  function toggle(label, checked, help) {
    const box = h("input", {type: "checkbox", role: "switch", checked});
    const node = h("label", {class: "switch"}, box, h("span", {class: "track", "aria-hidden": "true"}),
      h("span", {class: "switch-text"}, h("span", {class: "strong"}, label), help ? h("span", {class: "small muted"}, help) : null));
    node.input = box;
    return node;
  }

  function segmented(options, value, onChange, ariaLabel) {
    const group = h("div", {class: "segmented", role: "group", "aria-label": ariaLabel || null});
    let current = value;
    const buttons = options.map(([v, label, iconName]) => h("button", {
      type: "button", "aria-pressed": String(v === value),
      onclick: () => {
        current = v;
        buttons.forEach(b => b.setAttribute("aria-pressed", String(b.dataset.v === v)));
        if (onChange) onChange(v);
      }, "data-v": v,
    }, iconName ? icon(iconName, "sm") : null, label));
    append(group, buttons);
    group.value = () => current;
    return group;
  }

  function callout(kind, iconName, title, body, actions) {
    return h("div", {class: "callout " + kind, role: kind === "danger" ? "alert" : null},
      icon(iconName),
      h("div", {class: "callout-body"}, title ? h("strong", {}, title) : null,
        body ? (typeof body === "string" ? h("p", {}, body) : body) : null,
        actions ? h("div", {class: "row", style: {marginTop: "6px"}}, actions) : null));
  }

  function progressBar(done, total, label, kind) {
    const pct = total > 0 ? Math.min(100, Math.round((done / total) * 100)) : 0;
    return h("div", {class: "progress-row"},
      h("div", {class: "row-between"}, h("span", {class: "muted"}, label), h("span", {class: "strong"}, done + " / " + total)),
      h("div", {class: "progress" + (kind ? " " + kind : ""), role: "progressbar", "aria-label": label,
        "aria-valuemin": "0", "aria-valuemax": String(total), "aria-valuenow": String(done)},
        h("div", {class: "progress-fill", style: {width: pct + "%"}})));
  }

  function skeleton(lines = 4) {
    return h("div", {class: "card skeleton-card", "aria-busy": "true", "aria-label": "불러오는 중"},
      ...Array.from({length: lines}, (_, i) => h("div", {class: "skeleton", style: {width: (90 - i * 12) + "%"}})));
  }

  function emptyState({icon: iconName, title, text, action, neutral}) {
    return h("div", {class: "empty"},
      h("div", {class: "empty-icon" + (neutral ? " neutral" : "")}, icon(iconName || "info", "xl")),
      h("h3", {}, title), text ? h("p", {}, text) : null, action || null);
  }

  function errorState(message, retry) {
    const node = emptyState({icon: "alert", title: "화면을 불러오지 못했습니다", text: message,
      action: retry ? button("다시 시도", {icon: "refresh", onClick: retry}) : null});
    node.classList.add("error-state");
    node.setAttribute("role", "alert");
    return node;
  }

  // toasts

  function toast(message, kind = "info") {
    let box = document.getElementById("toasts");
    if (!box) {
      box = h("div", {id: "toasts", class: "toasts", role: "status", "aria-live": "polite"});
      document.body.appendChild(box);
    }
    const iconName = {success: "check-circle", danger: "x-circle", warning: "alert", info: "info"}[kind] || "info";
    const node = h("div", {class: "toast " + kind}, icon(iconName), h("div", {class: "toast-text"}, message),
      h("button", {class: "btn ghost icon-only sm", type: "button", "aria-label": "닫기", onclick: () => node.remove()}, icon("x", "sm")));
    box.appendChild(node);
    setTimeout(() => node.remove(), kind === "danger" ? 8000 : 4500);
  }

  // modal and drawer

  const openLayers = [];

  function trapFocus(container, event) {
    if (event.key !== "Tab") return;
    const items = Array.from(container.querySelectorAll(
      "button:not([disabled]), [href], input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex='-1'])"))
      .filter(el => el.offsetParent !== null);
    if (items.length === 0) return;
    const first = items[0], last = items[items.length - 1];
    if (event.shiftKey && document.activeElement === first) { last.focus(); event.preventDefault(); }
    else if (!event.shiftKey && document.activeElement === last) { first.focus(); event.preventDefault(); }
  }

  function layer(overlayClass, panel, onClose) {
    const opener = document.activeElement;
    const overlay = h("div", {class: "overlay " + overlayClass});
    overlay.appendChild(panel);
    let closed = false;
    const close = () => {
      if (closed) return;
      closed = true;
      overlay.remove();
      const i = openLayers.indexOf(close);
      if (i >= 0) openLayers.splice(i, 1);
      if (onClose) onClose();
      if (opener && opener.focus && document.contains(opener)) opener.focus();
    };
    overlay.addEventListener("mousedown", (e) => { if (e.target === overlay) close(); });
    overlay.addEventListener("keydown", (e) => {
      if (e.key === "Escape") { e.stopPropagation(); close(); }
      trapFocus(panel, e);
    });
    document.body.appendChild(overlay);
    openLayers.push(close);
    setTimeout(() => {
      const target = panel.querySelector("[autofocus]") || panel.querySelector("input, textarea, select, button");
      if (target) target.focus();
    }, 20);
    return close;
  }

  // plain: the Figma "확인 모달" look (no footer bar, main action on the left, others on the right).
  function modal({title, body, actions = [], wide = false, onClose, icon: iconName, plain = false}) {
    const titleId = "m" + Math.random().toString(36).slice(2, 8);
    const foot = h("div", {class: "modal-foot"});
    const panel = h("div", {class: "modal" + (wide ? " wide" : "") + (plain ? " plain" : ""), role: "dialog", "aria-modal": "true", "aria-labelledby": titleId},
      h("div", {class: "modal-head"}, iconName ? icon(iconName, "lg") : null, h("h2", {id: titleId}, title),
        h("button", {type: "button", class: "btn ghost icon-only sm", "aria-label": "닫기", onclick: () => close()}, icon("x"))),
      h("div", {class: "modal-body"}, body));
    const close = layer("", panel, onClose);
    const setActions = (list) => {
      set(foot, ...list.map(a => {
        const b = button(a.label, {variant: a.variant, icon: a.icon, disabled: a.disabled});
        b.addEventListener("click", async () => {
          if (!a.onClick) { close(); return; }
          let failed = false;
          const result = await busy(b, async () => {
            try { return await a.onClick(close); } catch (e) { failed = true; throw e; }
          });
          if (!failed && result !== false && a.closes !== false) close();
        });
        return b;
      }));
      if (list.length && !foot.parentNode) panel.appendChild(foot);
      if (!list.length) foot.remove();
    };
    setActions(actions);
    close.setActions = setActions;
    close.panel = panel;
    return close;
  }

  function confirmDialog({title, message, confirmLabel = "확인", danger = false, icon: iconName}) {
    return new Promise((resolve) => {
      let answered = false;
      modal({
        title, icon: iconName || (danger ? "alert" : "info"),
        body: typeof message === "string" ? h("p", {}, message) : message,
        onClose: () => { if (!answered) resolve(false); },
        actions: [
          {label: "취소", onClick: () => { answered = true; resolve(false); }},
          {label: confirmLabel, variant: danger ? "danger" : "primary", onClick: () => { answered = true; resolve(true); }},
        ],
      });
    });
  }

  function drawer({title, subtitle, body, foot, onClose}) {
    const titleId = "d" + Math.random().toString(36).slice(2, 8);
    const panel = h("aside", {class: "drawer", role: "dialog", "aria-modal": "true", "aria-labelledby": titleId},
      h("div", {class: "drawer-head"},
        h("div", {class: "stack-sm", style: {flex: "1 1 auto", minWidth: "0"}}, h("h2", {id: titleId}, title), subtitle || null),
        h("button", {type: "button", class: "btn ghost icon-only sm", "aria-label": "닫기", onclick: () => close()}, icon("x"))),
      h("div", {class: "drawer-body"}, body),
      foot ? h("div", {class: "drawer-foot"}, foot) : null);
    const close = layer("drawer-overlay", panel, onClose);
    return close;
  }

  function anyLayerOpen() { return openLayers.length > 0; }

  // clipboard

  async function copyText(text) {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      const area = h("textarea", {style: {position: "fixed", opacity: "0"}});
      area.value = text;
      document.body.appendChild(area);
      area.select();
      document.execCommand("copy");
      area.remove();
    }
    toast("복사했습니다.", "success");
  }

  // theme

  function applyTheme(choice) {
    let theme = choice || "system";
    try { localStorage.setItem("da.theme", theme); } catch (e) { /* storage may be unavailable */ }
    if (theme === "system") {
      theme = window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
    }
    document.documentElement.setAttribute("data-theme", theme);
  }

  function initTheme() {
    let saved = "system";
    try { saved = localStorage.getItem("da.theme") || "system"; } catch (e) { saved = "system"; }
    applyTheme(saved);
    if (window.matchMedia) {
      window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", () => {
        let current = "system";
        try { current = localStorage.getItem("da.theme") || "system"; } catch (e) { current = "system"; }
        if (current === "system") applyTheme("system");
      });
    }
  }

  // formatting and the honest status model

  function fmtTime(iso) {
    if (!iso) return "-";
    return String(iso).replace("T", " ").slice(0, 16);
  }

  function relTime(iso) {
    if (!iso) return "-";
    const t = new Date(String(iso).replace(" ", "T"));
    if (isNaN(t.getTime())) return fmtTime(iso);
    const diff = (Date.now() - t.getTime()) / 1000;
    if (diff < 60) return "방금 전";
    if (diff < 3600) return Math.floor(diff / 60) + "분 전";
    if (diff < 86400) return Math.floor(diff / 3600) + "시간 전";
    if (diff < 86400 * 7) return Math.floor(diff / 86400) + "일 전";
    return fmtTime(iso);
  }

  // Maps overall().state (plus pause) to the Figma status badges. "검사 완료" stays tied to
  // verified: only a real model that checked everything is shown as 정상 / 검사 완료.
  const STATES = {
    verified: {kind: "success", cls: "ok", icon: "check", short: "정상"},
    demo: {kind: "warning", cls: "demo", icon: "alert-circle", short: "시연용 모드"},
    failed: {kind: "danger", cls: "error", icon: "alert-circle", short: "오류"},
    retrying: {kind: "warning", cls: "attention", icon: "alert-circle", short: "확인 필요"},
    running: {kind: "info", cls: "busy", icon: "loader", short: "확인 중"},
    stale: {kind: "warning", cls: "attention", icon: "alert-circle", short: "확인 필요"},
    incomplete: {kind: "info", cls: "busy", icon: "clock", short: "확인 대기"},
    no_folders: {kind: "neutral", cls: "paused", icon: "folder", short: "폴더 없음"},
    paused: {kind: "neutral", cls: "paused", icon: "pause-bars", short: "일시 중지"},
  };

  // The state shown for the agent as a whole: pause wins, then the honest overall() state.
  function agentState(st) {
    if (!st) return "failed";
    if (st.paused) return "paused";
    return st.overall.state;
  }

  function stateInfo(overall, paused) {
    const key = paused ? "paused" : overall.state;
    const base = STATES[key] || {kind: "neutral", cls: "paused", icon: "info", short: overall.label};
    return Object.assign({key}, base, {label: overall.label, detail: overall.detail});
  }

  function stateBadge(overall, size, paused) {
    const info = stateInfo(overall, paused);
    const node = h("span", {class: "badge state " + info.cls + (size ? " " + size : "")}, icon(info.icon), info.short);
    if (info.key === "running") node.querySelector("svg").classList.add("spin");
    node.title = info.label + (info.detail ? " " + info.detail : "");
    return node;
  }

  // Calm notice, not an alarm: sending to an external AI is a choice the user made.
  function transmissionBadge(provider) {
    if (provider.sends_externally) {
      const node = badge("info", "cloud", "외부 AI로 전송 중");
      node.title = provider.id + ": " + provider.reason;
      return node;
    }
    const node = badge("success", "shield-check", "외부 전송 안 함");
    node.title = "문서가 이 컴퓨터 밖으로 나가지 않습니다. " + provider.reason;
    return node;
  }

  // Display wording only; the stored values stay INCONSISTENT / UNCERTAIN / CONSISTENT.
  const LABELS = {INCONSISTENT: "반영 안 됨", UNCERTAIN: "확인 필요", CONSISTENT: "문제없음"};
  const LABEL_STYLE = {
    INCONSISTENT: ["danger", "alert-circle"], UNCERTAIN: ["warning", "alert-circle"], CONSISTENT: ["success", "check"],
  };
  function labelBadge(label) {
    const [kind, iconName] = LABEL_STYLE[label] || ["neutral", "info"];
    return badge(kind, iconName, LABELS[label] || label);
  }
  const KINDS = {REQUIREMENT: "요구사항", TASK: "작업", TEST: "테스트", SCHEDULE: "일정", MEETING: "회의"};

  // Short judge names used in the sidebar status line and settings ("로컬 AI · 정상").
  function providerKind(id) {
    if (!id || id.startsWith("mock")) return "demo";
    if (id.startsWith("ollama")) return "local";
    return "external";
  }

  function providerName(id) {
    if (!id) return "-";
    return {demo: "시연용 판정기", local: "로컬 AI", external: "외부 AI"}[providerKind(id)];
  }

  return {
    h, set, append, icon, api, ApiError, button, busy, badge, field, input, textarea, select, toggle, segmented, callout,
    progressBar, skeleton, emptyState, errorState, toast, modal, confirmDialog, drawer, anyLayerOpen, copyText,
    applyTheme, initTheme, fmtTime, relTime, stateInfo, stateBadge, transmissionBadge, labelBadge, LABELS, KINDS,
    providerName, providerKind, agentState, STATES,
  };
})();
