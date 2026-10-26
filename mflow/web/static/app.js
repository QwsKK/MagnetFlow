/* MagnetFlow 控制台 · 单文件前端（无外部依赖）
   与 index.html / style.css 严格配套：所有 DOM 只复用 style.css 已有类名。
   后端字段以 mflow/web/server.py 实测为准（2026-10 复核）。 */
"use strict";

/* ============================================================ 0. 常量 / 图标 */

const $ = (sel, root) => (root || document).querySelector(sel);
const $$ = (sel, root) => Array.from((root || document).querySelectorAll(sel));

const KIND_CN = {
  harvest: "采集", pick_max: "选最大", push: "推送", subtitle: "字幕",
  doctor: "体检", chain: "全流程",
};

/* 状态 → 文案 / 配色类（style.css 里的 .pill-* ） */
const STATUS_CN = { running: "运行中", finished: "已完成", failed: "失败", killed: "已终止" };
const PILL_CLS = {
  running: "pill-running", finished: "pill-finished",
  failed: "pill-failed", killed: "pill-killed",
};
const TERMINAL_STATUS = { finished: 1, failed: 1, killed: 1 };

/* 图标：1.7 stroke 的线性 SVG，currentColor 取色，直接 innerHTML 注入（内容为常量） */
function _p(d, sw) {
  return '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="' +
    (sw || 1.7) + '" stroke-linecap="round" stroke-linejoin="round" width="16" height="16">' + d + "</svg>";
}
const ICONS = {
  grid: _p('<rect x="3" y="3" width="7" height="7" rx="1.5"/><rect x="14" y="3" width="7" height="7" rx="1.5"/><rect x="3" y="14" width="7" height="7" rx="1.5"/><rect x="14" y="14" width="7" height="7" rx="1.5"/>'),
  download: _p('<path d="M12 3v12"/><path d="M7 11l5 5 5-5"/><path d="M4 21h16"/>'),
  magnet: _p('<path d="M6 3v8a6 6 0 0 0 12 0V3"/><path d="M6 8h3"/><path d="M15 8h3"/><path d="M4 21h16"/>'),
  send: _p('<path d="M21 3L10 14"/><path d="M21 3l-7 18-4-7-7-4z"/>'),
  captions: _p('<rect x="3" y="5" width="18" height="14" rx="2"/><path d="M7 11h4"/><path d="M13 11h4"/><path d="M7 15h3"/><path d="M12 15h5"/>'),
  sliders: _p('<path d="M4 7h10"/><path d="M18 7h2"/><path d="M4 17h2"/><path d="M10 17h10"/><circle cx="16" cy="7" r="2"/><circle cx="8" cy="17" r="2"/>'),
  gear: _p('<circle cx="12" cy="12" r="3"/><path d="M19 12a7 7 0 0 0-.1-1.2l2-1.5-2-3.4-2.3 1a7 7 0 0 0-2-1.2L14.2 3h-4.4l-.4 2.7a7 7 0 0 0-2 1.2l-2.3-1-2 3.4 2 1.5A7 7 0 0 0 5 12a7 7 0 0 0 .1 1.2l-2 1.5 2 3.4 2.3-1a7 7 0 0 0 2 1.2l.4 2.7h4.4l.4-2.7a7 7 0 0 0 2-1.2l2.3 1 2-3.4-2-1.5A7 7 0 0 0 19 12z"/>'),
  play: _p('<path d="M7 4l13 8-13 8z"/>'),
  refresh: _p('<path d="M20 11a8 8 0 1 0-2.6 6.3"/><path d="M20 4v7h-7"/>'),
  eye: _p('<path d="M2 12s3.6-7 10-7 10 7 10 7-3.6 7-10 7-10-7-10-7z"/><circle cx="12" cy="12" r="3"/>'),
  eyeoff: _p('<path d="M3 3l18 18"/><path d="M10.6 5.2A10.9 10.9 0 0 1 12 5c6.4 0 10 7 10 7a17 17 0 0 1-3.1 4"/><path d="M6.6 6.6A16.7 16.7 0 0 0 2 12s3.6 7 10 7a10.7 10.7 0 0 0 4.3-.9"/>'),
  copy: _p('<rect x="9" y="9" width="12" height="12" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/>'),
  folder: _p('<path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/>'),
  chevR: _p('<path d="M9 5l7 7-7 7"/>'),
  chevL: _p('<path d="M15 5l-7 7 7 7"/>'),
  arrowD: _p('<path d="M12 5v14"/><path d="M6 13l6 6 6-6"/>'),
  stetho: _p('<path d="M5 3v6a4 4 0 0 0 8 0V3"/><path d="M9 13v3a5 5 0 0 0 10 0v-1"/><circle cx="19" cy="12" r="2"/>'),
  flask: _p('<path d="M9 3h6"/><path d="M10 3v6L4 19a2 2 0 0 0 1.8 3h12.4A2 2 0 0 0 20 19L14 9V3"/><path d="M7 15h10"/>'),
  list: _p('<path d="M8 6h13"/><path d="M8 12h13"/><path d="M8 18h13"/><path d="M3.5 6h.01"/><path d="M3.5 12h.01"/><path d="M3.5 18h.01"/>'),
  search: _p('<circle cx="11" cy="11" r="7"/><path d="M16.5 16.5L21 21"/>'),
  trash: _p('<path d="M4 7h16"/><path d="M9 7V4h6v3"/><path d="M6 7l1 14h10l1-14"/>'),
  check: _p('<path d="M4 12l5 5L20 6"/>'),
  cross: _p('<path d="M6 6l12 12"/><path d="M18 6L6 18"/>'),
  warn: _p('<path d="M12 3l9.5 17H2.5z"/><path d="M12 9v5"/><path d="M12 17.5h.01"/>'),
  info: _p('<circle cx="12" cy="12" r="9"/><path d="M12 11v6"/><path d="M12 7.5h.01"/>'),
  chip: _p('<rect x="6" y="6" width="12" height="12" rx="2"/><path d="M9 2v3"/><path d="M15 2v3"/><path d="M9 19v3"/><path d="M15 19v3"/><path d="M2 9h3"/><path d="M2 15h3"/><path d="M19 9h3"/><path d="M19 15h3"/>'),
  clock: _p('<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3.5 2"/>'),
  db: _p('<ellipse cx="12" cy="6" rx="8" ry="3"/><path d="M4 6v12c0 1.7 3.6 3 8 3s8-1.3 8-3V6"/><path d="M4 12c0 1.7 3.6 3 8 3s8-1.3 8-3"/>'),
  code: _p('<path d="M8 8l-4 4 4 4"/><path d="M16 8l4 4-4 4"/><path d="M13 5l-2 14"/>'),
  codefile: _p('<path d="M14 3v5h5"/><path d="M19 8v13H5V3h9z"/><path d="M9 13l-2 2 2 2"/><path d="M15 13l2 2-2 2"/>'),
  empty: _p('<path d="M3 8l9-5 9 5v8l-9 5-9-5z"/><path d="M3 8l9 5 9-5"/><path d="M12 13v8"/>', 1.3),
};

/* 路由表：导航文字 / 标题 / 副标题集中在这里 */
const ROUTES = [
  { hash: "/overview", cn: "总览", icon: "grid", title: "总览", sub: "采集 · 推送 · 字幕三条流水线的当前状态与一键入口" },
  { hash: "/harvest", cn: "采集", icon: "download", title: "磁力采集", sub: "按站点规则抓取磁力列表，结果写入磁力库" },
  { hash: "/library", cn: "磁力库", icon: "magnet", title: "磁力库", sub: "检索已入库磁力，批量标记推送状态" },
  { hash: "/push", cn: "推送 115", icon: "send", title: "推送 115", sub: "把待推送磁力离线到 115 网盘，可先试运行" },
  { hash: "/subtitle", cn: "字幕流水线", icon: "captions", title: "字幕流水线", sub: "扫描网盘视频 → 转写翻译 → 回写字幕" },
  { hash: "/sites", cn: "站点规则", icon: "sliders", title: "站点规则", sub: "编辑每个站点的列表页 / 详情页抓取规则" },
  { hash: "/settings", cn: "设置", icon: "gear", title: "设置", sub: "推送 115 / 采集器 / 字幕的配置文件" },
];
const ROUTE_MAP = {};
ROUTES.forEach((r) => { ROUTE_MAP[r.hash] = r; });

/* ============================================================ 1. 通用工具 */

