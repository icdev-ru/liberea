"use strict";
const $ = (s, el = document) => el.querySelector(s);
const $$ = (s, el = document) => [...el.querySelectorAll(s)];

function toast(msg) {
  const t = $("#toast");
  t.textContent = msg;
  t.classList.add("on");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.remove("on"), 2600);
}

// ------------------------------------------------ индикатор «идёт работа»
const busy = {
  n: 0, t: null,
  on() {
    this.n++;
    // страховка: если страница так и не сменилась (например, началось скачивание) — убрать индикатор
    clearTimeout(this.guard);
    this.guard = setTimeout(() => this.reset(), 20000);
    if (!this.t) this.t = setTimeout(() => { const el = $("#busy"); if (el) el.hidden = false; }, 250);
  },
  off() {
    this.n = Math.max(0, this.n - 1);
    if (!this.n) this.reset();
  },
  reset() {
    this.n = 0; clearTimeout(this.t); this.t = null;
    const el = $("#busy"); if (el) el.hidden = true;
  },
};
// переходы по ссылкам и отправка форм — страница грузится
document.addEventListener("click", (e) => {
  if (e.defaultPrevented || e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
  const a = e.target.closest("a[href]");
  if (!a || a.target === "_blank" || a.hasAttribute("download")) return;
  const href = a.getAttribute("href");
  if (!href || href.startsWith("#") || href.startsWith("javascript:") && !href.includes("history")) return;
  if (/\/(download\.|open\/)|\.json(\?|$)|\/file(\?|$)/.test(href)) return;  // скачивание — страница не меняется
  if (a.origin && a.origin !== location.origin) return;
  busy.on();
});
document.addEventListener("submit", (e) => { if (!e.defaultPrevented) busy.on(); });
window.addEventListener("pageshow", () => busy.reset());
// переключатель «Карточки / Только обложки» не засоряет историю — «назад» ведёт на предыдущую страницу
document.addEventListener("click", (e) => {
  const a = e.target.closest(".view-seg a");
  if (!a || e.ctrlKey || e.metaKey) return;
  e.preventDefault();
  busy.on();
  location.replace(a.href);
});

async function post(url, data) {
  const body = new URLSearchParams(data);
  busy.on();
  try {
    const r = await fetch(url, { method: "POST", body, credentials: "same-origin" });
    return await r.json();
  } catch (err) {
    return { ok: false, error: "Нет связи с сервером" };
  } finally {
    busy.off();
  }
}

function setActive(res) {
  if (!res || !res.ok) return;
  const chip = $("#active-chip");
  $("#active-name").textContent = res.name;
  $("#active-count").textContent = res.count;
  chip.href = "/my/" + res.id;
  chip.classList.remove("pulse"); void chip.offsetWidth; chip.classList.add("pulse");
}

// ------------------------------------------------ добавление в подборку
document.addEventListener("click", async (e) => {
  const add = e.target.closest(".btn-add");
  if (add) {
    const id = add.dataset.book;
    const inColl = add.classList.contains("is-in");
    const res = await post(inColl ? "/api/collect/remove" : "/api/collect/add", { book_id: id });
    if (!res.ok) return toast(res.error || "Ошибка");
    setActive(res);
    $$(`.btn-add[data-book="${id}"]`).forEach((b) => {
      b.classList.toggle("is-in", !inColl);
      b.textContent = b.dataset.short ? (inColl ? "+" : "✓") : (inColl ? "+ В подборку" : "✓ В подборке");
    });
    toast(inColl ? `Убрано из «${res.name}»` : `Добавлено в «${res.name}» (${res.count})`);
    return;
  }
  const all = e.target.closest("#add-all");
  if (all) {
    all.disabled = true;
    const res = await post("/api/collect/add-search", { q: all.dataset.q || "", scope: all.dataset.scope || "all", lib: all.dataset.lib || "", lang: all.dataset.lang || "",
      author: all.dataset.author || "", series: all.dataset.series || "" });
    all.disabled = false;
    if (!res.ok) return toast(res.error || "Ошибка");
    setActive(res);
    $$(".btn-add").forEach((b) => { b.classList.add("is-in"); b.textContent = b.dataset.short ? "✓" : "✓ В подборке"; });
    toast(`Добавлено новых: ${res.added}. В «${res.name}» теперь ${res.count}`);
    return;
  }
  const copy = e.target.closest("[data-copy]");
  if (copy) {
    const txt = $(copy.dataset.copy).textContent.trim();
    try { await navigator.clipboard.writeText(txt); toast("Скопировано"); }
    catch { prompt("Скопируйте ключ:", txt); }
  }
});

document.addEventListener("change", async (e) => {
  const t = e.target.closest(".coll-toggle");
  if (!t) return;
  const res = await post(t.checked ? "/api/collect/add" : "/api/collect/remove",
    { book_id: t.dataset.book, collection_id: t.dataset.coll });
  toast(res.ok ? (t.checked ? `Добавлено в «${res.name}»` : `Убрано из «${res.name}»`) : "Ошибка");
});

// ------------------------------------------------ подборка: порядок и удаление
(function collection() {
  const list = $("#coll-list");
  if (!list) return;
  const cid = list.dataset.coll;
  let saveT;
  const save = () => {
    clearTimeout(saveT);
    saveT = setTimeout(async () => {
      const ids = $$("li", list).map((li) => li.dataset.id);
      await fetch(`/api/my/${cid}/order`, { method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ids }) });
      toast("Порядок сохранён");
    }, 400);
  };
  let drag = null;
  list.addEventListener("dragstart", (e) => { drag = e.target.closest("li"); drag.classList.add("dragging"); });
  list.addEventListener("dragend", () => { drag && drag.classList.remove("dragging"); $$(".over", list).forEach((x) => x.classList.remove("over")); drag = null; save(); });
  list.addEventListener("dragover", (e) => {
    e.preventDefault();
    const li = e.target.closest("li");
    if (!li || li === drag) return;
    const r = li.getBoundingClientRect();
    li.parentNode.insertBefore(drag, e.clientY > r.top + r.height / 2 ? li.nextSibling : li);
  });
  list.addEventListener("click", async (e) => {
    const li = e.target.closest("li");
    if (!li) return;
    if (e.target.closest(".up") && li.previousElementSibling) { list.insertBefore(li, li.previousElementSibling); save(); }
    if (e.target.closest(".down") && li.nextElementSibling) { list.insertBefore(li.nextElementSibling, li); save(); }
    const rm = e.target.closest(".remove");
    if (rm) {
      const res = await post("/api/collect/remove", { book_id: rm.dataset.book, collection_id: rm.dataset.coll });
      if (res.ok) { li.remove(); $("#coll-count").textContent = res.count;
        if (String(res.id) === ($("#active-chip").getAttribute("href") || "").split("/").pop()) setActive(res); }
    }
  });
})();

