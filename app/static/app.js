// CheckStock UI — тонкий клиент к JSON API. Правила учёта живут только на сервере:
// интерфейс показывает данные, отправляет операции и предлагает варианты при нехватке.

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = (v) => String(v ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
const num = (v, cls = "") => (v ? `<span class="num ${cls}">${v}</span>` : `<span class="num zero">0</span>`);
const uuid = () => (crypto.randomUUID ? crypto.randomUUID() : `${Date.now()}-${Math.random().toString(16).slice(2)}`);
const time = (iso) => new Date(iso).toLocaleString("ru-RU", { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
const orderNo = () => `WB-${String(Date.now()).slice(-6)}`;

const URLS = { receipt: "/api/receipts", transfer: "/api/transfers", fbo: "/api/fbo", fbs: "/api/fbs" };
const FORMS = [
  ["receipt", "Приход"],
  ["transfer", "Перемещение"],
  ["fbo", "FBO"],
  ["fbs", "FBS"],
];

const state = {
  tab: "stock",
  form: "receipt",
  meta: {},
  products: [],
  warehouses: [],
  stock: null,
  documents: [],
  history: [],
  reconcile: null,
  filter: { sku: "", warehouse_id: "", kind: "" },
  editing: null, // id ФФ, который сейчас переименовывают
  draft: null,
  // Idempotency-Key на каждую форму: живёт, пока данные формы не меняются,
  // поэтому двойная отправка или повтор после обрыва связи не проведут операцию дважды.
  keys: { receipt: uuid(), transfer: uuid(), fbo: uuid(), fbs: uuid() },
  pending: null, // отправка, ожидающая выбора варианта после нехватки
};

// ---------- API ----------

async function api(method, url, body, key) {
  const headers = { "Content-Type": "application/json" };
  if (key) headers["Idempotency-Key"] = key;
  const res = await fetch(url, { method, headers, body: body ? JSON.stringify(body) : undefined });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw Object.assign(new Error(data.message || `Ошибка ${res.status}`), { status: res.status, data });
  return data;
}

function historyUrl(params = state.filter, limit = 300) {
  const q = new URLSearchParams({ limit });
  for (const [k, v] of Object.entries(params)) if (v) q.set(k, v);
  return `/api/history?${q}`;
}

async function refresh() {
  const [stock, documents, warehouses, reconcile, history] = await Promise.all([
    api("GET", "/api/stock"),
    api("GET", "/api/documents"),
    api("GET", "/api/warehouses"),
    api("GET", "/api/reconcile"),
    api("GET", historyUrl()),
  ]);
  Object.assign(state, { stock, documents, warehouses, reconcile, history });
  render();
}

// ---------- helpers ----------

const active = () => state.warehouses.filter((w) => !w.deleted);
const whName = (id) => state.warehouses.find((w) => w.id === +id)?.name ?? `#${id}`;

function whOptions(selected, { activeOnly = false, exclude = null } = {}) {
  return state.warehouses
    .filter((w) => (!activeOnly || !w.deleted) && w.id !== +exclude)
    .map((w) => `<option value="${w.id}" ${w.id === +selected ? "selected" : ""}>${esc(w.name)}${w.deleted ? " (удалён)" : ""}</option>`)
    .join("");
}

function skuOptions(selected) {
  return state.products
    .map((p) => `<option value="${esc(p.sku)}" ${p.sku === selected ? "selected" : ""}>${esc(p.sku)} · ${esc(p.name)}</option>`)
    .join("");
}

function cellOf(sku, warehouseId) {
  const s = state.stock;
  const col = s.warehouses.findIndex((w) => w.id === +warehouseId);
  const row = s.rows.find((r) => r.sku === sku);
  return col < 0 || !row ? { on_hand: 0, available: 0, reserved: 0 } : row.cells[col];
}

function newDraft() {
  const [a, b] = active();
  const sku = state.products[0]?.sku ?? "";
  return {
    receipt: { lines: [{ sku, wh: a?.id ?? "", qty: "" }], reference: "" },
    transfer: { sku, src: a?.id ?? "", dst: b?.id ?? "", qty: "" },
    fbo: { sku, src: a?.id ?? "", qty: "", destination: "WB Коледино" },
    fbs: { sku, src: a?.id ?? "", qty: "", order_no: orderNo() },
  };
}

function toast(message, kind = "ok") {
  const el = document.createElement("div");
  el.className = `toast toast--${kind}`;
  el.textContent = message;
  $("[data-toasts]").append(el);
  setTimeout(() => el.remove(), kind === "error" ? 9000 : 6000);
}

function openModal(html) {
  const modal = $("[data-modal]");
  modal.innerHTML = `<div class="modal__box" role="dialog" aria-modal="true">${html}</div>`;
  modal.hidden = false;
}
function closeModal() {
  $("[data-modal]").hidden = true;
  state.pending = null;
}

// ---------- render ----------

function render() {
  renderHeader();
  for (const view of $$("[data-view]")) view.hidden = view.dataset.view !== state.tab;
  for (const tab of $$("[data-tab]")) tab.classList.toggle("is-active", tab.dataset.tab === state.tab);
  ({ stock: renderStock, ops: renderOps, warehouses: renderWarehouses, history: renderHistory })[state.tab]();
}

function renderHeader() {
  const r = state.reconcile;
  const badge = $("[data-reconcile]");
  badge.className = `badge ${r.ok ? "is-ok" : "is-bad"}`;
  badge.textContent = r.ok
    ? `Учёт сходится: принято ${r.received} = на ФФ ${r.on_ff} + в пути ${r.in_transit} + выбыло ${r.left}`
    : `Расхождение в учёте: ${r.mismatches.length}`;
  const open = state.documents.length;
  const count = $("[data-open-count]");
  count.hidden = !open;
  count.textContent = open;
}

function renderStock() {
  const s = state.stock;
  const t = s.totals;
  const transit = t.transit_ff + t.transit_fbo + t.transit_fbs;
  const head1 = s.warehouses
    .map((w) => `<th colspan="3" class="group">ФФ «${esc(w.name)}» ${w.deleted ? '<span class="tag">удалён</span>' : ""}</th>`)
    .join("");
  const head2 = s.warehouses
    .map(() => `<th class="n group">Физически</th><th class="n">Доступно</th><th class="n">Резерв FBS</th>`)
    .join("");
  const cells = (row) =>
    row.cells.map((c) => `<td class="n group">${num(c.on_hand)}</td><td class="n">${num(c.available)}</td><td class="n">${num(c.reserved, "v-res")}</td>`).join("");
  const tail = (row) => `
    <td class="n group">${num(row.transit_ff, "v-transit")}</td>
    <td class="n">${num(row.transit_fbo, "v-transit")}</td>
    <td class="n">${num(row.transit_fbs, "v-transit")}</td>
    <td class="n group"><b>${num(row.tracked)}</b></td>`;

  $('[data-view="stock"]').innerHTML = `
    <div class="view__head">
      <div><h2>Остатки</h2><p class="hint">Где товар, сколько доступно и что уже отправлено. Нажмите на строку — увидите, где числится артикул.</p></div>
    </div>
    <div class="stats">
      <div class="card stat"><span>Физически на ФФ</span><b class="num">${t.on_hand}</b><small>доступно + резерв FBS</small></div>
      <div class="card stat"><span>Доступно</span><b class="num">${t.available}</b><small>можно резервировать и отправлять</small></div>
      <div class="card stat"><span>В резерве FBS</span><b class="num">${t.reserved}</b><small>заблокировано до отгрузки или отмены</small></div>
      <div class="card stat"><span>В пути</span><b class="num">${transit}</b><small>между ФФ ${t.transit_ff} · на МП ${t.transit_fbo} · к клиенту ${t.transit_fbs}</small></div>
    </div>
    <div class="card table-wrap">
      <table class="stock">
        <thead>
          <tr><th colspan="2"></th>${head1}<th colspan="3" class="group">В пути</th><th class="group"></th></tr>
          <tr><th>Артикул</th><th>Товар</th>${head2}<th class="n group">Между ФФ</th><th class="n">На склад МП (FBO)</th><th class="n">К клиенту (FBS)</th><th class="n group">Всего на учёте</th></tr>
        </thead>
        <tbody>
          ${s.rows.map((r) => `<tr data-action="where" data-sku="${esc(r.sku)}"><td class="sku">${esc(r.sku)}</td><td>${esc(r.name)}</td>${cells(r)}${tail(r)}</tr>`).join("")}
        </tbody>
        <tfoot><tr><td colspan="2">Итого</td>${cells(t)}${tail(t)}</tr></tfoot>
      </table>
    </div>
    <div class="legend">
      <span><span class="v-res">■</span> резерв FBS — физически на ФФ, но не доступен</span>
      <span><span class="v-transit">■</span> в пути — уже не на ФФ, но ещё на нашем учёте</span>
      <span>Удалённый ФФ показывается, пока на нём что-то числится</span>
    </div>`;
}

// Черновик мог сослаться на удалённый или совпадающий ФФ — подставляем допустимый.
function normalizeDraft() {
  const d = state.draft;
  const exists = (id) => state.warehouses.some((w) => w.id === +id);
  const firstActive = (except) => active().find((w) => w.id !== +except)?.id ?? "";
  for (const line of d.receipt.lines) if (!active().some((w) => w.id === +line.wh)) line.wh = firstActive();
  for (const f of ["transfer", "fbo", "fbs"]) if (!exists(d[f].src)) d[f].src = firstActive();
  if (!active().some((w) => w.id === +d.transfer.dst && w.id !== +d.transfer.src)) d.transfer.dst = firstActive(d.transfer.src);
}

function renderOps() {
  normalizeDraft();
  const forms =FORMS.map(([key, label]) => `<button type="button" data-action="form" data-form="${key}" class="${state.form === key ? "is-active" : ""}">${label}</button>`).join("");
  $('[data-view="ops"]').innerHTML = `
    <div class="view__head"><div><h2>Операции</h2><p class="hint">Приход, перемещения между ФФ, отгрузки FBO и заказы FBS. Справа — документы в работе.</p></div></div>
    <div class="grid-2">
      <div class="card"><div class="card__body"><div class="seg">${forms}</div>${renderForm()}</div></div>
      <div class="card">${renderOpenDocs()}</div>
    </div>`;
  updateAvailability();
}

function renderForm() {
  const d = state.draft[state.form];
  const qty = (v) => `<div class="field"><label>Количество, шт.</label><input type="number" min="1" step="1" required data-field="qty" value="${esc(v)}"></div>`;
  const sku = (v) => `<div class="field"><label>Артикул</label><select data-field="sku">${skuOptions(v)}</select></div>`;
  const src = (v, label = "С какого ФФ") => `<div class="field"><label>${label}</label><select data-field="src">${whOptions(v)}</select><div class="avail" data-avail></div></div>`;

  switch (state.form) {
    case "receipt":
      return `<form data-form="receipt">
        <div class="lines">${d.lines.map((l, i) => `
          <div class="line">
            <select data-field="lines.${i}.sku" aria-label="Артикул">${skuOptions(l.sku)}</select>
            <select data-field="lines.${i}.wh" aria-label="ФФ">${whOptions(l.wh, { activeOnly: true })}</select>
            <input type="number" min="1" step="1" required placeholder="шт." aria-label="Количество" data-field="lines.${i}.qty" value="${esc(l.qty)}">
            <button type="button" class="btn btn--icon" data-action="remove-line" data-i="${i}" title="Убрать строку" ${d.lines.length === 1 ? "disabled" : ""}>✕</button>
          </div>`).join("")}
        </div>
        <div class="actions" style="margin-bottom:14px">
          <button type="button" class="btn btn--ghost btn--sm" data-action="add-line">+ Строка</button>
          <button type="button" class="btn btn--ghost btn--sm" data-action="fill-test-receipt">Заполнить тестовым приходом</button>
        </div>
        <div class="field"><label>Накладная поставщика (необязательно)</label><input data-field="reference" value="${esc(d.reference)}" placeholder="например, ПН-145"></div>
        <button class="btn btn--block">Провести приход</button>
        <p class="hint">Удалённые ФФ приход не принимают — их нет в списке.</p>
      </form>`;
    case "transfer":
      return `<form data-form="transfer">
        ${sku(d.sku)}
        <div class="row">${src(d.src, "Откуда")}<div class="field"><label>Куда</label><select data-field="dst">${whOptions(d.dst, { activeOnly: true, exclude: d.src })}</select></div></div>
        ${qty(d.qty)}
        <button class="btn btn--block">Отправить перемещение</button>
        <p class="hint">Товар сразу уходит с ФФ отправления и числится в пути до приёмки на ФФ назначения.</p>
      </form>`;
    case "fbo":
      return `<form data-form="fbo">
        <p class="hint" style="margin:0 0 12px">Отгрузка на склад маркетплейса по FBO</p>
        ${sku(d.sku)}${src(d.src)}
        <div class="row">${qty(d.qty)}<div class="field"><label>Склад маркетплейса</label><input required data-field="destination" value="${esc(d.destination)}"></div></div>
        <button class="btn btn--block">Отгрузить на склад МП</button>
        <p class="hint">Товар покидает ФФ и числится в пути, пока склад МП его не примет.</p>
      </form>`;
    case "fbs":
      return `<form data-form="fbs">
        <p class="hint" style="margin:0 0 12px">Резерв под заказ FBS: отгрузка и доставка — в списке «В работе»</p>
        ${sku(d.sku)}${src(d.src)}
        <div class="row">${qty(d.qty)}<div class="field"><label>Номер заказа</label><input required data-field="order_no" value="${esc(d.order_no)}"></div></div>
        <button class="btn btn--block">Зарезервировать</button>
        <p class="hint">Резерв блокирует товар на ФФ до отгрузки заказа или отмены.</p>
      </form>`;
  }
}

function updateAvailability() {
  const box = $("[data-avail]");
  if (!box) return;
  const d = state.draft[state.form];
  const c = cellOf(d.sku, d.src);
  box.innerHTML = `Доступно на «${esc(whName(d.src))}»: <b>${c.available} шт.</b>${c.reserved ? ` · в резерве FBS ${c.reserved}` : ""}`;
}

const DOC_GROUPS = [
  ["Резервы FBS", (d) => d.kind === "fbs" && d.status === "reserved", "tag--amber", (d) => [
    [`/api/fbs/${d.id}/ship`, "Отгрузить", "btn--sm"],
    [`/api/fbs/${d.id}/cancel`, "Отменить", "btn--sm btn--ghost"],
  ]],
  ["FBS: в пути к клиенту", (d) => d.kind === "fbs" && d.status === "shipped", "tag--blue", (d) => [[`/api/fbs/${d.id}/deliver`, "Доставлен", "btn--sm"]]],
  ["Перемещения в пути", (d) => d.kind === "transfer", "tag--blue", (d) => [[`/api/transfers/${d.id}/receive`, `Принять на «${d.dest.name}»`, "btn--sm"]]],
  ["FBO: в пути на склад МП", (d) => d.kind === "fbo", "tag--blue", (d) => [[`/api/fbo/${d.id}/accept`, "Принято складом МП", "btn--sm"]]],
];

function renderOpenDocs() {
  const docs = state.documents;
  const groups = DOC_GROUPS.map(([title, match, tag, actions]) => {
    const items = docs.filter(match);
    if (!items.length) return "";
    return `<div class="docs-group"><h4>${title} <span class="tag ${tag}">${items.length}</span></h4>
      ${items.map((d) => `
        <div class="doc">
          <div class="doc__main"><b>${esc(d.kind_label)} №${d.id}</b> · ${esc(d.sku)} ${esc(d.product_name)} · <b class="num">${d.qty} шт.</b>
            <div class="doc__route">${esc(d.route)} · ${time(d.updated_at)}</div></div>
          <div class="actions">${actions(d).map(([url, label, cls]) => `<button class="btn ${cls}" data-action="step" data-url="${url}">${esc(label)}</button>`).join("")}</div>
        </div>`).join("")}
    </div>`;
  }).join("");
  return `<div class="card__head"><h3>В работе</h3><span class="muted">${docs.length ? `${docs.length} открыт.` : ""}</span></div>
    ${groups || '<div class="empty">Открытых документов нет: всё принято, отгружено или отменено.</div>'}`;
}

function renderWarehouses() {
  const stat = (label, v, cls = "") => `<div><span>${label}</span><b class="num ${cls}">${v}</b></div>`;
  const EVENTS = { created: "создан", renamed: "переименован", deleted: "удалён", restored: "восстановлен" };
  const card = (w) => `
    <div class="card wh ${w.deleted ? "is-deleted" : ""}">
      <div class="card__head">
        ${state.editing === w.id
          ? `<form class="wh__name" data-rename="${w.id}"><input value="${esc(w.name)}" required maxlength="100"><button class="btn btn--sm">Сохранить</button><button type="button" class="btn btn--sm btn--ghost" data-action="cancel-rename">Отмена</button></form>`
          : `<h3 class="wh__name">${esc(w.name)} ${w.deleted ? '<span class="tag">удалён</span>' : ""}</h3>`}
      </div>
      <div class="card__body">
        <div class="wh__stats">
          ${stat("Физически", w.on_hand)}${stat("Доступно", w.available)}${stat("Резерв FBS", w.reserved, w.reserved ? "v-res" : "")}${stat("Едет на склад", w.inbound_transit, w.inbound_transit ? "v-transit" : "")}
        </div>
        <div class="chips">${w.skus.map((s) => `<span class="chip">${esc(s.sku)}: ${s.on_hand}${s.reserved ? ` (резерв ${s.reserved})` : ""}</span>`).join("") || '<span class="muted">Товара нет</span>'}</div>
        ${w.deleted && w.on_hand ? '<p class="hint" style="margin:0 0 12px">Склад удалён, но товар на нём числится. Его можно отгрузить или вывезти перемещением; принимать новый товар склад не будет.</p>' : ""}
        <div class="actions">
          ${w.deleted
            ? `<button class="btn btn--sm btn--ghost" data-action="restore" data-id="${w.id}">Восстановить</button>`
            : `<button class="btn btn--sm btn--ghost" data-action="rename" data-id="${w.id}">Переименовать</button>
               <button class="btn btn--sm btn--danger" data-action="delete" data-id="${w.id}">Удалить</button>`}
        </div>
        <ul class="events">${w.events.map((e) => `<li>${time(e.at)} — ${EVENTS[e.event] ?? e.event}${e.event === "renamed" ? `: «${esc(e.old_name)}» → «${esc(e.new_name)}»` : ""}</li>`).join("")}</ul>
      </div>
    </div>`;

  $('[data-view="warehouses"]').innerHTML = `
    <div class="view__head">
      <div><h2>Склады фулфилмента</h2><p class="hint">Удаление не стирает склад: остатки и история сохраняются, склад перестаёт принимать товар.</p></div>
      <form class="add-wh" data-add-wh><input placeholder="Название нового ФФ" required maxlength="100"><button class="btn">Добавить</button></form>
    </div>
    <div class="wh-grid">${state.warehouses.map(card).join("")}</div>`;
}

function renderHistory() {
  const f = state.filter;
  const kinds = [["", "Все операции"], ["receipt", "Приход"], ["transfer", "Перемещения"], ["fbo", "FBO"], ["fbs", "FBS"]];
  $('[data-view="history"]').innerHTML = `
    <div class="view__head"><div><h2>История операций</h2><p class="hint">Каждая строка — проводка: какой товар, сколько единиц, откуда и куда переместился.</p></div></div>
    <div class="filters">
      <select data-filter="sku"><option value="">Все артикулы</option>${skuOptions(f.sku)}</select>
      <select data-filter="warehouse_id"><option value="">Все ФФ</option>${whOptions(f.warehouse_id)}</select>
      <select data-filter="kind">${kinds.map(([v, l]) => `<option value="${v}" ${f.kind === v ? "selected" : ""}>${l}</option>`).join("")}</select>
    </div>
    <div class="card table-wrap">
      ${state.history.length ? `<table>
        <thead><tr><th>Время</th><th>Документ</th><th>Операция</th><th>Товар</th><th class="n">Кол-во</th><th>Откуда</th><th></th><th>Куда</th></tr></thead>
        <tbody>${state.history.map((h) => `<tr>
          <td class="muted num">${time(h.at)}</td><td>№${h.document_id}${h.reference ? ` <span class="muted">· ${esc(h.reference)}</span>` : ""}</td>
          <td>${esc(h.action_label)}</td><td><span class="sku">${esc(h.sku)}</span> ${esc(h.product_name)}</td>
          <td class="n"><b class="num">${h.qty}</b></td><td>${esc(h.from_label)}</td><td class="muted">→</td><td>${esc(h.to_label)}</td></tr>`).join("")}
        </tbody></table>` : '<div class="empty">Операций пока нет.</div>'}
    </div>`;
}

// ---------- «Где товар» ----------

async function openWhere(sku) {
  const [w, moves] = await Promise.all([api("GET", `/api/stock/${encodeURIComponent(sku)}`), api("GET", historyUrl({ sku }, 12))]);
  const drawer = $("[data-drawer]");
  drawer.innerHTML = `
    <div class="drawer__head">
      <div><h3>Где сейчас ${esc(w.sku)}</h3><span class="muted">${esc(w.name)} · на ФФ ${w.on_hand} шт., в пути ${w.in_transit} шт.</span></div>
      <button class="btn btn--ghost btn--sm" data-action="close-drawer">Закрыть</button>
    </div>
    <div class="drawer__body">
      ${w.places.map((p) => `
        <div class="place place--${p.kind}"><i></i>
          <div>${esc(p.label)}${p.document_id ? ` <span class="muted">· док. №${p.document_id}</span>` : ""}
            ${p.kind === "ff" ? `<small>доступно ${p.available}${p.reserved ? `, в резерве FBS ${p.reserved}` : ""}</small>` : ""}</div>
          <b class="num">${p.qty}</b>
        </div>`).join("") || '<div class="empty">Товар нигде не числится.</div>'}
      <p class="hint">Выбыло из учёта ФФ: принято складами МП ${w.accepted_by_marketplace} шт., доставлено клиентам ${w.delivered_to_clients} шт.</p>
      <h4 class="sub">Последние движения</h4>
      <table class="mini"><tbody>${moves.map((h) => `<tr><td class="muted">${time(h.at)}</td><td>${esc(h.action_label)}</td><td class="n"><b>${h.qty}</b></td><td>${esc(h.from_label)} → ${esc(h.to_label)}</td></tr>`).join("") || '<tr><td class="muted">Движений нет</td></tr>'}</tbody></table>
    </div>`;
  drawer.hidden = false;
}

// ---------- действия ----------

function setField(form, path, value) {
  const draft = state.draft[form];
  const parts = path.split(".");
  let target = draft;
  while (parts.length > 1) target = target[parts.shift()];
  target[parts[0]] = value;
  state.keys[form] = uuid(); // данные изменились — это уже другой запрос
}

function payload(form, allocations = null) {
  const d = state.draft[form];
  if (form === "receipt") {
    return {
      lines: d.lines.map((l) => ({ sku: l.sku, warehouse_id: +l.wh, qty: +l.qty })),
      reference: d.reference.trim() || null,
    };
  }
  const body = { sku: d.sku, qty: +d.qty, ...(allocations ? { allocations } : { warehouse_id: +d.src }) };
  if (form === "transfer") body.dest_warehouse_id = +d.dst;
  if (form === "fbo") body.destination = d.destination;
  if (form === "fbs") body.order_no = d.order_no;
  return body;
}

async function submit(form, allocations = null) {
  const button = $(`form[data-form="${form}"] button:not([type])`);
  if (button) button.disabled = true;
  try {
    const res = await api("POST", URLS[form], payload(form, allocations), state.keys[form]);
    closeModal();
    toast(res.message, "ok");
    state.keys[form] = uuid();
    const keep = state.draft[form];
    state.draft[form] = newDraft()[form];
    if (form !== "receipt") Object.assign(state.draft[form], { sku: keep.sku, src: keep.src, dst: keep.dst ?? "" });
    await refresh();
  } catch (e) {
    if (e.data?.error?.startsWith("shortage")) showShortage(form, e.data);
    else toast(e.message, "error");
  } finally {
    if (button) button.disabled = false;
  }
}

function showShortage(form, err) {
  state.pending = { form, options: err.options };
  const rows = err.warehouses
    .map((w) => `<tr><td>${esc(w.name)}${w.deleted ? ' <span class="tag">удалён</span>' : ""}</td><td class="n">${w.available}</td><td class="n">${w.reserved || "—"}</td></tr>`)
    .join("");
  openModal(`
    <header><h3>${err.error === "shortage" ? "На выбранном ФФ не хватает" : "Товара не хватает"}</h3></header>
    <div class="body">
      <p>${esc(err.message)}</p>
      <table class="mini"><thead><tr><th>ФФ</th><th class="n">Доступно</th><th class="n">Резерв FBS</th></tr></thead><tbody>${rows}</tbody></table>
      ${err.options.length ? `<div class="options">${err.options.map((o, i) => `
        <button class="option" data-action="apply-option" data-i="${i}">${esc(o.label)}
          <small>${o.allocations.length > 1 ? `Будет ${o.allocations.length} документа — по одному на каждый ФФ, номер заказа общий` : "Один документ с другого ФФ"}</small></button>`).join("")}</div>` : ""}
    </div>
    <footer><button class="btn btn--ghost" data-action="close-modal">${err.options.length ? "Отмена" : "Изменить количество"}</button></footer>`);
}

async function step(url, button) {
  button.disabled = true;
  try {
    const res = await api("POST", url);
    toast(res.message, res.changed ? "ok" : "info");
  } catch (e) {
    toast(e.message, "error");
  }
  await refresh();
}

function confirmDelete(id) {
  const w = state.warehouses.find((x) => x.id === +id);
  const stock = w.skus.map((s) => `${esc(s.sku)}: ${s.on_hand} шт.`).join(", ");
  openModal(`
    <header><h3>Удалить ФФ «${esc(w.name)}»?</h3></header>
    <div class="body">
      ${w.on_hand
        ? `<p>На складе числится <b>${w.on_hand} шт.</b> (${stock}). Остатки и история сохранятся и останутся видны с пометкой «удалён».</p>
           <p>Склад перестанет принимать товар — приход и перемещения на него будут закрыты. Отгрузить или вывезти товар с него можно.</p>`
        : `<p>Остатков на складе нет. История операций сохранится.</p>`}
      ${w.inbound_transit ? `<p class="tag">На склад едут перемещения (${w.inbound_transit} шт.) — сначала примите их.</p>` : ""}
    </div>
    <footer><button class="btn btn--ghost" data-action="close-modal">Отмена</button><button class="btn btn--danger" data-action="confirm-delete" data-id="${w.id}">Удалить</button></footer>`);
}

async function warehouseAction(method, url, body) {
  try {
    const res = await api(method, url, body);
    toast(res.message, res.changed === false ? "info" : "ok");
    closeModal();
    state.editing = null;
    await refresh();
  } catch (e) {
    toast(e.message, "error");
  }
}

function showReconcile() {
  const r = state.reconcile;
  openModal(`
    <header><h3>${r.ok ? "Учёт сходится" : "Найдены расхождения"}</h3></header>
    <div class="body">
      <p>Сверка пересчитывает учёт по журналу проводок независимо от таблицы остатков и проверяет четыре правила:</p>
      <ol class="hint">
        <li>остатки каждого ФФ (свободно и резерв) равны сумме проводок;</li>
        <li>в пути ровно столько, сколько по открытым документам «в пути»;</li>
        <li>резерв на ФФ равен сумме открытых заказов FBS;</li>
        <li>принято ${r.received} = на ФФ ${r.on_ff} + в пути ${r.in_transit} + выбыло ${r.left}.</li>
      </ol>
      ${r.mismatches.map((m) => `<p class="tag">${esc(m)}</p>`).join("")}
    </div>
    <footer><button class="btn" data-action="close-modal">Понятно</button></footer>`);
}

// ---------- события ----------

document.addEventListener("click", async (e) => {
  const tab = e.target.closest("[data-tab]");
  if (tab) {
    state.tab = tab.dataset.tab;
    render();
    return;
  }
  if (e.target.closest("[data-reconcile]")) return showReconcile();
  if (e.target === $("[data-modal]")) return closeModal();

  const el = e.target.closest("[data-action]");
  if (!el) {
    if (!e.target.closest(".menu")) $(".menu__list").hidden = true;
    return;
  }
  const { action } = el.dataset;
  switch (action) {
    case "toggle-menu":
      $(".menu__list").hidden = !$(".menu__list").hidden;
      break;
    case "reset": {
      $(".menu__list").hidden = true;
      const withReceipt = el.dataset.withReceipt === "true";
      if (!confirm(`Сбросить все данные к тестовым${withReceipt ? " и провести приход из задания" : ""}?`)) return;
      const res = await api("POST", "/api/demo/reset", { with_receipt: withReceipt });
      state.draft = newDraft();
      toast(res.message, "ok");
      await refresh();
      break;
    }
    case "where":
      openWhere(el.dataset.sku);
      break;
    case "close-drawer":
      $("[data-drawer]").hidden = true;
      break;
    case "form":
      state.form = el.dataset.form;
      renderOps();
      break;
    case "add-line": {
      const last = state.draft.receipt.lines.at(-1);
      state.draft.receipt.lines.push({ sku: last.sku, wh: last.wh, qty: "" });
      state.keys.receipt = uuid();
      renderOps();
      break;
    }
    case "remove-line":
      state.draft.receipt.lines.splice(+el.dataset.i, 1);
      state.keys.receipt = uuid();
      renderOps();
      break;
    case "fill-test-receipt": {
      const id = (name) => active().find((w) => w.name === name)?.id ?? active()[0]?.id;
      state.draft.receipt.lines = [
        ["SKU-101", "Север", 10], ["SKU-101", "Юг", 6], ["SKU-102", "Север", 5], ["SKU-103", "Юг", 4],
      ].map(([sku, wh, qty]) => ({ sku, wh: id(wh), qty }));
      state.draft.receipt.reference = "Тестовый приход из задания";
      state.keys.receipt = uuid();
      renderOps();
      break;
    }
    case "apply-option": {
      const { form, options } = state.pending;
      await submit(form, options[+el.dataset.i].allocations);
      break;
    }
    case "close-modal":
      closeModal();
      break;
    case "step":
      await step(el.dataset.url, el);
      break;
    case "rename":
      state.editing = +el.dataset.id;
      renderWarehouses();
      $("[data-rename] input")?.select();
      break;
    case "cancel-rename":
      state.editing = null;
      renderWarehouses();
      break;
    case "delete":
      confirmDelete(el.dataset.id);
      break;
    case "confirm-delete":
      await warehouseAction("DELETE", `/api/warehouses/${el.dataset.id}`);
      break;
    case "restore":
      await warehouseAction("POST", `/api/warehouses/${el.dataset.id}/restore`);
      break;
  }
});

document.addEventListener("input", (e) => {
  const form = e.target.closest("form[data-form]");
  if (form && e.target.dataset.field) {
    setField(form.dataset.form, e.target.dataset.field, e.target.value);
    if (e.target.dataset.field === "src" && form.dataset.form === "transfer") renderOps();
    else updateAvailability();
  }
});

document.addEventListener("change", async (e) => {
  if (e.target.dataset.filter) {
    state.filter[e.target.dataset.filter] = e.target.value;
    state.history = await api("GET", historyUrl());
    renderHistory();
  }
});

document.addEventListener("submit", async (e) => {
  e.preventDefault();
  const form = e.target;
  if (form.dataset.form) return submit(form.dataset.form);
  if (form.dataset.addWh !== undefined) {
    const name = $("input", form).value;
    return warehouseAction("POST", "/api/warehouses", { name });
  }
  if (form.dataset.rename) return warehouseAction("PATCH", `/api/warehouses/${form.dataset.rename}`, { name: $("input", form).value });
});

document.addEventListener("keydown", (e) => {
  if (e.key !== "Escape") return;
  closeModal();
  $("[data-drawer]").hidden = true;
});

// ---------- старт ----------

(async function start() {
  const [meta, products] = await Promise.all([api("GET", "/api/meta"), api("GET", "/api/products")]);
  Object.assign(state, { meta, products });
  $("[data-store]").textContent = meta.store;
  $("[data-demo]").hidden = !meta.demo_mode;
  await refresh();
  state.draft = newDraft();
  render();
})();