function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => (
    { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

/* DOM 工厂：h("div.card", {onClick:fn, href:""}, "文本", 节点, ...)
   class 走选择器简写；dataset.* / on* / 其余 setAttribute；kids 里 null/undefined/false 会被忽略 */
function h(sel, attrs) {
  let tag = "div", id = "", cls = [];
  String(sel || "div").split(/(?=[.#])/).forEach((part) => {
    if (part[0] === ".") cls.push(part.slice(1));
    else if (part[0] === "#") id = part.slice(1);
    else tag = part;
  });
  const node = document.createElement(tag);
  if (id) node.id = id;
  if (cls.length) node.className = cls.join(" ");
  const a = attrs && typeof attrs === "object" && !(attrs instanceof Node) && !Array.isArray(attrs) ? attrs : null;
  let from = 1;
  if (a) {
    Object.keys(a).forEach((k) => {
      const v = a[k];
      if (v === null || v === undefined || v === false) return;
      if (k === "class" || k === "className") node.className = (node.className ? node.className + " " : "") + v;
      else if (k === "text") node.textContent = String(v);
      else if (k === "html") node.innerHTML = String(v);       /* 只用于 ICONS 常量 */
      else if (k === "style") node.style.cssText = String(v);
      else if (k === "dataset") Object.keys(v).forEach((d) => { node.dataset[d] = v[d]; });
      else if (k.slice(0, 2) === "on" && typeof v === "function") node.addEventListener(k.slice(2), v);
      else if (k === "value") node.value = v;
      else node.setAttribute(k, v === true ? "" : String(v));
    });
    from = 2;
  }
  const push = (k) => {
    if (k === null || k === undefined || k === false || k === true) return;
    if (Array.isArray(k)) return k.forEach(push);
    node.appendChild(k instanceof Node ? k : document.createTextNode(String(k)));
  };
  for (let i = from; i < arguments.length; i++) push(arguments[i]);
  return node;
}

function svgIcon(name, cls) {
  const s = h("span", { class: cls || "ic", html: ICONS[name] || ICONS.info });
  return s.firstChild;                       /* 直接返回 <svg> 节点 */
}

/* 统一请求：非 2xx 时把 FastAPI 的 detail（字符串 或 pydantic 数组）抛成 Error */
async function api(path, opts) {
  const o = opts || {};
  const init = { method: o.method || "GET", headers: {}, cache: "no-store" };
  if (o.body !== undefined) {
    init.headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(o.body);
  }
  let res;
  try {
    res = await fetch(path, init);
  } catch (err) {
    throw new Error("连不上后端（服务可能已退出）：" + (err && err.message ? err.message : err));
  }
  let data = null;
  const text = await res.text();
  if (text) {
    try { data = JSON.parse(text); } catch (e) { data = text; }
  }
  if (!res.ok) {
    let detail = data && data.detail;
    if (Array.isArray(detail)) {                     /* pydantic 校验错误数组 */
      detail = detail.map((d) => {
        const loc = Array.isArray(d.loc) ? d.loc.filter((x) => x !== "body").join(".") : "";
        return (loc ? loc + ": " : "") + (d.msg || "");
      }).join("；");
    } else if (detail === undefined || detail === null) {
      detail = typeof data === "string" && data ? data : ("HTTP " + res.status);
    }
    const e = new Error(String(detail));
    e.status = res.status;
    e.data = data;
    throw e;
  }
  return data;
}

const TOAST_ICON = { success: "check", warn: "warn", danger: "cross", info: "info" };
const TOAST_CLS = { success: "toast-success", warn: "toast-warn", danger: "toast-danger", info: "toast-info" };
const TOAST_TITLE = { success: "成功", warn: "注意", danger: "失败", info: "提示" };

function toast(msg, type) {
  const t = type || "info";
  const root = $("#toast-root");
  if (!root) return;
  const box = h("div.toast." + (TOAST_CLS[t] || TOAST_CLS.info), {},
    h("span.t-ic", { html: ICONS[TOAST_ICON[t] || ICONS.info] }),
    h("div.t-body", {},
      h("div.t-title", { text: TOAST_TITLE[t] || TOAST_TITLE.info }),
      h("div.t-msg", { text: String(msg) })));
  root.appendChild(box);
  requestAnimationFrame(() => box.classList.add("in"));
  const kill = () => {
    box.classList.remove("in");
    setTimeout(() => { if (box.parentNode) box.parentNode.removeChild(box); }, 240);
  };
  const timer = setTimeout(kill, 4000);
  box.addEventListener("click", () => { clearTimeout(timer); kill(); });
}

function confirmModal(title, msg, opts) {
  const o = opts || {};
  return new Promise((resolve) => {
    const root = $("#modal-root"), mask = $("#modal-mask"), box = $("#modal-box");
    if (!root || !box) return resolve(false);
    const ok = h("button.btn." + (o.danger ? "btn-danger" : "btn-primary"), { text: o.okText || "确定" });
    const no = h("button.btn.btn-ghost", { text: o.noText || "取消" });
    box.replaceChildren(
      h("h3.modal-title", { text: title || "确认" }),
      h("p.modal-text", { text: msg || "" }),
      o.detail ? h("div.modal-list", { text: String(o.detail) }) : null,
      h("div.modal-actions", {}, no, ok));
    const done = (val) => {
      root.classList.add("hidden");
      mask.removeEventListener("click", cancel);
      document.removeEventListener("keydown", onKey);
      resolve(val);
    };
    const cancel = () => done(false);
    const onKey = (ev) => {
      if (ev.key === "Escape") done(false);
      else if (ev.key === "Enter") { ev.preventDefault(); done(true); }
    };
    ok.addEventListener("click", () => done(true));
    no.addEventListener("click", cancel);
    mask.addEventListener("click", cancel);
    document.addEventListener("keydown", onKey);
    root.classList.remove("hidden");
    setTimeout(() => ok.focus(), 30);
  });
}

function fmtSize(bytes) {
  const n = Number(bytes);
  if (!isFinite(n) || bytes === null || bytes === undefined || bytes === "") return String(bytes === null ? "—" : bytes);
  const u = ["B", "KB", "MB", "GB", "TB"];
  let i = 0, v = n;
  while (v >= 1024 && i < u.length - 1) { v /= 1024; i++; }
  return (i === 0 ? v.toFixed(0) : v.toFixed(v >= 100 ? 0 : v >= 10 ? 1 : 2)) + " " + u[i];
}
/* 后端给的是 KB / GB，单独处理，避免误乘 1024 */
function fmtKb(kb) {
  const n = Number(kb);
  if (!isFinite(n)) return "—";
  return n < 1024 ? n.toFixed(1) + " KB" : n < 1048576 ? (n / 1024).toFixed(2) + " MB" : (n / 1048576).toFixed(2) + " GB";
}
function fmtGb(gb) {
  const n = Number(gb);
  if (!isFinite(n) || n <= 0) return "—";
  return n.toFixed(2) + " GB";
}
function fmtTime(v) {
  if (v === null || v === undefined || v === "") return "—";
  let d;
  if (typeof v === "number") d = new Date(v * 1000);
  else if (/^\d{10}$/.test(String(v))) d = new Date(Number(v) * 1000);
  else d = new Date(v);
  if (!d || isNaN(d.getTime())) return String(v);
  const p = (x) => String(x).padStart(2, "0");
  return d.getFullYear() + "-" + p(d.getMonth() + 1) + "-" + p(d.getDate()) +
    " " + p(d.getHours()) + ":" + p(d.getMinutes()) + ":" + p(d.getSeconds());
}
function fmtAgo(v) {
  const t = typeof v === "number" ? v : Date.parse(v) / 1000;
  if (!isFinite(t) || !t) return "—";
  const s = Math.max(0, Math.floor(Date.now() / 1000 - t));
  if (s < 60) return s + " 秒前";
  if (s < 3600) return Math.floor(s / 60) + " 分钟前";
  if (s < 86400) return Math.floor(s / 3600) + " 小时前";
  if (s < 86400 * 30) return Math.floor(s / 86400) + " 天前";
  return fmtTime(v);
}
function fmtDur(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  if (s < 60) return s + "s";
  if (s < 3600) return Math.floor(s / 60) + "m" + String(s % 60).padStart(2, "0") + "s";
  return Math.floor(s / 3600) + "h" + String(Math.floor((s % 3600) / 60)).padStart(2, "0") + "m";
}
function debounce(fn, ms) {
  let t = 0;
  return function () {
    const args = arguments, self = this;
    clearTimeout(t);
    t = setTimeout(() => fn.apply(self, args), ms || 350);
  };
}
function btn(label, opts, handler) {
  const o = opts || {};
  const b = h("button.btn." + (o.cls || ""), { disabled: !!o.disabled });
  if (o.icon) b.appendChild(svgIcon(o.icon));
  b.appendChild(document.createTextNode(label));
  b.appendChild(h("span.spinner"));
  if (handler) b.addEventListener("click", handler);
  b._label = label;
  return b;
}
function _textNodeOf(b) {
  const kids = Array.from(b.childNodes);
  return kids.find((n) => n.nodeType === 3 && n.nodeValue.trim()) || null;
}
function busy(b, on, txt) {
  if (!b) return;
  b.classList.toggle("loading", !!on);
  const t = _textNodeOf(b);
  if (t) t.nodeValue = on ? (txt || "处理中…") : (b._label || "");
}
function setDisabled(b, off) { if (b) b.disabled = !!off; }
function skeletonGrid(n) {
  const g = h("div.grid" + (n > 3 ? ".g-3" : ".g-2"));
  for (let i = 0; i < n; i++) g.appendChild(h("div.skel.skel-card"));
  return g;
}
function skeletonRows(n) {
  const w = h("div.skel-rows");
  for (let i = 0; i < n; i++) w.appendChild(h("div.skel"));
  return w;
}
function emptyBlock(icon, title, sub) {
  return h("div.empty", {}, svgIcon(icon, "e-ic"),
    h("div.e-title", { text: title }),
    sub ? h("div.e-sub", { text: sub }) : null);
}
function cardBox(title, icon, extra) {
  const card = h("section.card", {});
  const head = h("div.card-head", {}, svgIcon(icon), h("div.card-title", {}, title));
  if (extra && extra.length) head.appendChild(h("div.spacer"), ...extra);
  card.appendChild(head);
  card.head = head;
  return card;
}
function pill(status, extraCls) {
  const cls = PILL_CLS[status] || "pill-muted";
  const p = h("span.pill." + cls + (extraCls ? "." + extraCls : ""), {},
    status === "running" ? h("i.dot") : null,
    STATUS_CN[status] || status || "未知");
  return p;
}
function tickBox(kind) {                      /* kind: ok|bad|warn|na */
  const ic = { ok: "check", bad: "cross", warn: "warn", na: "info" }[kind] || "info";
  return h("span.tick." + kind, { html: ICONS[ic] });
}
function rowOf(kind, name, sub, right) {
  return h("div.row", {},
    tickBox(kind),
    h("div.grow", {}, h("div.name", { text: name }), sub ? h("div.sub", { text: sub }) : null),
    right ? (right instanceof Node ? right : h("span.size", { text: String(right) })) : null);
}

/* ============================================================ 2. 轮询登记 + 路由 */

const pollTimers = new Set();                 /* 当前视图的轮询 id，切路由时清空 */
function addPoll(fn, ms) {
  const id = setInterval(() => {
    if (document.hidden) return;              /* 页面隐藏时挂起，可见后自动恢复 */
    Promise.resolve(fn()).catch(() => {});
  }, ms);
  pollTimers.add(id);
  return id;
}
function stopAll() {
  pollTimers.forEach((id) => clearInterval(id));
  pollTimers.clear();
  if (LOG_HANDLES.length) LOG_HANDLES.splice(0).forEach((h) => h.destroy());
}
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) {
    refreshRunBadge().catch(() => {});
    if (typeof RERENDER_ON_SHOW === "function") RERENDER_ON_SHOW();
  }
});
let RERENDER_ON_SHOW = null;

const VIEWS = {};
let currentHash = "";

function renderNav() {
  const nav = $("#nav");
  if (!nav) return;
  nav.replaceChildren(...ROUTES.map((r) => h("a.nav-item", { href: "#" + r.hash },
    svgIcon(r.icon), h("span.nav-text", { text: r.cn }))));
}
function markNav(hash) {
  $$("#nav .nav-item").forEach((a) => {
    a.classList.toggle("active", a.getAttribute("href") === "#" + hash);
  });
}