// ------------------------------------------------ сборка PDF/FB2
document.addEventListener("click", async (e) => {
  const b = e.target.closest(".build");
  if (!b) return;
  const box = $("#build-status");
  $$(".build").forEach((x) => (x.disabled = true));
  box.hidden = false;
  box.innerHTML = `<div class="bar"><i></i></div><div>Сборка ${b.dataset.fmt.toUpperCase()}…</div>`;
  const ruBox = $("#build-ru");
  const res = await post(`/api/my/${b.dataset.coll}/build`, { fmt: b.dataset.fmt, ru: ruBox && ruBox.checked ? 1 : 0 });
  if (!res.ok) { box.textContent = res.error || "Ошибка"; $$(".build").forEach((x) => (x.disabled = false)); return; }
  const poll = async () => {
    const j = await (await fetch(`/api/jobs/${res.job}`)).json();
    const pct = j.total ? Math.round((100 * j.done) / j.total) : 0;
    if (j.status === "done") {
      // файл не скачивается сам — только по кнопке (иначе получалось два скачивания)
      box.innerHTML = `Готово: <a class="btn" href="/jobs/${j.id}/file" download>Скачать ${j.fmt.toUpperCase()}</a>`;
      $$(".build").forEach((x) => (x.disabled = false));
      return;
    }
    if (j.status === "error") {
      box.textContent = "Ошибка сборки: " + j.error;
      $$(".build").forEach((x) => (x.disabled = false));
      return;
    }
    box.innerHTML = `<div class="bar"><i style="width:${pct}%"></i></div><div>Сборка ${j.fmt.toUpperCase()}: ${j.done} из ${j.total} книг…</div>`;
    setTimeout(poll, 800);
  };
  poll();
});

