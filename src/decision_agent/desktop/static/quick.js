/* Quick-check window (480x640, always on top): note a decision during a meeting and see what
   it may touch. "관련 항목 찾기" saves nothing; "메모로 저장" adds a note to the next check. */
"use strict";

(() => {
  const {h, set, icon, api, button, busy, badge, textarea, callout, toast, stateBadge, transmissionBadge, labelBadge, LABELS} = DA;
  const root = document.getElementById("quick");
  const status = h("div", {class: "row"});
  const text = textarea({rows: "4", placeholder: "예: 발표는 다음 주 월요일로 연기한다", "aria-label": "방금 정한 결정"});
  const out = h("div", {class: "stack-sm"});
  const now = h("div", {class: "stack-sm"});
  const query = h("input", {class: "input", type: "search", placeholder: "결정, 파일, 담당자, 메모 검색", "aria-label": "검색"});
  const searchOut = h("div", {class: "stack-sm"});

  const find = button("관련 항목 찾기", {variant: "primary", icon: "search"});
  find.addEventListener("click", () => busy(find, async () => {
    const r = await api("/api/quick/preview", "POST", {text: text.value});
    set(out, 
      h("section", {class: "card stack-sm"}, h("h3", {}, "비슷한 기존 결정"),
        r.decisions.length === 0 ? h("p", {class: "small muted"}, "비슷한 결정이 없습니다.")
          : h("div", {class: "list"}, ...r.decisions.map(d => h("div", {class: "list-item"}, h("span", {class: "chip"}, d.date || "-"), h("div", {class: "list-main"}, d.text))))),
      h("section", {class: "card stack-sm"}, h("h3", {}, "영향받을 수 있는 항목"),
        h("p", {class: "small muted"}, "글자 비교로만 찾았습니다. AI 확인은 하지 않았습니다."),
        r.artifacts.length === 0 ? h("p", {class: "small muted"}, "관련 항목을 찾지 못했습니다.")
          : h("div", {class: "list"}, ...r.artifacts.map(a => h("div", {class: "list-item"}, a.label ? labelBadge(a.label) : null,
            h("div", {class: "list-main"}, h("span", {}, a.text), h("span", {class: "list-sub"}, a.source_doc)))))));
  }));

  const save = button("메모로 저장", {icon: "note"});
  save.addEventListener("click", () => busy(save, async () => {
    await api("/api/notes", "POST", {text: text.value, date: null});
    set(out, callout("success", "check-circle", "메모로 저장했습니다", "다음 점검에 포함합니다. 결과는 대시보드에서 확인하세요."));
    text.value = "";
  }));

  let timer = null;
  query.addEventListener("input", () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      const q = query.value.trim();
      if (!q) { set(searchOut, ); return; }
      let r;
      try { r = await api("/api/search?q=" + encodeURIComponent(q)); } catch (e) { toast(e.message, "danger"); return; }
      const total = r.decisions.length + r.findings.length + r.files.length + r.notes.length;
      set(searchOut, total === 0 ? h("p", {class: "small muted"}, "결과가 없습니다.") : h("div", {class: "list"},
        ...r.decisions.map(d => h("div", {class: "list-item"}, icon("results", "sm"), h("div", {class: "list-main"}, d.latest, h("span", {class: "list-sub"}, d.date || "")))),
        ...r.findings.map(f => h("div", {class: "list-item"}, labelBadge(f.label), h("div", {class: "list-main"}, f.text, h("span", {class: "list-sub"}, f.source_doc)))),
        ...r.files.map(p => h("div", {class: "list-item"}, icon("file", "sm"), h("div", {class: "list-main mono"}, p))),
        ...r.notes.map(n => h("div", {class: "list-item"}, icon("note", "sm"), h("div", {class: "list-main"}, n.text, h("span", {class: "list-sub"}, n.date))))));
    }, 250);
  });

  async function loadNow() {
    try {
      const [st, rows] = await Promise.all([api("/api/status"), api("/api/findings")]);
      set(status, stateBadge(st.overall), transmissionBadge(st.provider));
      const bad = rows.filter(r => r.label === "INCONSISTENT").slice(0, 6);
      set(now, bad.length === 0
        ? h("p", {class: "small muted"}, "지금 반영되지 않은 것 같은 항목이 없습니다.")
        : h("div", {class: "list"}, ...bad.map(r => h("div", {class: "list-item"}, badge("danger", "x-circle", LABELS[r.label]),
          h("div", {class: "list-main"}, h("span", {}, r.text), h("span", {class: "list-sub"}, r.source_doc + (r.owner ? " · 담당 " + r.owner : "")))))));
    } catch (e) {
      set(status, badge("danger", "x-circle", "프로그램과 연결이 끊겼습니다"));
    }
  }

  set(root, 
    h("div", {class: "quick-head"}, h("h2", {}, "빠른 점검")),
    status,
    h("section", {class: "card stack-sm"}, h("label", {class: "field-label", for: "quick-text"}, "방금 정한 결정"), text,
      h("div", {class: "row"}, find, save),
      h("p", {class: "small muted"}, "'관련 항목 찾기'는 아무것도 저장하지 않습니다.")),
    out,
    h("section", {class: "card stack-sm"}, h("h3", {}, "반영되지 않은 것 같은 항목"), now),
    h("section", {class: "card stack-sm"}, h("h3", {}, "찾아보기"), query, searchOut));
  text.id = "quick-text";

  DA.initTheme();
  loadNow();
  setInterval(loadNow, 8000);
  window.addEventListener("focus", () => { loadNow(); text.focus(); });
  text.focus();
})();