async function route() {
  const hash = (location.hash || "#/overview").replace(/^#/, "") || "/overview";
  const path = ROUTE_MAP[hash] ? hash : "/overview";
  const meta = ROUTE_MAP[path];
  currentHash = path;
  if (!ROUTE_MAP[hash] && location.hash !== "#" + path) { location.hash = "#" + path; return; }
  stopAll();
  RERENDER_ON_SHOW = null;
  markNav(path);
  const vt = $("#view-title"), vs = $("#view-sub"), view = $("#view");
  if (vt) vt.textContent = meta.title;
  if (vs) vs.textContent = meta.sub;
  if (view) {
    view.dataset.route = path;
    view.replaceChildren(h("div.grid.g-3", {},
      h("div.skel.skel-card"), h("div.skel.skel-card"), h("div.skel.skel-card")),
      h("div.skel.skel-block"));
  }
  try {
    await VIEWS[path](view, meta);
  } catch (err) {
    if (view && currentHash === path) {
      view.replaceChildren(h("section.card", {},
        h("div.card-head", {}, svgIcon("warn"), h("div.card-title", { text: "加载失败" })),
        h("div.note.danger", { text: (err && err.message) || String(err) }),
        h("div.form-actions", {}, btn("重试", { cls: "btn-primary", icon: "refresh" }, () => route()))));
    }
    toast("加载失败：" + ((err && err.message) || err), "danger");
  }
}

/* 顶栏「运行中 N」：全局 5s 轻量轮询，不随视图切换清理 */
let badgeBusyCache = "";
async function refreshRunBadge() {
  const badge = $("#run-badge"), txt = $("#run-badge-text");
  if (!badge) return;
  let data = null;
  try { data = await api("/api/tasks"); } catch (e) {
    if (badgeBusyCache !== "down") {
      badgeBusyCache = "down";
      badge.classList.remove("busy");
      badge.classList.add("hidden");
      if (txt) txt.textContent = "服务未响应";
    }
    return;
  }
  const list = (data && (data.tasks || data.items)) || [];
  const run = list.filter((t) => t.status === "running").length;
  const key = String(run);
  if (key !== badgeBusyCache) {
    badgeBusyCache = key;
    badge.classList.toggle("hidden", run === 0);
    badge.classList.toggle("busy", run > 0);
    if (txt) txt.textContent = run > 0 ? "运行中 " + run : "";
  }
}

/* 概览里的快捷任务：跳转后由目标视图接上日志终端 */
let PENDING_TASK = null;
function takePending() {
  const t = PENDING_TASK;
  PENDING_TASK = null;
  return t;
}
async function startTask(url, body, opts) {
  const o = opts || {};
  let res;
  try {
    res = await api(url, { method: "POST", body: body === undefined ? {} : body });
  } catch (err) {
    toast((err && err.message) || String(err), err && err.status === 409 ? "warn" : "danger");
    return null;
  }
  const task = (res && res.task) || null;
  if (o.silent !== true) toast(o.okMsg || "任务已启动" + (task ? "：" + (task.label || task.id) : ""), "success");
  if (task && o.goto) {
    if (location.hash !== "#" + o.goto) {
      PENDING_TASK = task.id;
      location.hash = "#" + o.goto;
    } else {
      PENDING_TASK = task.id;
      route();
    }
  }
  return task;
}

/* 站点列表缓存（采集 / 推送 / 站点视图共用） */
let SITES_CACHE = { at: 0, sites: [] };
async function loadSites(force) {
  if (!force && SITES_CACHE.sites.length && Date.now() - SITES_CACHE.at < 30000) return SITES_CACHE.sites;
  const d = await api("/api/sites");
  SITES_CACHE = { at: Date.now(), sites: (d && d.sites) || [] };
  return SITES_CACHE.sites;
}
function siteSelect(sites, allLabel, withDisabledMark) {
  const sel = h("select", {});
  if (allLabel !== null) sel.appendChild(h("option", { value: "", text: allLabel || "全部站点" }));
  sites.forEach((s) => {
    let t = s.name;
    if (withDisabledMark) t += s.enabled ? "（启用）" : "（停用）";
    sel.appendChild(h("option", { value: s.name, text: t }));
  });
  return sel;
}

/* ============================================================ 3. 视图：总览 */

VIEWS["/overview"] = async function (view) {
  view.replaceChildren(skeletonGrid(4), h("div.skel.skel-block"));
  const [ov, st] = await Promise.all([
    api("/api/overview").catch((e) => { throw e; }),
    api("/api/system/status").catch(() => null),
  ]);
  const hv = (ov && ov.harvester) || {}, pu = (ov && ov.push) || {}, su = (ov && ov.subtitle) || {};
  const last = (ov && ov.last_tasks) || {};

  const stat = (label, icon, value, sub, tone, unit) => {
    const has = value !== null && value !== undefined && value !== "";
    const val = (has && unit)
      ? h("div.stat-value", { html: esc(value) + "<small>" + esc(unit) + "</small>" })
      : h("div.stat-value", { text: has ? String(value) : "—" });
    return h("div.stat-card" + (tone ? "." + tone : ""), {},
      h("div.stat-label", {}, svgIcon(icon), label), val,
      sub ? h("div.stat-sub", { text: sub }) : null);
  };

  const cards = h("div.grid.g-3", {},
    stat("磁力总数", "magnet", hv.magnet_total,
      "启用站点 " + (hv.sites_enabled !== undefined ? hv.sites_enabled : "—") + " / " + ((hv.sites || []).length)),
    stat("待推送", "send", hv.magnet_pending !== undefined ? hv.magnet_pending : pu.pending,
      "推送开关 " + (pu.enabled ? "开" : "关"), (hv.magnet_pending ? "tone-warn" : "tone-ok")),
    stat("今日新增", "clock", hv.magnet_today, hv.last_export
      ? "最新导出 " + hv.last_export.site + " · " + fmtAgo(hv.last_export.mtime) : "暂无导出文件"),
    stat("字幕完成", "captions", su.done, "片库共 " + (su.video_total !== undefined ? su.video_total : "—") + " 部", "tone-pri"),
    stat("字幕失败", "warn", su.failed, su.pending !== undefined ? "待处理 " + su.pending : "", (su.failed ? "tone-bad" : "")),
    stat("磁盘剩余", "db", st && st.disk_free_gb != null ? st.disk_free_gb : null,
      st && st.root ? st.root : "", "tone-ok", "GB"));

  const tiles = h("div.grid.g-3", {},
    tileBtn("发起环境体检", "跑一遍 Cookie / WebDAV / 引擎自检", "stetho",
      () => startTask("/api/tasks/doctor", {}, { goto: "/subtitle", okMsg: "环境体检已启动" })),
    tileBtn("采集试运行", "第一个启用站点的第 1 页，dry_run 不入库", "download",
      async () => {
        const sites = await loadSites().catch(() => []);
        const site = ((sites.find((s) => s.enabled) || sites[0]) || {}).name;
        if (!site) { toast("还没有可用站点：先去「站点规则」配置并启用一个", "warn"); return; }
        startTask("/api/tasks/harvest", { site, pages: [1, 1], limit: 20, dry_run: true },
          { goto: "/harvest", okMsg: "试运行采集已启动（" + site + "）" });
      }),
    tileBtn("查看字幕清单", "subtitle status，只读不动流水线", "list",
      () => startTask("/api/tasks/subtitle", { command: "status" }, { goto: "/subtitle", okMsg: "清单任务已启动" })));

  const envCard = h("section.card", {},
    h("div.card-head", {}, svgIcon("stetho"), h("div.card-title", { text: "环境自检" }), h("div.spacer"),
      btn("刷新", { cls: "btn-sm btn-ghost", icon: "refresh" }, () => route())));
  const rows = h("div.rows");
  rows.appendChild(rowOf(pu.installed ? "ok" : "bad", "p115 推送库已安装",
    pu.installed ? "可直接推送" : "未安装 p115 包，推送会失败"));
  rows.appendChild(rowOf(pu.cookie_set ? "ok" : "bad", "115 Cookie 已配置",
    pu.cookie_set ? "config/push115.yaml" : "Cookie 为空，推送前必须先填"));
  rows.appendChild(rowOf(pu.enabled ? "ok" : "na", "推送总开关", pu.enabled ? "开启" : "已关闭（全流程会跳过推送）"));
  rows.appendChild(rowOf(su.engine_ok ? "ok" : "bad", "转写引擎", su.engine_path || "未配置 asr.external_exe"));
  rows.appendChild(rowOf(su.webdav_root ? "ok" : "bad", "WebDAV 片库目录", su.webdav_root || "未配置 webdav.root"));
  rows.appendChild(rowOf(st && st.python && st.python !== "?" ? "ok" : "warn", "运行环境 Python " + (st && st.python || "?"),
    st ? ("MagnetFlow " + (st.version || "?") + " · 端口 " + (st.port || "?")) : ""));
  if (su.webdav_root) rows.appendChild(h("div.row", {}, tickBox("na"),
    h("div.grow", {}, h("div.name", { text: "WebDAV 连通性未验证" }),
      h("div.sub", { text: "设备：" + (su.device || "?") + " · 根目录：" + su.webdav_root })),
    btn("去测一次", { cls: "btn-sm btn-ghost" }, () => { location.hash = "#/subtitle"; })));
  envCard.appendChild(rows);
  if (!pu.cookie_set) {
    envCard.appendChild(h("div.note.warn", {},
      "115 Cookie 还没配置：到 ", h("a.k", { href: "#/settings", text: "设置 → 推送115" }), " 粘贴 Cookie，或设环境变量 ",
      h("code.k", { text: "P115_COOKIE" }), "。"));
  }
  if (su.failed) {
    envCard.appendChild(h("div.note.warn", { text: "字幕流水线有 " + su.failed + " 部失败，可到 字幕流水线 → 开始运行 勾选「重试失败条目」。" }));
  }

  const exportCard = h("section.card", {},
    h("div.card-head", {}, svgIcon("codefile"), h("div.card-title", { text: "导出文件" }), h("div.spacer"),
      btn("打开导出目录", { cls: "btn-sm btn-ghost", icon: "folder" }, async (ev) => {
        const b = ev.currentTarget;
        busy(b, true);
        try {
          const r = await api("/api/system/open", { method: "POST", body: { key: "exports" } });
          toast("已在服务器上打开：" + ((r && r.path) || "exports"), "success");
        } catch (e) { toast(e.message, "danger"); }
        busy(b, false);
      })));
  const exBody = h("div");
  exBody.appendChild(skeletonRows(4));
  exportCard.appendChild(exBody);
  api("/api/exports").then((d) => {
    const items = (d && (d.items || d.exports || d.files)) || [];
    const withUrl = items.filter((it) => it.url || it.download_url || it.href);
    if (!items.length) {
      exBody.replaceChildren(emptyBlock("codefile", "还没有导出文件",
        "跑一次采集后，harvester/out/<站点>/ 下会出现 latest.json 与时间戳快照。"));
      return;
    }
    const rws = h("div.rows");
    items.slice(0, 40).forEach((it) => {
      const url = it.url || it.download_url || it.href || "";
      const nameNode = url
        ? h("a.name", { href: url, target: "_blank", rel: "noopener", text: it.name || it.path || "文件" })
        : h("div.name", { text: it.name || "文件" });
      rws.appendChild(h("div.row", {},
        h("span.tick.na", { html: ICONS.codefile }),
        h("div.grow", {}, nameNode, it.path ? h("div.sub", { text: it.path }) : null),
        it.size_kb !== undefined && it.size_kb !== null ? h("span.size", { text: fmtKb(it.size_kb) }) : null,
        h("span.time", { text: fmtAgo(it.mtime || it.modified || it.created) })));
    });
    exBody.replaceChildren(rws);
    if (!withUrl.length) {
      exBody.appendChild(h("p.card-hint", { text: "接口未返回可下载链接，只显示文件名；要下载请点上面「打开导出目录」。" }));
    }
  }).catch((e) => exBody.replaceChildren(h("div.note.danger", { text: "读取导出列表失败：" + e.message })));

  const lastCard = h("section.card", {},
    h("div.card-head", {}, svgIcon("clock"), h("div.card-title", { text: "最近一次任务（按类型）" })));
  const lastKeys = Object.keys(last);
  if (!lastKeys.length) {
    lastCard.appendChild(emptyBlock("clock", "还没有任务记录", "跑一次采集或推送，这里会出现最近一次结果。"));
  } else {
    const lr = h("div.rows");
    lastKeys.forEach((k) => {
      const t = last[k] || {};
      lr.appendChild(h("div.row", {}, tickBox(t.status === "finished" ? "ok" : t.status === "running" ? "na" : "bad"),
        h("div.grow", {}, h("div.name", { text: (t.label || KIND_CN[k] || k) + " · #" + (t.id || "?") }),
          h("div.sub", { text: "退出码 " + (t.exit_code === null || t.exit_code === undefined ? "—" : t.exit_code) + " · " + fmtAgo(t.created) })),
        pill(t.status)));
    });
    lastCard.appendChild(lr);
  }

  view.replaceChildren(cards,
    h("section.card", {},
      h("div.card-head", {}, svgIcon("play"), h("div.card-title", { text: "快捷操作" }), h("div.spacer"),
        h("span.card-hint", { text: "点了会自动跳到对应页面并接着看日志" }))),
    tilesWrap(tiles),
    h("div.grid.g-split", {}, envCard, h("div", {}, exportCard, lastCard)));
};

function tilesWrap(grid) {
  /* .btn-tile 需要 .tile-ic + .tile-body 结构；把 grid 包一层保持卡片节奏 */
  const holder = h("div.card", {}, grid);
  holder.style.paddingTop = "4px";
  return holder;
}
function tileBtn(title, sub, icon, handler) {
  const b = h("button.btn-tile", {}, h("span.tile-ic", { html: ICONS[icon] || ICONS.play }),
    h("span.tile-body", {}, h("b", { text: title }), h("span", { text: sub })));
  b.addEventListener("click", handler);
  return b;
}

/* ============================================================ 4. 共享组件：schema 表单 */

/* 后端 schema 条目：{key,label,type,group,default,min,options,help}
   type: str|password|textarea|bool|int|float|enum|strlist
   返回 {node, getValues, dirtyEl, bindSave(saveFn)} —— getValues() 读当前表单值 */
function renderSchemaForm(schema, values, opts) {
  const o = opts || {};
  const groups = [];
  const gmap = {};
  schema.forEach((sp) => {
    const g = sp.group || "常规";
    if (!gmap[g]) { gmap[g] = { name: g, items: [] }; groups.push(gmap[g]); }
    gmap[g].items.push(sp);
  });

  const box = h("div");
  const getters = {};                 /* key -> () => 当前值 */
  const initial = {};
  const dirtyEl = h("span.dirty-dot", {}, svgIcon("warn"), "有未保存的修改");
  let dirty = false;
  const setDirty = (on) => {
    if (dirty === on) return;
    dirty = on;
    dirtyEl.classList.toggle("show", on);
  };

  function addField(parent, sp) {
    const key = sp.key;
    const cur = values && values[key] !== undefined ? values[key]
      : (sp.default !== undefined ? sp.default : "");
    initial[key] = JSON.parse(JSON.stringify(cur === undefined ? "" : cur));
    const f = h("div.field");
    const lab = h("div.field-label", {}, h("div.lab", { text: sp.label || key }));
    if (sp.help) lab.appendChild(h("div.help", { text: sp.help }));
    const body = h("div.field-body");
    let el = null;

    if (sp.type === "bool") {
      const input = h("input", { type: "checkbox" });
      input.checked = !!cur;
      const sw = h("label.switch", {}, input, h("span.track"),
        h("span.sw-text", {}, h("b", { text: "" })));
      const txt = sw.querySelector(".sw-text b");
      const paint = () => { txt.textContent = input.checked ? "开" : "关"; };
      paint();
      input.addEventListener("change", paint);
      el = input;
      body.appendChild(sw);
      getters[key] = () => input.checked;
    } else if (sp.type === "password") {
      const input = h("input", { type: "password", autocomplete: "new-password",
        placeholder: sp.placeholder || "（留空则不改动）" });
      input.value = String(cur == null ? "" : cur);
      const eye = h("button.pw-eye", { type: "button", title: "显示/隐藏",
        html: ICONS.eyeoff });
      eye.addEventListener("click", () => {
        const pw = input.type === "password";
        input.type = pw ? "text" : "password";
        eye.innerHTML = pw ? ICONS.eye : ICONS.eyeoff;
      });
      el = input;
      body.appendChild(h("div.input-pw", {}, input, eye));
      getters[key] = () => input.value.trim();
    } else if (sp.type === "enum") {
      const sel = h("select", {});
      const opts = sp.options || [];
      let has = false;
      opts.forEach((op) => {
        sel.appendChild(h("option", { value: op, text: op }));
        if (op === cur) has = true;
      });
      if (!has && cur !== "" && cur !== undefined && cur !== null) {
        sel.appendChild(h("option", { value: String(cur), text: String(cur) + "（当前值）" }));
      }
      if (has) sel.value = cur;
      el = sel;
      body.appendChild(sel);
      getters[key] = () => sel.value;
    } else if (sp.type === "int" || sp.type === "float") {
      const input = h("input", { type: "number",
        step: sp.type === "int" ? "1" : "any",
        min: sp.min !== undefined ? String(sp.min) : "0" });
      input.value = cur === "" || cur === null || cur === undefined ? "" : String(cur);
      el = input;
      body.appendChild(input);
      getters[key] = () => (input.value === "" ? (sp.default !== undefined ? sp.default : 0)
        : (sp.type === "int" ? Math.max(parseInt(input.value, 10) || 0, sp.min || 0)
          : Math.max(parseFloat(input.value) || 0, sp.min || 0)));
    } else if (sp.type === "strlist") {
      const arr = Array.isArray(cur) ? cur : [];
      const ta = h("textarea", { placeholder: "每行一个，留空 = 不限", rows: "4" });
      ta.value = arr.join("\n");
      el = ta;
      body.appendChild(ta);
      getters[key] = () => ta.value.split(/\r?\n/).map((s) => s.trim()).filter(Boolean);
    } else if (sp.type === "textarea") {
      const ta = h("textarea", { rows: "5" });
      ta.value = String(cur == null ? "" : cur);
      el = ta;
      body.appendChild(ta);
      getters[key] = () => ta.value.replace(/\s+$/, "");
    } else {
      const input = h("input", { type: "text" });
      input.value = String(cur == null ? "" : cur);
      el = input;
      body.appendChild(input);
      getters[key] = () => input.value.trim();
    }
    ["input", "change"].forEach((ev) => el.addEventListener(ev, () => {
      let now;
      try { now = JSON.stringify(getters[key]()); } catch (e) { now = ""; }
      setDirty(now !== JSON.stringify(initial[key]));
    }));
    f.appendChild(lab);
    f.appendChild(body);
    parent.appendChild(f);
  }

  groups.forEach((g) => {
    const fs = h("fieldset.fieldset", {}, h("legend", { text: g.name }));
    g.items.forEach((sp) => addField(fs, sp));
    box.appendChild(fs);
  });
  box.getValues = () => {
    const out = {};
    Object.keys(getters).forEach((k) => { out[k] = getters[k](); });
    return out;
  };
  box.markClean = () => {
    Object.keys(getters).forEach((k) => {
      try { initial[k] = JSON.parse(JSON.stringify(getters[k]())); } catch (e) { /* noop */ }
    });
    setDirty(false);
  };
  box.dirtyEl = dirtyEl;
  /* 统一的保存按钮：saveFn(values) 返回 Promise；成功后清 dirty */
  box.bindSave = (saveFn, okMsg) => {
    const save = btn("保存", { cls: "btn-primary", icon: "check" }, async () => {
      busy(save, true, "保存中…");
      try {
        await saveFn(box.getValues());
        box.markClean();
        toast(okMsg || "已保存（改动前的原文件已自动备份）", "success");
      } catch (e) { toast(e.message, "danger"); }
      busy(save, false);
    });
    const actions = h("div.form-actions", {}, save, dirtyEl, h("div.spacer", { style: "flex:1" }));
    if (o.pathText) actions.appendChild(h("span.cfg-path", { text: o.pathText }));
    box.appendChild(actions);
    box.saveBtn = save;
    return save;
  };
  return box;
}

/* 原始 YAML 折叠编辑器：collapse 打开时懒加载 GET，保存 PUT {text} */
function mountRawCollapse(host, getUrl, putUrl, label) {
  const ta = h("textarea", { rows: "18", spellcheck: "false" });
  const st = h("span.hint");
  const save = btn("保存 YAML", { cls: "btn-sm btn-primary-soft", icon: "check" });
  const reload = btn("重新读取", { cls: "btn-sm btn-ghost", icon: "refresh" });
  const body = h("div.collapse-body", {}, ta,
    h("div.form-actions", {}, save, reload, st));
  const head = h("button.collapse-head", { type: "button" }, svgIcon("chevR"),
    label || "直接编辑原始 YAML（高级）");
  const wrap = h("div.collapse", {}, head, body);
  let loaded = false;
  const load = async () => {
    st.textContent = "读取中…";
    try {
      const d = await api(getUrl);
      ta.value = d.text || "";
      st.textContent = d.path || "";
      loaded = true;
    } catch (e) { st.textContent = "读取失败：" + e.message; }
  };
  head.addEventListener("click", () => {
    const open = head.classList.toggle("open");
    body.classList.toggle("open", open);
    if (open && !loaded) load();
  });
  save.addEventListener("click", async () => {
    busy(save, true, "保存中…");
    try {
      await api(putUrl, { method: "PUT", body: { text: ta.value } });
      toast("YAML 已保存（语法校验通过）", "success");
      st.textContent = "";
    } catch (e) {
      toast("保存失败：" + e.message, "danger");
    }
    busy(save, false);
  });
  reload.addEventListener("click", load);
  host.appendChild(wrap);
  return wrap;
}

/* ============================================================ 4b. 共享组件：日志终端 + 任务面板 */

const LOG_HANDLES = [];              /* stopAll() 里引用：切视图时销毁全部终端 */

function logLineCls(line) {
  if (/\[界面\]/.test(line)) return "l-info";
  if (/ERROR|Traceback|CRITICAL|失败|错误|异常/.test(line)) return "l-err";
  if (/WARNING|WARN\b|警告|重试/.test(line)) return "l-warn";
  if (/完成|成功|全部|OK\b|done/i.test(line)) return "l-ok";
  if (/^[\s-]+$/u.test(line) || /跳过|SKIP|====|——/.test(line)) return "l-dim";
  return "";
}

function attachTaskLog(host, taskId, meta) {
  /* host 里放一台终端；自己轮询、自带挂起/恢复/终止按钮 */
  const m = meta || {};
  const pillSlot = h("span");
  const prog = h("span.term-prog");
  const pauseBtn = btn("挂起", { cls: "btn-sm btn-warn", icon: "clock" });
  const stopBtn = btn("终止", { cls: "btn-sm btn-danger", icon: "cross" });
  const title = h("div.term-title", {}, m.label || KIND_CN[m.kind] || "任务日志",
    h("span.kind", { text: "#" + String(taskId).slice(0, 8) +
      (m.kind ? " · " + (KIND_CN[m.kind] || m.kind) : "") }));
  const bodyEl = h("div.term-body");
  const jumpBtn = h("button.scroll-bottom", { type: "button" }, "回到底部");
  const pausedNote = h("span.term-paused-note", { style: "margin:8px 14px" },
    svgIcon("warn"), "进程已挂起，日志暂停推进");
  const foot = h("div.term-foot", {}, h("span.hint", {}), h("div.spacer"),
    h("span.hint", { text: "Ctrl+A 全选日志" }));
  const card = h("div.term-card", {},
    h("div.term-head", {},
      h("span.dot-traffic", {}, h("i"), h("i"), h("i")),
      title, pillSlot, pausedNote, h("div.spacer"), prog, pauseBtn, stopBtn),
    h("div", { style: "position:relative" }, bodyEl, jumpBtn),
    foot);
  host.replaceChildren(card);

  let seq = -1;
  let timerId = null;
  let stick = true;
  let gone = false;
  let curStatus = "running";
  let curPaused = false;
  let lastElapsed = 0;
  let elapsedFetched = false;

  bodyEl.addEventListener("scroll", () => {
    stick = bodyEl.scrollHeight - bodyEl.scrollTop - bodyEl.clientHeight < 48;
    jumpBtn.classList.toggle("show", !stick);
  });
  jumpBtn.addEventListener("click", () => {
    stick = true;
    jumpBtn.classList.remove("show");
    bodyEl.scrollTop = bodyEl.scrollHeight;
  });

  function paintCtl(t) {
    pillSlot.replaceChildren(pill(t.status));
    curStatus = t.status;
    curPaused = !!t.paused;
    prog.textContent = t.status === "running"
      ? (t.elapsed ? fmtDur(t.elapsed) : "") : "";
    const run = t.status === "running";
    pauseBtn.classList.toggle("hidden", !run);
    stopBtn.classList.toggle("hidden", !run);
    pausedNote.classList.toggle("show", run && curPaused);
    setDisabled(pauseBtn, !run);
    if (run) {
      const tn = _textNodeOf(pauseBtn);
      if (tn) tn.nodeValue = curPaused ? "恢复" : "挂起";
      pauseBtn._label = curPaused ? "恢复" : "挂起";
    }
    if (TERMINAL_STATUS[t.status]) {
      const el = t.elapsed !== undefined ? t.elapsed : lastElapsed;
      foot.firstChild.textContent = (STATUS_CN[t.status] || t.status) +
        " · 退出码 " + (t.exit_code === null || t.exit_code === undefined ? "—" : t.exit_code) +
        " · 用时 " + fmtDur(el || 0);
      if (t.progress && Object.keys(t.progress).length) {
        prog.textContent = Object.keys(t.progress).slice(0, 4)
          .map((k) => k + "=" + t.progress[k]).join(" · ");
      }
    } else {
      foot.firstChild.textContent = "日志实时滚动中（1.5s 增量拉取）";
      if (t.elapsed) prog.textContent = (prog.textContent ? prog.textContent + " · " : "") + "已运行 " + fmtDur(t.elapsed);
      if (t.progress && Object.keys(t.progress).length) {
        prog.textContent = Object.keys(t.progress).slice(0, 4)
          .map((k) => k + "=" + t.progress[k]).join(" · ");
      }
    }
  }

  function appendLines(lines) {
    if (!lines.length) return;
    if (bodyEl.querySelector(".term-empty")) bodyEl.replaceChildren();
    const frag = document.createDocumentFragment();
    lines.forEach((pair) => {
      const s = pair[0], text = String(pair[1] == null ? "" : pair[1]);
      frag.appendChild(h("div.term-line" + (logLineCls(text) ? "." + logLineCls(text) : ""), {},
        h("span.ln", { text: String(s) }), h("span.tx", { text: text })));
    });
    /* 控制 DOM 行数，超 3000 掐头 */
    bodyEl.appendChild(frag);
    while (bodyEl.childElementCount > 3000) bodyEl.removeChild(bodyEl.firstChild);
    if (stick) bodyEl.scrollTop = bodyEl.scrollHeight;
  }

  async function pollOnce() {
    if (gone) return;
    let d;
    try {
      d = await api("/api/tasks/" + taskId + "/log?after=" + seq);
    } catch (e) {
      if (e.status === 404) {
        gone = true;
        if (timerId) { clearInterval(timerId); timerId = null; }
        bodyEl.replaceChildren(h("div.term-empty", {},
          "日志已不在内存（服务重启后只保留在日志文件里）。可在 设置 → 数据目录 打开日志目录查看。"));
        pauseBtn.classList.add("hidden");
        stopBtn.classList.add("hidden");
        return;
      }
      return;                                        /* 瞬时错误：下轮再试 */
    }
    if (d.lines && d.lines.length) appendLines(d.lines);
    if (typeof d.seq === "number") seq = d.seq;
    /* log 接口没带 elapsed：终态时补拉一次任务详情拿用时 */
    if (TERMINAL_STATUS[d.status] && !elapsedFetched) {
      elapsedFetched = true;
      api("/api/tasks/" + taskId).then((one) => {
        const t = one && one.task;
        if (t) {
          /* history 只带 created/finished，elapsed 缺失时自己推算 */
          const el = t.elapsed !== undefined && t.elapsed !== null
            ? t.elapsed
            : (t.created ? Math.max(0, Math.floor((t.finished || t.created) - t.created)) : 0);
          lastElapsed = el;
          foot.firstChild.textContent = (STATUS_CN[t.status] || d.status) +
            " · 退出码 " + (t.exit_code === null || t.exit_code === undefined ? "—" : t.exit_code) +
            " · 用时 " + fmtDur(lastElapsed);
        }
      }).catch(() => {});
    }
    paintCtl({ status: d.status, paused: d.paused, exit_code: d.exit_code,
      elapsed: undefined, progress: d.progress });
    if (TERMINAL_STATUS[d.status] && timerId) {
      clearInterval(timerId);
      timerId = null;
      if (API_HISTORY_REFRESH) API_HISTORY_REFRESH();
    }
  }

  pauseBtn.addEventListener("click", async () => {
    const act = curPaused ? "resume" : "pause";
    setDisabled(pauseBtn, true);
    try {
      const r = await api("/api/tasks/" + taskId + "/action",
        { method: "POST", body: { action: act } });
      curPaused = !!r.paused;
      paintCtl({ status: "running", paused: curPaused, elapsed: 0, progress: {} });
      toast(act === "pause" ? "已挂起进程树，日志暂停推进" : "已恢复运行", "info");
      pollOnce();
    } catch (e) {
      toast(e.message, e.status === 409 ? "warn" : "danger");
    }
    setDisabled(pauseBtn, false);
  });
  stopBtn.addEventListener("click", async () => {
    const yes = await confirmModal("终止任务", "确定要终止这个任务吗？进程树会被结束。",
      { danger: true, okText: "终止" });
    if (!yes) return;
    setDisabled(stopBtn, true);
    try {
      await api("/api/tasks/" + taskId + "/action", { method: "POST", body: { action: "stop" } });
      toast("已发送终止指令", "success");
      pollOnce();
    } catch (e) {
      toast(e.message, e.status === 409 ? "warn" : "danger");
      setDisabled(stopBtn, false);
    }
  });

  pollOnce();
  timerId = addPoll(pollOnce, 1500);
  const handle = {
    id: taskId,
    destroy() {
      if (timerId) { clearInterval(timerId); timerId = null; }
      gone = true;
      const i = LOG_HANDLES.indexOf(handle);
      if (i >= 0) LOG_HANDLES.splice(i, 1);
    },
  };
  LOG_HANDLES.push(handle);
  return handle;
}

let API_HISTORY_REFRESH = null;       /* 终端接上时反向刷新任务列表的钩子 */

/* 任务面板：任务表格（类型/状态/时间/用时/退出码/操作）+ 下方独立日志终端
   kinds = 本视图关心的任务类型；超出时默认过滤，可切「全部类型」 */
function mountTaskPanel(host, kinds) {
  const kindsList = kinds && kinds.length ? kinds : null;
  let showAll = false;
  let attachedId = null;
  let attachedHandle = null;
  let lastSig = "";

  const logHost = h("div", { style: "margin-top:14px" });
  const hint = h("span.card-hint");
  const toggle = btn("显示全部类型", { cls: "btn-sm btn-ghost", icon: "list" });

  const tbody = h("tbody");
  const table = h("table.grid-table", {},
    h("thead", {}, h("tr", {},
      h("th", { text: "类型", style: "width:78px" }),
      h("th", { text: "任务 ID" }),
      h("th", { text: "状态", style: "width:92px" }),
      h("th", { text: "开始", style: "width:110px" }),
      h("th", { text: "用时", style: "width:82px" }),
      h("th", { text: "退出码", style: "width:72px" }),
      h("th.cell-act", { text: "" }))));
  table.appendChild(tbody);

  const card = h("section.card", {},
    h("div.card-head", {}, svgIcon("list"), h("div.card-title", { text: "任务与日志" }),
      h("div.spacer"), hint, kindsList ? toggle : null),
    h("div.table-wrap", {}, table),
    logHost);
  host.appendChild(card);

  toggle.addEventListener("click", () => {
    showAll = !showAll;
    toggle._label = showAll ? "只看本页类型" : "显示全部类型";
    const tn = _textNodeOf(toggle);
    if (tn) tn.nodeValue = toggle._label;
    lastSig = "";
    refresh();
  });

  function detachAttached() {
    if (attachedHandle) {
      attachedHandle.destroy();
      attachedHandle = null;
      attachedId = null;
    }
  }

  function emptyLog() {
    if (attachedId) return;
    const archived = lastList.some((t) => t.gone);
    logHost.replaceChildren(h("div.term-card", {},
      h("div.term-head", {},
        h("span.dot-traffic", {}, h("i"), h("i"), h("i")),
        h("div.term-title", {}, "执行日志")),
      h("div.term-body", { style: "height:120px;display:grid;place-items:center" },
        h("div.term-empty", { text: archived
          ? "上面的任务都已归档（服务重启后日志只留在文件里）。发起一个新任务，这里会实时滚动。"
          : "还没有选中的任务。点上方任意一行的「日志」按钮查看实时输出。" }))));
  }

  function rowOfTask(t) {
    const elapsed = t.elapsed !== undefined && t.elapsed !== null
      ? t.elapsed
      : (t.created ? Math.max(0, Math.floor((t.finished || Date.now() / 1000) - t.created)) : 0);
    const active = t.id === attachedId;
    const kindCn = KIND_CN[t.kind] || t.kind || "";
    const label = t.label || kindCn || "任务";
    const openBtn = btn(active ? "当前" : "日志", {
      cls: "btn-sm" + (active ? " btn-primary-soft" : " btn-ghost"), icon: "codefile" });
    if (active) setDisabled(openBtn, true);
    else openBtn.addEventListener("click", () => attach(t.id, t));
    const actCell = t.gone
      ? h("td.cell-act", {}, h("span.cell-time", {
        title: "服务重启后日志只留在文件里：设置 → 数据目录 → 运行日志", text: "—" }))
      : h("td.cell-act", {}, openBtn);
    const tr = h("tr" + (active ? ".sel" : ""), {},
      h("td", {}, h("span.cell-num", { text: kindCn || "—" })),
      h("td", {}, h("div.cell-title", { title: label + "  #" + t.id,
        text: "#" + String(t.id).slice(0, 8) })),
      h("td", {}, pill(t.status), t.paused ? h("span.pill.pill-warn", { style: "margin-left:4px" }, "挂起") : null),
      h("td.cell-time", { title: fmtTime(t.created), text: t.created ? fmtAgo(t.created) : "—" }),
      h("td.cell-num", { text: elapsed ? fmtDur(elapsed) : "—" }),
      h("td.cell-num", { text: t.exit_code === null || t.exit_code === undefined ? "—" : String(t.exit_code) }),
      actCell);
    return tr;
  }

  let lastList = [];
  let limit = 6;
  function renderTable(list) {
    lastList = list;
    if (!list.length) {
      tbody.replaceChildren(h("tr", {}, h("td", { colspan: "7" },
        emptyBlock("list", "还没有任务记录",
          kindsList && !showAll ? "发起一个本页的任务后会出现在这里，也可以点右上角「显示全部类型」。" : "发起任务后会出现在这里。"))));
      return;
    }
    const trs = list.slice(0, limit).map(rowOfTask);
    if (list.length > limit) {
      trs.push(h("tr", {}, h("td", { colspan: "7", style: "text-align:center" },
        h("button.link", {
          text: "显示更多（还有 " + (list.length - limit) + " 条）",
          onClick: () => { limit = 50; renderTable(lastList); },
        }))));
    }
    tbody.replaceChildren(...trs);
  }

  async function refresh() {
    let d;
    try { d = await api("/api/tasks"); } catch (e) { return; }
    let list = (d && d.tasks) || [];
    const total = list.length;
    if (kindsList && !showAll) list = list.filter((t) => kindsList.indexOf(t.kind) >= 0);
    const sig = JSON.stringify(list.map((t) => [t.id, t.status, t.paused, t.exit_code, attachedId]));
    if (sig !== lastSig) {
      lastSig = sig;
      renderTable(list);
      hint.textContent = kindsList && !showAll
        ? "本页类型 " + list.length + " 个 · 全部 " + total + " 个"
        : "共 " + total + " 个任务";
    }
    /* 没有选中终端时自动接一个：优先运行中，其次日志还在内存里的最新一条 */
    if (!attachedId) {
      const t = list.find((x) => x.status === "running") || list.find((x) => !x.gone);
      if (t) attach(t.id, t);
      else emptyLog();
    }
  }

  function attach(id, meta) {
    if (attachedId === id) return;
    detachAttached();
    attachedId = id;
    attachedHandle = attachTaskLog(logHost, id, meta || {});
    lastSig = "";
    renderTable(lastList);          /* 立刻刷新选中高亮 */
  }

  refresh();
  const timer = addPoll(refresh, 4000);
  API_HISTORY_REFRESH = () => { refresh().catch(() => {}); };
  /* 视图切换 stopAll 会清 timer；这里登记面板级清理 */
  const panelHandle = {
    destroy() {
      clearInterval(timer);
      detachAttached();
      if (API_HISTORY_REFRESH) API_HISTORY_REFRESH = null;
      const i = LOG_HANDLES.indexOf(panelHandle);
      if (i >= 0) LOG_HANDLES.splice(i, 1);
    },
  };
  LOG_HANDLES.push(panelHandle);
  /* 概览页发起任务跳转过来时，接管移交的任务 */
  const pendingId = takePending();
  if (pendingId) attach(pendingId);
  return { attach, refresh, panelHandle };
}

/* ============================================================ 5. 视图：采集 */

function swBox(label, def) {
  const input = h("input", { type: "checkbox" });
  input.checked = !!def;
  const txt = h("span.sw-text", {}, h("b", { text: def ? "开" : "关" }));
  input.addEventListener("change", () => { txt.querySelector("b").textContent = input.checked ? "开" : "关"; });
  return { node: h("label.switch", {}, input, h("span.track"), txt), input };
}

VIEWS["/harvest"] = async function (view) {
  let sites = [];
  try { sites = await loadSites(); } catch (e) { sites = []; }
  const sel = siteSelect(sites, null, true);
  const firstEnabled = sites.find((s) => s.enabled);
  if (firstEnabled) sel.value = firstEnabled.name;
  const p1 = h("input", { type: "number", min: "1", value: "1", style: "width:90px" });
  const p2 = h("input", { type: "number", min: "1", value: "3", style: "width:90px" });
  const kw = h("input", { type: "text", placeholder: "搜索关键词（留空 = 用站点配置里的默认词）", style: "max-width:280px" });
  const lim = h("input", { type: "number", min: "0", value: "0", style: "width:90px" });
  const dry = swBox("", false);
  const go = btn("开始采集", { cls: "btn-primary", icon: "play" }, async () => {
    const ps = Math.max(parseInt(p1.value, 10) || 1, 1);
    const pe = Math.max(parseInt(p2.value, 10) || ps, ps);
    const body = {
      site: sel.value,
      pages: [ps, pe],
      keyword: kw.value.trim(),
      limit: parseInt(lim.value, 10) || 0,
      dry_run: dry.input.checked,
    };
    busy(go, true, "启动中…");
    await startTask("/api/tasks/harvest", body, { goto: "/harvest", okMsg: "采集任务已启动" });
    busy(go, false);
  });
  const card = h("section.card", {},
    h("div.card-head", {}, svgIcon("download"), h("div.card-title", { text: "发起采集" }),
      h("div.spacer"),
      h("a.btn.btn-sm.btn-ghost", { href: "#/sites", text: "编辑站点规则" })),
    h("div.inline-row", {},
      h("span.inline-label", {}, "站点"), sel,
      h("span.inline-label", {}, "页码"), p1, h("span.inline-label", {}, "至"), p2,
      h("span.inline-label", {}, "上限"), lim,
      h("span.inline-label", { title: "0 = 不限", text: "（0=不限）" })),
    h("div.inline-row", {},
      h("span.inline-label", {}, "关键词"), kw,
      h("div.toolbar-sep"),
      h("div.checkline", {}, dry.node, h("span.inline-label", {}, "试运行（只打印不入库）")),
      h("div.spacer", { style: "flex:1" }), go),
    h("p.card-hint", { text: "关键词会替换列表页地址里的 {keyword}（比如 /search/8k/1 里的 8k）；留空则用站点规则里配置的默认词。上限 = 本次总共处理多少个详情页（跨所有页码累计，0 = 不限）；每个详情页只收录体积最大的那一条磁力。试运行不会写入磁力库。" }));
  view.replaceChildren(card);
  mountTaskPanel(view, ["harvest", "chain"]);
};

/* ============================================================ 6. 视图：磁力库 */

function copyText(txt) {
  const done = () => toast("已复制完整哈希（40 位）", "success");
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(txt).then(done, () => fallbackCopy(txt, done));
  } else {
    fallbackCopy(txt, done);
  }
}
function fallbackCopy(txt, done) {
  const ta = h("textarea", { style: "position:fixed;opacity:0" });
  ta.value = txt;
  document.body.appendChild(ta);
  ta.select();
  try { document.execCommand("copy"); done(); } catch (e) { toast("复制失败，请手动选择", "warn"); }
  document.body.removeChild(ta);
}