// ------------------------------------------------ «Поделиться» (только на телефонах)
// Файл книги передаётся через системное меню «Поделиться» (Web Share API). Браузер разрешает это
// только на защищённых адресах (https или localhost); на http://192.168.x.x можно поделиться ссылкой.
(function initShare() {
  const MIME = { fb2: "application/x-fictionbook+xml", pdf: "application/pdf", txt: "text/plain" };
  const ready = {};   // подготовленные файлы: если жест пользователя «истёк» пока файл скачивался

  const fileName = (r, fallback) => {
    const cd = r.headers.get("Content-Disposition") || "";
    let m = cd.match(/filename\*=UTF-8''([^;]+)/i);
    if (m) return decodeURIComponent(m[1]);
    m = cd.match(/filename="?([^";]+)"?/i);
    return m ? m[1] : fallback;
  };
  const copy = async (text) => {
    try { await navigator.clipboard.writeText(text); return true; } catch (e) {}
    const ta = document.createElement("textarea");
    ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
    document.body.appendChild(ta); ta.select();
    let ok = false; try { ok = document.execCommand("copy"); } catch (e) {}
    ta.remove(); return ok;
  };
  const shareLink = async (title, url) => {
    if (navigator.share) {
      try { await navigator.share({ title, text: title, url }); } catch (e) {}
      return;
    }
    if (/Android/i.test(navigator.userAgent)) {
      location.href = "intent:#Intent;action=android.intent.action.SEND;type=text/plain;S.android.intent.extra.TEXT=" +
        encodeURIComponent(title + "\n" + url) + ";end";
      return;
    }
    toast((await copy(url)) ? "Ссылка скопирована" : url);
  };

  document.addEventListener("click", async (e) => {
    const it = e.target.closest(".share-item");
    if (!it) {
      $$("details.share[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
      return;
    }
    const d = it.closest(".share");
    d.open = false;
    const fmt = it.dataset.fmt, title = d.dataset.title, ru = d.dataset.ru === "1";
    if (fmt === "link") return shareLink(title, new URL(`/book/${d.dataset.book}`, location.href).href);
    const url = new URL(`/book/${d.dataset.book}/download.${fmt}${ru ? "?ru=1" : ""}`, location.href).href;
    if (!(navigator.share && navigator.canShare)) return shareLink(title, url);  // http: только ссылка
    let file = ready[url];
    if (!file) {
      busy.on();
      try {
        const r = await fetch(url, { credentials: "same-origin" });
        if (!r.ok) throw new Error("HTTP " + r.status);
        const blob = await r.blob();
        file = new File([blob], fileName(r, `book.${fmt}`), { type: MIME[fmt] });
      } catch (err) {
        busy.off(); return toast("Не удалось получить файл: " + err.message);
      }
      busy.off();
      if (!navigator.canShare({ files: [file] })) {
        return toast(`Формат ${fmt.toUpperCase()} этим браузером передать нельзя — выберите PDF или TXT`);
      }
      ready[url] = file;
    }
    try {
      await navigator.share({ files: [file], title });
      delete ready[url];
    } catch (err) {
      if (err.name === "NotAllowedError") toast("Файл готов — нажмите «Поделиться» ещё раз");
      else if (err.name !== "AbortError") toast("Не удалось поделиться: " + err.message);
    }
  });
})();