VIEWS["/library"] = async function (view) {
  const state = { q: "", site: "", pushed: "all", page: 1,
    sort: "first_seen", order: "desc", size: 50 };
  const selected = new Set();          /* 跨页保留的勾选 */

  const searchInput = h("input", { type: "search", placeholder: "搜索标题 / 文件名 / 完整哈希", value: state.q });
  const searchBox = h("div.search", {}, h("span.s-ic", { html: ICONS.search }), searchInput);
  const pushedSel = h("select.inline-sm", {},
    h("option", { value: "all", text: "全部状态" }),
    h("option", { value: "pending", text: "待推送" }),
    h("option", { value: "pushed", text: "已推送" }));
  const chipsBox = h("div.chips");
  const tableHost = h("div.card.flush", {}, skeletonRowsWrap());
  const pagerHost = h("div");
  const bulkCount = h("span", { text: "0" });
  const markPushedBtn = btn("标记已推送", { cls: "btn-sm btn-primary-soft", icon: "send" });
  const markUnpushBtn = btn("取消标记", { cls: "btn-sm btn-ghost", icon: "refresh" });
  const delBtn = btn("删除", { cls: "btn-sm btn-danger", icon: "trash" });
  const bulk = h("div.bulkbar", {}, h("b", {}, bulkCount, " 条已选"),
    markPushedBtn, markUnpushBtn, delBtn);
  function skeletonRowsWrap() {
    const w = h("div.skel-rows", { style: "padding:18px 20px" });
    for (let i = 0; i < 6; i++) w.appendChild(h("div.skel"));
    return w;
  }

  /* --- 批量条 --- */
  function syncBulk() {
    bulk.classList.toggle("show", selected.size > 0);
    bulkCount.textContent = String(selected.size);
  }
  async function bulkAction(action) {
    const hashes = Array.from(selected);
    if (!hashes.length) return;
    if (action === "delete") {
      const yes = await confirmModal("删除磁力", "确定删除选中的 " + hashes.length +
        " 条磁力吗？此操作不可撤销（只删库记录，不影响已推送的网盘文件）。",
      { danger: true, okText: "删除 " + hashes.length + " 条" });
      if (!yes) return;
    }
    [markPushedBtn, markUnpushBtn, delBtn].forEach((b) => setDisabled(b, true));
    try {
      const r = await api("/api/magnets/bulk", { method: "POST", body: { hashes, action } });
      toast(({ pushed: "已标记为已推送", unpushed: "已取消推送标记", delete: "已删除" })[action] +
        "：" + (r.affected || 0) + " 条", "success");
      selected.clear();
      syncBulk();
      loadAll();
    } catch (e) {
      toast(e.message, "danger");
    }
    [markPushedBtn, markUnpushBtn, delBtn].forEach((b) => setDisabled(b, false));
  }
  markPushedBtn.addEventListener("click", () => bulkAction("pushed"));
  markUnpushBtn.addEventListener("click", () => bulkAction("unpushed"));
  delBtn.addEventListener("click", () => bulkAction("delete"));

  /* --- facets chips --- */
  function chip(label, num, active, onClick) {
    const c = h("button.chip" + (active ? ".active" : ""), { type: "button" },
      label, num !== null && num !== undefined ? h("span.num", { text: String(num) }) : null);
    c.addEventListener("click", onClick);
    return c;
  }
  function renderChips(f) {
    const items = [];
    items.push(chip("全部", f.total, !state.site && state.pushed === "all", () => {
      state.site = ""; state.pushed = "all"; state.page = 1;
      pushedSel.value = "all";
      loadAll();
    }));
    items.push(chip("待推送", f.pending, state.pushed === "pending", () => {
      state.pushed = state.pushed === "pending" ? "all" : "pending";
      state.site = ""; state.page = 1;
      pushedSel.value = state.pushed;
      loadAll();
    }));
    (f.sites || []).forEach((s) => {
      items.push(chip(s.site, s.c, state.site === s.site, () => {
        state.site = state.site === s.site ? "" : s.site;
        state.page = 1;
        loadAll();
      }));
    });
    chipsBox.replaceChildren(...items);
  }

  /* --- 表格 --- */
  function headCell(label, sortKey) {
    const th = h("th" + (sortKey ? ".sortable" : ""), { text: label });
    if (sortKey) {
      th.appendChild(h("span.caret", { text: state.sort === sortKey && state.order === "asc" ? "▲" : "▼" }));
      if (state.sort === sortKey) th.classList.add("sorted");
      th.addEventListener("click", () => {
        if (state.sort === sortKey) state.order = state.order === "desc" ? "asc" : "desc";
        else { state.sort = sortKey; state.order = "desc"; }
        state.page = 1;
        loadList();
      });
    }
    return th;
  }
  /* 单条删除：只删库记录，不影响网盘文件 */
  async function deleteOne(it) {
    const yes = await confirmModal("删除这一条磁力",
      "确定从磁力库删除这条记录吗？此操作不可撤销（只删库记录，不影响已推送的网盘文件）。",
      { danger: true, okText: "删除", detail: (it.title || "(无标题)") + "\n" + it.infohash });
    if (!yes) return;
    try {
      const r = await api("/api/magnets/bulk",
        { method: "POST", body: { hashes: [it.infohash], action: "delete" } });
      selected.delete(it.infohash);
      syncBulk();
      toast("已删除 1 条", "success");
      loadAll();
    } catch (e) {
      toast("删除失败：" + e.message, "danger");
    }
  }

  function renderTable(d) {
    const items = d.items || [];
    const box = h("div.table-wrap");
    const allChecked = items.length > 0 && items.every((it) => selected.has(it.infohash));
    const headCb = h("input", { type: "checkbox" });
    headCb.checked = allChecked;
    headCb.addEventListener("change", () => {
      items.forEach((it) => {
        if (headCb.checked) selected.add(it.infohash); else selected.delete(it.infohash);
      });
      syncBulk();
      loadList(d);
    });
    const table = h("table.grid-table", {},
      h("thead", {}, h("tr", {},
        h("th.cell-check", {}, headCb),
        h("th", { text: "哈希" }), headCell("标题", "title"), headCell("大小", "size"),
        h("th", { text: "站点" }), h("th", { text: "状态" }), headCell("收录时间", "first_seen"),
        h("th.cell-act", { text: "操作" }))));
    const tb = h("tbody");
    items.forEach((it) => {
      const cb = h("input", { type: "checkbox" });
      cb.checked = selected.has(it.infohash);
      const pushed = Number(it.pushed) === 1;
      const hashEl = h("span.hashlink", { title: it.infohash, text: (it.infohash || "").slice(0, 12) });
      hashEl.addEventListener("click", () => copyText(it.infohash));
      const titleA = h("div.cell-title", { title: it.title || "" });
      if (it.source_url) {
        titleA.appendChild(h("a", { href: it.source_url, target: "_blank", rel: "noopener", text: it.title || "(无标题)" }));
      } else {
        titleA.appendChild(document.createTextNode(it.title || "(无标题)"));
      }
      const delBtn = h("button.iconbtn.danger", { type: "button", title: "删除这一条",
        html: ICONS.trash });
      delBtn.addEventListener("click", (ev) => {
        ev.stopPropagation();
        deleteOne(it);
      });
      const tr = h("tr" + (cb.checked ? ".sel" : ""), {},
        h("td.cell-check", {}, cb),
        h("td", {}, hashEl),
        h("td", {}, titleA),
        h("td.cell-num", { text: it.size_gb ? Number(it.size_gb).toFixed(2) + " GB" : "—" }),
        h("td.cell-num", { text: it.site || "—" }),
        h("td", {}, h("span.pill." + (pushed ? "pill-ok" : "pill-muted"), {}, pushed ? "已推送" : "待推送")),
        h("td.cell-time", { title: fmtTime(it.first_seen), text: fmtAgo(it.first_seen) }),
        h("td.cell-act", {}, delBtn));
      cb.addEventListener("change", () => {
        if (cb.checked) selected.add(it.infohash); else selected.delete(it.infohash);
        tr.classList.toggle("sel", cb.checked);
        syncBulk();
      });
      tb.appendChild(tr);
    });
    table.appendChild(tb);
    box.appendChild(table);
    if (!items.length) {
      box.replaceChildren(emptyBlock("magnet", "没有符合条件的磁力",
        "换个关键词、清掉站点/状态过滤，或先去「采集」页跑一轮。"));
    }
    tableHost.replaceChildren(box);
    /* 分页 */
    const pages = Math.max(1, d.pages || 1);
    const page = d.page || 1;
    const info = h("span.pager-info", {}, "共 " + (d.total || 0) + " 条 · 第 " + page + " / " +
      pages + " 页 · 每页 " + (d.size || state.size) + " 条");
    const prev = h("button.pbtn", { type: "button", title: "上一页" });
    prev.appendChild(svgIcon("chevL"));
    prev.disabled = page <= 1;
    prev.addEventListener("click", () => { state.page = page - 1; loadList(); });
    const next = h("button.pbtn", { type: "button", title: "下一页" });
    next.appendChild(svgIcon("chevR"));
    next.disabled = page >= pages;
    next.addEventListener("click", () => { state.page = page + 1; loadList(); });
    const nums = [];
    const lo = Math.max(1, Math.min(page - 2, pages - 4));
    for (let i = lo; i <= Math.min(pages, lo + 4); i++) {
      const b = h("button.pbtn" + (i === page ? ".active" : ""), { type: "button", text: String(i) });
      b.addEventListener("click", () => { state.page = i; loadList(); });
      nums.push(b);
    }
    pagerHost.replaceChildren(h("div.pager", {}, prev, ...nums, next,
      h("div.spacer"), info));
  }

  async function loadList(cached) {
    if (!cached) tableHost.replaceChildren(skeletonRowsWrap());
    try {
      /* 后端 pushed 过滤只认 0/1，all 表示不过滤 */
      const pVal = state.pushed === "pending" ? "0" : state.pushed === "pushed" ? "1" : "all";
      const d = cached || await api("/api/magnets?page=" + state.page +
        "&q=" + encodeURIComponent(state.q) + "&site=" + encodeURIComponent(state.site) +
        "&pushed=" + pVal + "&size=" + state.size +
        "&sort=" + state.sort + "&order=" + state.order);
      /* 删空最后一页后自动回退，别让用户停在空页 */
      if (!cached && (d.items || []).length === 0 && state.page > 1) {
        state.page -= 1;
        return loadList();
      }
      renderTable(d);
    } catch (e) {
      tableHost.replaceChildren(h("div.note.danger", { style: "margin:18px 20px", text: "读取失败：" + e.message }));
    }
  }
  async function loadFacets() {
    try { renderChips(await api("/api/magnets/facets")); } catch (e) { /* 保留旧 chips */ }
  }
  function loadAll() {
    loadList();
    loadFacets();
    syncBulk();
  }

  searchInput.addEventListener("input", debounce(() => {
    state.q = searchInput.value.trim();
    state.page = 1;
    loadAll();
  }, 350));
  pushedSel.addEventListener("change", () => {
    state.pushed = pushedSel.value;
    state.site = "";
    state.page = 1;
    loadAll();
  });

  view.replaceChildren(
    h("section.card", {},
      h("div.toolbar.grow-search", {}, searchBox,
        h("div.toolbar-sep"), pushedSel),
      h("div", { style: "margin-top:12px" }, chipsBox)),
    tableHost, pagerHost, bulk);

  loadAll();
};

/* ============================================================ 7. 视图：推送 115 */

/* 115 扫码登录：弹二维码 → 轮询 → 成功后写回 config/push115.yaml 的 cookie */
async function openQrLogin(onSaved) {
  const root = $("#modal-root"), box = $("#modal-box");
  if (!root || !box) return;
  const img = h("img", { alt: "115 登录二维码",
    style: "width:220px;height:220px;display:block;margin:0 auto;border-radius:8px;background:#fff;padding:8px;image-rendering:pixelated" });
  const statusEl = h("p.modal-text", { style: "text-align:center;margin-top:12px", text: "正在获取二维码…" });
  const cancel = h("button.btn.btn-ghost", { text: "取消" });
  const refresh = h("button.btn.btn-ghost", { text: "刷新二维码" });
  box.replaceChildren(
    h("h3.modal-title", { text: "扫码登录 115" }),
    h("div", { style: "display:grid;place-items:center;margin:8px 0 4px" }, img),
    statusEl,
    h("div.modal-actions", {}, refresh, cancel));
  root.classList.remove("hidden");

  let uid = null, timer = null, done = false;
  const myHash = currentHash;
  const stop = (hide) => {
    done = true;
    if (timer) { clearInterval(timer); timer = null; }
    if (hide !== false) root.classList.add("hidden");
  };
  cancel.addEventListener("click", () => stop(true));

  /* 取一张新码；过期或扫码失败时点「刷新二维码」重新来 */
  async function begin() {
    if (timer) { clearInterval(timer); timer = null; }
    uid = null;
    img.removeAttribute("src");
    statusEl.textContent = "正在获取二维码…";
    try {
      const d = await api("/api/115/qrcode/start", { method: "POST", body: {} });
      if (done) return;
      uid = d.uid;
      img.src = d.image;
      statusEl.textContent = "打开手机「115」App → 扫一扫，扫完在手机上点确认（5 分钟内有效）";
    } catch (e) {
      statusEl.textContent = "获取二维码失败：" + e.message;
      return;
    }
    timer = addPoll(poll, 2000);
  }
  refresh.addEventListener("click", () => { if (!done) begin(); });

  const poll = async () => {
    if (done || !uid) return;
    if (currentHash !== myHash) { stop(true); return; }   /* 切视图了，别再挂着 */
    let r;
    try {
      r = await api("/api/115/qrcode/status?uid=" + encodeURIComponent(uid));
    } catch (e) {
      statusEl.textContent = "查询登录状态失败：" + e.message;
      return;
    }
    if (r.status === "confirmed") {
      statusEl.textContent = "登录成功，正在保存 Cookie…";
      try {
        await api("/api/settings/push115",
          { method: "PUT", body: { values: { cookie: r.cookie } } });
        toast("115 登录成功，Cookie 已保存到 config/push115.yaml", "success");
        stop(true);
        if (onSaved) onSaved(r.cookie);
      } catch (e) {
        statusEl.textContent = "登录成功但保存失败：" + e.message;
        stop(false);
      }
      return;
    }
    if (r.status === "expired") {
      statusEl.textContent = "二维码已过期，点「刷新二维码」重来";
      if (timer) { clearInterval(timer); timer = null; }
      return;
    }
    const extra = r.msg && r.msg !== "老乡验证失败！" ? "（" + r.msg + "）" : "";
    statusEl.textContent = "等待扫码…" + extra;
  };
  begin();
}