// ------------------------------------------------ перевод
function initTranslate() {
  const box = $("#translation");
  if (!box) return;
  const id = box.dataset.book;
  const watch = (api, bar, txt, status) => {
    if (!["queued", "running"].includes(status)) return;
    const poll = async () => {
      const s = await (await fetch(api)).json();
      if (s.status === "done" || s.status === "error") return location.reload();
      const pct = s.total ? Math.round((100 * (s.done || 0)) / s.total) : 0;
      const b = $(bar); if (b) b.style.width = pct + "%";
      const t = $(txt);
      if (t) t.textContent = s.status === "queued" ? `в очереди${s.position ? ": " + s.position + "-я" : ""}`
        : `переводится: ${pct}%` + (s.eta_min != null ? `, осталось ~${s.eta_min} мин` : "");
      setTimeout(poll, api.includes("/llm/") ? 5000 : 2000);
    };
    poll();
  };
  watch(`/api/translate/${id}`, "#tr-bar", "#tr-text", box.dataset.status);
  watch(`/api/llm/${id}`, "#llm-bar", "#llm-text", box.dataset.llm);
  const bind = (sel, api) => {
    const btn = $(sel);
    if (!btn) return;
    btn.onclick = async () => {
      btn.disabled = true;
      const r = await post(api, {});
      if (!r.ok) { btn.disabled = false; return toast(r.error || "Ошибка"); }
      location.reload();
    };
  };
  bind("#tr-start", `/api/translate/${id}`);
  bind("#llm-start", `/api/llm/${id}`);
}

document.addEventListener("click", async (e) => {
  const b = e.target.closest("#tr-all");
  if (!b) return;
  if (!confirm("Поставить в очередь перевода все найденные английские книги?")) return;
  b.disabled = true;
  const r = await post("/admin/translate-search", { q: b.dataset.q, scope: b.dataset.scope, lib: b.dataset.lib, lang: b.dataset.lang || "" });
  b.disabled = false;
  toast(r.ok ? `В очередь добавлено: ${r.queued}` : (r.error || "Ошибка"));
});

function initTrQueue() {
  const poll = async () => {
    try {
      const s = await (await fetch("/admin/translate/status")).json();
      $("#trq-done").textContent = s.done; $("#trq-queued").textContent = s.queued;
      const c = s.current;
      $("#trq-current").textContent = c ? `Сейчас: ${c.title} — ${Math.round(100 * c.done / (c.total || 1))}%` : "";
      if (s.queued || c) setTimeout(poll, 3000);
    } catch { /* сервер перезапускается */ }
  };
  poll();
}

// ------------------------------------------------ перевод из карточек и подборки
document.addEventListener("click", async (e) => {
  const go = e.target.closest(".tr-go");
  if (go) {
    go.disabled = true;
    const r = await post(`/api/translate/${go.dataset.book}`, {});
    if (!r.ok) { go.disabled = false; return toast(r.error || "Ошибка"); }
    const line = go.closest(".tr-line");
    if (line) line.innerHTML = '<span class="tr-badge wait">RU…</span> <span class="muted">в очереди на перевод</span>';
    toast("Книга поставлена в очередь на перевод");
    return;
  }
  const ct = e.target.closest("#coll-tr, #coll-llm");
  if (ct) {
    if (ct.id === "coll-llm" && !confirm("Поставить книги в очередь качественного перевода? Каждая книга — 1–2 часа.")) return;
    ct.disabled = true;
    const r = await post(`/api/my/${ct.dataset.coll}/${ct.id === "coll-llm" ? "llm" : "translate"}`, {});
    if (!r.ok) { ct.disabled = false; return toast(r.error || "Ошибка"); }
    toast(`В очередь на перевод: ${r.queued}`);
    setTimeout(() => location.reload(), 800);
  }
});