VIEWS["/push"] = async function (view) {
  let conf;
  try { conf = await api("/api/settings/push115"); }
  catch (e) { throw new Error("读取推送配置失败：" + e.message); }
  const sites = await loadSites().catch(() => []);

  const box = h("div");
  if (conf.env_cookie) {
    box.appendChild(h("div.note", {}, "检测到环境变量 ", h("code.k", { text: "P115_COOKIE" }),
      "：Cookie 已由环境变量提供，表单里的 Cookie 留空保存不会清掉它。"));
  }
  if (conf.uses_env_placeholder) {
    box.appendChild(h("div.note", { text: "配置文件里的 Cookie 写的是 ${P115_COOKIE} 占位符，实际取值来自环境变量。" }));
  }
  if (!conf.values.cookie && !conf.env_cookie && !conf.uses_env_placeholder) {
    box.appendChild(h("div.note.warn", { text: "115 Cookie 还没配置：最省事的是点下面「扫码登录」（手机 115 App 扫一下自动填好）；也可以登录 115.com 后按 F12 执行 document.cookie 把整串粘进来。" }));
  }
  if (!conf.values.save_path) {
    box.appendChild(h("div.note.warn", { text: "保存目录为空：将用 115 网页上的默认目录。想接字幕流水线的话，把「字幕」设置里的片库目录对应填上（如 /云下载/学习资料/常规）。" }));
  }

  const form = renderSchemaForm(conf.schema, conf.values, { pathText: conf.path });
  form.bindSave(async (vals) => {
    await api("/api/settings/push115", { method: "PUT", body: { values: vals } });
  }, "推送设置已保存");
  const formCard = h("section.card", {},
    h("div.card-head", {}, svgIcon("send"),
      h("div.card-title", {}, "推送设置 ", h("span.path", { text: "config/push115.yaml" }))),
    form);
  box.appendChild(formCard);

  /* 测试 Cookie：拿表单里当前输入的值测 */
  const testBtn = btn("测试 Cookie", { cls: "btn-warn", icon: "flask" });
  const testLine = h("div.result-line");
  testBtn.addEventListener("click", async () => {
    busy(testBtn, true, "测试中…");
    testLine.classList.remove("show", "ok", "bad");
    try {
      const r = await api("/api/test/cookie",
        { method: "POST", body: { cookie: form.getValues().cookie } });
      testLine.textContent = (r.ok ? "✓ " : "✗ ") + r.msg;
      testLine.classList.add("show", r.ok ? "ok" : "bad");
    } catch (e) {
      testLine.textContent = "✗ " + e.message;
      testLine.classList.add("show", "bad");
    }
    busy(testBtn, false);
  });
  /* 测试保存目录：确认 115 网盘里真实存在（接口只认目录 ID，路径写错会落默认目录） */
  const dirBtn = btn("测试保存目录", { cls: "btn-warn", icon: "folder" });
  const dirLine = h("div.result-line");
  dirBtn.addEventListener("click", async () => {
    busy(dirBtn, true, "查询中…");
    dirLine.classList.remove("show", "ok", "bad");
    const v = form.getValues();
    try {
      const r = await api("/api/test/savedir",
        { method: "POST", body: { path: v.save_path, cookie: v.cookie } });
      dirLine.textContent = (r.ok ? "✓ " : "✗ ") + r.msg;
      dirLine.classList.add("show", r.ok ? "ok" : "bad");
    } catch (e) {
      dirLine.textContent = "✗ " + e.message;
      dirLine.classList.add("show", "bad");
    }
    busy(dirBtn, false);
  });

  /* 扫码登录：成功后自动写回 cookie 并重渲染（红条消失） */
  const qrBtn = btn("扫码登录", { cls: "btn-primary-soft", icon: "chip" }, () => {
    openQrLogin((cookie) => {
      const inp = form.querySelector("input[type=password]");
      if (inp) inp.value = cookie;
      route();
    });
  });
  const formActions = form.saveBtn.parentNode;
  formActions.insertBefore(qrBtn, formActions.children[1]);
  formActions.insertBefore(testBtn, formActions.children[2]);
  formActions.insertBefore(dirBtn, formActions.children[3]);
  form.appendChild(testLine);
  form.appendChild(dirLine);

  /* 发起推送卡 */
  const dry = swBox("", true);
  const lim = h("input", { type: "number", min: "0", value: "20", style: "width:100px" });
  const sel = siteSelect(sites, "全部站点（未推送的都推）", true);
  const pushBtn = btn("开始推送", { cls: "btn-primary", icon: "send" }, async () => {
    busy(pushBtn, true, "启动中…");
    await startTask("/api/tasks/push", {
      dry_run: dry.input.checked,
      limit: parseInt(lim.value, 10) || 0,
      site: sel.value,
    }, { goto: "/push", okMsg: "推送任务已启动" });
    busy(pushBtn, false);
  });
  const pickBtn = btn("推送最大合集", { cls: "btn-ghost", icon: "chip" }, async () => {
    const yes = await confirmModal("推送最大合集",
      "从最近一次采集导出里挑体积最大的那条合集推给 115（用 out/<站点>/latest.json）。继续？",
      { okText: "推送" });
    if (!yes) return;
    busy(pickBtn, true, "启动中…");
    await startTask("/api/tasks/pick-max", { site: "" }, { goto: "/push", okMsg: "选最大合集任务已启动" });
    busy(pickBtn, false);
  });
  box.appendChild(h("section.card", {},
    h("div.card-head", {}, svgIcon("play"), h("div.card-title", { text: "发起推送" })),
    h("div.inline-row", {},
      h("div.checkline", {}, dry.node, h("span.inline-label", {}, "试运行（不真的提交给 115）")),
      h("div.toolbar-sep"),
      h("span.inline-label", {}, "本次最多"), lim,
      h("span.inline-label", {}, "限定站点"), sel,
      h("div.spacer", { style: "flex:1" }), pickBtn, pushBtn),
    h("p.card-hint", { text: "推送顺序：库里「待推送」的磁力按收录时间逐条提交 115 离线下载；成功后自动标记为已推送。" })));

  view.replaceChildren(box);
  mountTaskPanel(view, ["push", "pick_max", "chain"]);
};

/* ============================================================ 8. 视图：字幕流水线 */

VIEWS["/subtitle"] = async function (view) {
  let conf;
  try { conf = await api("/api/settings/subtitle"); }
  catch (e) { throw new Error("读取字幕配置失败：" + e.message); }

  const box = h("div");
  const form = renderSchemaForm(conf.schema, conf.values, { pathText: conf.path });
  form.bindSave(async (vals) => {
    await api("/api/settings/subtitle", { method: "PUT", body: { values: vals } });
  }, "字幕设置已保存（改动精确落到 YAML 对应行）");

  /* 测试按钮 + 结果行 */
  const wvLine = h("div.result-line");
  const engLine = h("div.result-line");
  function showResult(line, ok, msg) {
    line.textContent = (ok ? "✓ " : "✗ ") + msg;
    line.classList.remove("ok", "bad");
    line.classList.add("show", ok ? "ok" : "bad");
  }
  const wvBtn = btn("测试 WebDAV", { cls: "btn-warn", icon: "db" });
  wvBtn.addEventListener("click", async () => {
    busy(wvBtn, true, "连接中…");
    wvLine.classList.remove("show");
    const v = form.getValues();
    try {
      const r = await api("/api/test/webdav", { method: "POST", body: {
        base_url: v["webdav.base_url"], root: v["webdav.root"],
        username: v["webdav.username"], password: v["webdav.password"],
        auth: v["webdav.auth"] } });
      showResult(wvLine, r.ok, r.msg);
    } catch (e) { showResult(wvLine, false, e.message); }
    busy(wvBtn, false);
  });
  const engBtn = btn("测试引擎", { cls: "btn-warn", icon: "chip" });
  engBtn.addEventListener("click", async () => {
    busy(engBtn, true, "检查中…");
    engLine.classList.remove("show");
    try {
      const r = await api("/api/test/engine",
        { method: "POST", body: { exe: form.getValues()["asr.external_exe"] } });
      showResult(engLine, r.ok, r.msg);
    } catch (e) { showResult(engLine, false, e.message); }
    busy(engBtn, false);
  });
  const actions = form.saveBtn.parentNode;
  actions.insertBefore(wvBtn, actions.children[1]);
  actions.insertBefore(engBtn, actions.children[2]);
  form.appendChild(wvLine);
  form.appendChild(engLine);
  box.appendChild(h("section.card", {},
    h("div.card-head", {}, svgIcon("captions"),
      h("div.card-title", {}, "字幕设置 ", h("span.path", { text: "subtitle/config.yaml" }))),
    form));

  /* 运行卡 */
  const lim = h("input", { type: "number", min: "0", value: "0", style: "width:100px" });
  const retry = swBox("", true);
  const runBtn = btn("开始运行", { cls: "btn-primary", icon: "play" }, async () => {
    const limit = parseInt(lim.value, 10) || 0;
    busy(runBtn, true, "启动中…");
    await startTask("/api/tasks/subtitle",
      { command: retry.input.checked ? "retry_failed" : "run", limit },
      { goto: "/subtitle", okMsg: "字幕流水线已启动" });
    busy(runBtn, false);
  });
  const statusBtn = btn("查看清单", { cls: "btn-ghost", icon: "list" }, async () => {
    busy(statusBtn, true, "启动中…");
    await startTask("/api/tasks/subtitle", { command: "status" },
      { goto: "/subtitle", okMsg: "清单任务已启动（只读）" });
    busy(statusBtn, false);
  });
  const doctorBtn = btn("流水线体检", { cls: "btn-ghost", icon: "stetho" }, async () => {
    busy(doctorBtn, true, "启动中…");
    await startTask("/api/tasks/subtitle", { command: "doctor" },
      { goto: "/subtitle", okMsg: "流水线体检已启动" });
    busy(doctorBtn, false);
  });
  box.appendChild(h("section.card", {},
    h("div.card-head", {}, svgIcon("play"), h("div.card-title", { text: "运行控制" })),
    h("div.inline-row", {},
      h("span.inline-label", {}, "本轮最多处理"), lim,
      h("span.inline-label", { text: "0 = 用配置里的值" }),
      h("div.toolbar-sep"),
      h("div.checkline", {}, retry.node, h("span.inline-label", {}, "重试失败条目")),
      h("div.spacer", { style: "flex:1" }),
      doctorBtn, statusBtn, runBtn),
    h("p.card-hint", { text: "「查看清单 / 体检」只读不动流水线，可随时点；「开始运行」会真的下载、转写、回写字幕。跑完自动关机在上面的设置里开。" })));

  view.replaceChildren(box);
  mountTaskPanel(view, ["subtitle", "doctor"]);
};