// ------------------------------------------------ админка
function initAdmin(running) {
  const box = $("#scan-status");
  const poll = async () => {
    const s = await (await fetch("/admin/scan/status")).json();
    box.hidden = false;
    const pct = s.total ? Math.round((100 * s.done) / s.total) : 0;
    $("#scan-bar").style.width = pct + "%";
    const lib = ((s.label || s.kind || "") + " " + (s.info || "")).trim();
    const t = `[${lib}] ${s.done || 0} / ${s.total || 0} · новых ${s.added || 0}, обновлено ${s.updated || 0}, без изменений ${s.unchanged || 0}, удалено ${s.removed || 0}, ошибок ${s.errors || 0}, предупреждений ${s.warnings || 0}`;
    $("#scan-text").textContent = s.running ? `Идёт сканирование: ${t} — ${s.current || ""}` : (s.message || "Готово: " + t);
    if (s.running) setTimeout(poll, 1000);
    else if (s.total !== undefined && !poll.reloaded) { poll.reloaded = true; setTimeout(() => location.reload(), 1500); }
  };
  const start = async (force, kind) => {
    const r = await post("/admin/scan", { force, kind });
    if (!r.ok) toast(r.error || "Сканирование уже идёт");
    poll.reloaded = false;
    poll();
  };
  $$(".scan-btn").forEach((b) => (b.onclick = () => {
    const force = b.dataset.force === "1";
    if (force && !confirm("Переиндексировать всю библиотеку? Это может занять время.")) return;
    start(b.dataset.force, b.dataset.kind);
  }));
  $("#clear-cache").onclick = async () => { const r = await post("/admin/cache/clear", {}); toast(`Удалено файлов: ${r.removed}`); };
  if (running) poll();
}

// ------------------------------------------------ поле поиска: без автозаполнения, крестик «очистить»
function initSearchFields() {
  $$('input[type="search"]').forEach((inp) => {
    inp.setAttribute("autocomplete", "off");
    if (inp.parentElement.classList.contains("clearable")) return;
    const wrap = document.createElement("span");
    wrap.className = "clearable";
    inp.parentNode.insertBefore(wrap, inp);
    wrap.appendChild(inp);
    const x = document.createElement("button");
    x.type = "button";
    x.className = "clear-x";
    x.setAttribute("aria-label", "Очистить");
    x.title = "Очистить";
    x.textContent = "×";
    wrap.appendChild(x);
    const sync = () => { x.hidden = !inp.value; };
    inp.addEventListener("input", sync);
    x.addEventListener("click", () => { inp.value = ""; sync(); inp.focus(); });
    sync();
  });
}
initSearchFields();

// ------------------------------------------------ тёмная / светлая тема
(function initTheme() {
  const btn = $("#theme-btn");
  if (!btn) return;
  const root = document.documentElement;
  const isDark = () => root.dataset.theme ? root.dataset.theme === "dark"
    : window.matchMedia("(prefers-color-scheme: dark)").matches;
  const label = () => { btn.title = isDark() ? "Светлая тема" : "Тёмная тема"; };
  btn.addEventListener("click", () => {
    const t = isDark() ? "light" : "dark";
    root.dataset.theme = t;
    try { localStorage.setItem("bl_theme", t); } catch (e) {}
    label();
  });
  label();
})();

// ------------------------------------------------ переименование подборки
(function initRename() {
  const btn = $("#rename-btn"), form = $("#rename-form"), title = $("#coll-title");
  if (btn && form) {
    btn.onclick = () => {
      title.hidden = true; form.hidden = false;
      const inp = form.querySelector("input"); inp.focus(); inp.select();
    };
    $("#rename-cancel").onclick = () => { form.hidden = true; title.hidden = false; };
  }
  document.addEventListener("click", (e) => {
    const r = e.target.closest(".rename-row");
    if (!r) return;
    const name = prompt("Новое название подборки:", r.dataset.name);
    if (name === null || !name.trim() || name.trim() === r.dataset.name) return;
    const f = document.createElement("form");
    f.method = "post"; f.action = `/my/${r.dataset.coll}/rename`;
    for (const [k, v] of [["name", name.trim()], ["back", "/my"]]) {
      const i = document.createElement("input"); i.type = "hidden"; i.name = k; i.value = v; f.appendChild(i);
    }
    document.body.appendChild(f); busy.on(); f.submit();
  });
})();