/* ============================================================ 9. 视图：站点规则 */

VIEWS["/sites"] = async function (view) {
  let d;
  try { d = await api("/api/sites"); }
  catch (e) { throw new Error("读取站点列表失败：" + e.message); }
  const sites = (d && d.sites) || [];
  if (!sites.length) {
    view.replaceChildren(h("section.card", {},
      emptyBlock("sliders", "还没有站点规则",
        "在 harvester/sites/ 放一个 yaml 即可被自动识别。")));
    return;
  }
  const wrap = h("div");
  sites.forEach((s) => {
    const input = h("input", { type: "checkbox" });
    input.checked = !!s.enabled;
    const txt = h("span.sw-text", {}, h("b", { text: s.enabled ? "启用" : "停用" }));
    input.addEventListener("change", async () => {
      setDisabled(input, true);
      try {
        await api("/api/sites/" + s.name + "/enabled",
          { method: "POST", body: { enabled: input.checked } });
        txt.querySelector("b").textContent = input.checked ? "启用" : "停用";
        toast("站点 " + s.name + " 已" + (input.checked ? "启用" : "停用"), "success");
        SITES_CACHE.at = 0;                       /* 让其它页缓存失效 */
      } catch (e) {
        input.checked = !input.checked;           /* 回滚 */
        toast("切换失败：" + e.message, "danger");
      }
      setDisabled(input, false);
    });
    const card = h("section.card", {},
      h("div.card-head", {}, svgIcon("sliders"),
        h("div.card-title", {}, s.name,
          h("span.pill." + (s.enabled ? "pill-ok" : "pill-muted"), { text: s.enabled ? "采集中" : "已停用" })),
        h("div.spacer"),
        h("label.switch", {}, input, h("span.track"), txt)));
    const meta = [];
    if (s.base_url) meta.push(s.base_url);
    if (s.url_template) meta.push("列表页 " + s.url_template);
    meta.push("库里 " + (s.db_count || 0) + " 条");
    if (s.pages && s.pages.length >= 2) meta.push("默认页 " + s.pages[0] + "–" + s.pages[1]);
    card.appendChild(h("p.card-hint", { text: meta.join(" · ") }));

    /* 编辑规则（懒加载） */
    const rulesBody = h("div.collapse-body");
    const rulesHead = h("button.collapse-head", { type: "button" }, svgIcon("chevR"),
      "编辑抓取规则（表单）");
    let loaded = false;
    rulesHead.addEventListener("click", async () => {
      const open = rulesHead.classList.toggle("open");
      rulesBody.classList.toggle("open", open);
      if (!open || loaded) return;
      rulesBody.appendChild(skeletonRows(4));
      try {
        const one = await api("/api/sites/" + s.name);
        const form = renderSchemaForm(one.schema, one.values);
        if (one.ajax_note) form.appendChild(h("div.note", { text: one.ajax_note }));
        form.bindSave(async (vals) => {
          await api("/api/sites/" + s.name, { method: "PUT", body: { values: vals } });
        }, "站点 " + s.name + " 规则已保存");
        rulesBody.replaceChildren(form);
        loaded = true;
      } catch (e) {
        rulesBody.replaceChildren(h("div.note.danger", { text: "读取失败：" + e.message }));
      }
    });
    card.appendChild(h("div.collapse", {}, rulesHead, rulesBody));

    /* 原始 YAML */
    mountRawCollapse(card, "/api/sites/" + s.name + "/raw",
      "/api/sites/" + s.name + "/raw",
      "原始 YAML（AJAX 详情规则等高级字段只能在这里改）");
    wrap.appendChild(card);
  });
  view.replaceChildren(wrap);
};

/* ============================================================ 10. 视图：设置 */

const PATH_CN = {
  exports: "导出目录", logs: "运行日志", config: "推送配置",
  subtitle_work: "字幕工作区", subtitle_logs: "字幕日志",
  subtitle_data: "字幕进度库", root: "项目根目录",
};
const SETTINGS_TABS = [
  { which: "push115", cn: "推送 115" },
  { which: "harvester", cn: "采集器" },
  { which: "subtitle", cn: "字幕流水线" },
];

VIEWS["/settings"] = async function (view) {
  const pane = h("div");
  let current = SETTINGS_TABS[0].which;
  const tabsBox = h("div.tabs");
  SETTINGS_TABS.forEach((t) => {
    const b = h("button.tab" + (t.which === current ? ".active" : ""), { type: "button", text: t.cn });
    b.dataset.which = t.which;
    b.addEventListener("click", () => {
      if (t.which === current) return;
      current = t.which;
      $$(".tab", tabsBox).forEach((x) => x.classList.toggle("active", x.dataset.which === current));
      renderPane(current);
    });
    tabsBox.appendChild(b);
  });
  tabsBox.appendChild(h("div.spacer"));

  async function renderPane(which) {
    pane.replaceChildren(skeletonRows(5));
    let conf;
    try { conf = await api("/api/settings/" + which); }
    catch (e) {
      pane.replaceChildren(h("div.note.danger", { text: "读取失败：" + e.message }));
      return;
    }
    const box = h("div");
    if (which === "push115") {
      if (conf.env_cookie) {
        box.appendChild(h("div.note", {}, "环境变量 ", h("code.k", { text: "P115_COOKIE" }),
          " 已设置，Cookie 可以不在这里填。"));
      }
      if (conf.uses_env_placeholder) {
        box.appendChild(h("div.note", { text: "yaml 里写的是 ${P115_COOKIE} 占位符。" }));
      }
    }
    const form = renderSchemaForm(conf.schema, conf.values, { pathText: conf.path });
    form.bindSave(async (vals) => {
      await api("/api/settings/" + which, { method: "PUT", body: { values: vals } });
    }, "已保存，注释和排版原样保留");
    const card = h("section.card", {},
      h("div.card-head", {}, svgIcon("gear"),
        h("div.card-title", {}, (SETTINGS_TABS.find((t) => t.which === which) || {}).cn,
          h("span.path", { text: conf.path }))),
      form);
    mountRawCollapse(card, "/api/settings/" + which + "/raw",
      "/api/settings/" + which + "/raw", "直接编辑原始 YAML（保存前会校验语法）");
    box.appendChild(card);
    pane.replaceChildren(box);
  }
  await renderPane(current);

  /* 数据目录卡 */
  const dirCard = h("section.card", {},
    h("div.card-head", {}, svgIcon("folder"), h("div.card-title", { text: "数据目录" }), h("div.spacer"),
      h("span.card-hint", { text: "点了会在本机弹出资源管理器" })));
  const plist = h("div.path-list");
  plist.appendChild(skeletonRows(5));
  dirCard.appendChild(plist);
  api("/api/system/paths").then((d) => {
    const rows = Object.keys(PATH_CN).filter((k) => d[k]).map((k) => {
      const open = btn("打开", { cls: "btn-sm btn-ghost", icon: "folder" });
      open.addEventListener("click", async () => {
        busy(open, true);
        try {
          const r = await api("/api/system/open", { method: "POST", body: { key: k } });
          toast("已打开：" + ((r && r.path) || k), "success");
        } catch (e) { toast(e.message, "danger"); }
        busy(open, false);
      });
      return h("div.path-row", {},
        tickBox(d[k].exists ? "ok" : "bad"),
        h("span.pl-name", { text: PATH_CN[k] }),
        h("span.pl-val", { title: d[k].path, text: d[k].path }),
        d[k].exists ? null : h("span.pill.pill-muted", { text: "不存在" }),
        open);
    });
    plist.replaceChildren(...rows);
  }).catch((e) => plist.replaceChildren(h("div.note.danger", { text: "读取目录列表失败：" + e.message })));

  view.replaceChildren(tabsBox, pane, dirCard);
};

/* ============================================================ 11. 启动 */

function boot() {
  renderNav();
  window.addEventListener("hashchange", route);
  /* 顶栏运行徽标 + 侧栏底部信息：全局 5s，不参与视图轮询登记 */
  setInterval(() => {
    if (!document.hidden) refreshRunBadge().catch(() => {});
  }, 5000);
  refreshRunBadge().catch(() => {});
  api("/api/system/status").then((st) => {
    const fa = $("#foot-app"), fd = $("#foot-disk"), te = $("#top-env");
    if (fa) fa.textContent = "MagnetFlow v" + (st.version || "?");
    if (fd) fd.textContent = st.disk_free_gb != null ? "磁盘剩余 " + st.disk_free_gb + " GB" : "";
    if (te) te.textContent = "端口 " + (st.port || "?") + " · Python " + (st.python || "?");
  }).catch(() => {});
  route();
}

boot();
