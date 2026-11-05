/* arXiv Radar dashboard. Static, no build step.
 *
 * Read-only data comes from the deployed site (data/*.json).
 * User edits are recorded as "ops" in localStorage, applied on top of the data for display,
 * and committed to the repo in a single commit on Sync (GitHub Git Data API).
 */
"use strict";

const EDIT_FILES = ["feedback.json", "library.json", "authors.json", "my_papers.json"];
const DEFAULTS = {
  "feedback.json": { deleted: {} },
  "library.json": { folders: {}, papers: {} },
  "authors.json": { authors: [] },
  "my_papers.json": { papers: [] },
};
const OP_FILE = {
  delete: "feedback.json", undelete: "feedback.json",
  save: "library.json", unsave: "library.json", folder_create: "library.json",
  folder_rename: "library.json", folder_delete: "library.json", folder_topic: "library.json",
  author_add: "authors.json", author_remove: "authors.json",
  mypaper_add: "my_papers.json", mypaper_remove: "my_papers.json",
};

const S = {
  cfg: {}, index: { days: [], topics: [] }, dayCache: {}, day: null,
  base: {}, view: {}, matches: { authors: {} }, citations: { papers: {} },
  pending: [], filters: { topic: "all", min: null, q: "", showDeleted: false, followedOnly: false },
  folder: null, openAuthor: null, openCite: null,
};

// ------------------------------------------------------------------ utils
const $ = (sel, el = document) => el.querySelector(sel);
const esc = (s) => String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const clone = (x) => JSON.parse(JSON.stringify(x));
const today = () => new Date().toISOString().slice(0, 10);
const ls = {
  get(k, d) { try { const v = localStorage.getItem("radar:" + k); return v == null ? d : JSON.parse(v); } catch { return d; } },
  set(k, v) { try { localStorage.setItem("radar:" + k, JSON.stringify(v)); } catch { /* storage unavailable */ } },
};

async function getJSON(url, fallback) {
  try {
    const r = await fetch(url, { cache: "no-cache" });
    if (!r.ok) return fallback;
    return await r.json();
  } catch { return fallback; }
}

let toastTimer;
function toast(msg, action) {
  const t = $("#toast");
  t.innerHTML = `<span>${esc(msg)}</span>` + (action ? `<button type="button">${esc(action.label)}</button>` : "");
  t.hidden = false;
  if (action) t.querySelector("button").onclick = () => { t.hidden = true; action.run(); };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { t.hidden = true; }, action ? 6000 : 3000);
}

function nameKey(name) {
  let s = name.normalize("NFKD").replace(/[̀-ͯ]/g, "").toLowerCase().trim();
  if (s.includes(",")) { const [l, f] = s.split(",", 2); s = `${f} ${l}`; }
  return s.replace(/[.\-']/g, " ").replace(/[^a-z ]/g, "").split(/\s+/).filter(Boolean);
}
const lastName = (n) => { const t = nameKey(n); return t[t.length - 1] || ""; };

// ------------------------------------------------------------------ ops
function paperMeta(p) {
  const topics = (p.topics && p.topics.length) ? p.topics : (p.best_topic ? [p.best_topic] : []);
  const keep = ["id", "title", "authors", "abstract", "abs_url", "pdf_url", "primary_category", "categories", "published", "date", "doi", "tldr"];
  const m = {};
  keep.forEach((k) => { if (p[k] != null) m[k] = p[k]; });
  m.topics = topics;
  return m;
}

function applyOp(f, op) {
  const lib = f["library.json"], fb = f["feedback.json"];
  const cleanup = () => {
    const used = new Set(Object.values(lib.folders).flatMap((x) => x.papers));
    Object.keys(lib.papers).forEach((id) => { if (!used.has(id)) delete lib.papers[id]; });
  };
  switch (op.type) {
    case "delete":
      fb.deleted[op.id] = { date: op.date, title: op.paper.title, abstract: op.paper.abstract, topics: op.paper.topics || [] };
      break;
    case "undelete": delete fb.deleted[op.id]; break;
    case "folder_create":
      if (!lib.folders[op.name]) lib.folders[op.name] = { topic: op.topic || null, papers: [] };
      break;
    case "save": {
      const fo = lib.folders[op.folder] || (lib.folders[op.folder] = { topic: null, papers: [] });
      if (!fo.papers.includes(op.id)) fo.papers.unshift(op.id);
      lib.papers[op.id] = { ...(lib.papers[op.id] || {}), ...op.paper, saved: lib.papers[op.id]?.saved || op.date };
      break;
    }
    case "unsave": {
      const fo = lib.folders[op.folder];
      if (fo) fo.papers = fo.papers.filter((x) => x !== op.id);
      cleanup();
      break;
    }
    case "folder_rename":
      if (lib.folders[op.from] && !lib.folders[op.to]) {
        const next = {};
        Object.entries(lib.folders).forEach(([k, v]) => { next[k === op.from ? op.to : k] = v; });
        lib.folders = next;
      }
      break;
    case "folder_delete": delete lib.folders[op.name]; cleanup(); break;
    case "folder_topic": if (lib.folders[op.name]) lib.folders[op.name].topic = op.topic || null; break;
    case "author_add": {
      const list = f["authors.json"].authors;
      op.names.forEach((n) => { if (!list.some((a) => a.name.toLowerCase() === n.toLowerCase())) list.push({ name: n, openalex_id: null, added: op.date }); });
      break;
    }
    case "author_remove":
      f["authors.json"].authors = f["authors.json"].authors.filter((a) => a.name !== op.name);
      break;
    case "mypaper_add": {
      const list = f["my_papers.json"].papers;
      if (!list.some((p) => p.query === op.query)) list.push({ query: op.query, label: op.label || null, added: op.date });
      break;
    }
    case "mypaper_remove":
      f["my_papers.json"].papers = f["my_papers.json"].papers.filter((p) => p.query !== op.query);
      break;
  }
  return f;
}

function normalizeFiles(f) {
  EDIT_FILES.forEach((n) => { f[n] = { ...clone(DEFAULTS[n]), ...(f[n] || {}) }; });
  return f;
}

function recompute() {
  S.view = normalizeFiles(clone(S.base));
  S.pending.forEach((op) => applyOp(S.view, op));
  const c = $("#pending-count");
  c.textContent = S.pending.length;
  c.hidden = S.pending.length === 0;
}

// An undo that exactly reverses the last unsynced edit removes that edit instead of queuing a new one.
const CANCELS = { undelete: "delete", unsave: "save", author_remove: "author_add", mypaper_remove: "mypaper_add" };
function cancelsLast(op) {
  const last = S.pending[S.pending.length - 1];
  if (!last || CANCELS[op.type] !== last.type) return false;
  if (op.type === "undelete") return last.id === op.id;
  if (op.type === "unsave") return last.id === op.id && last.folder === op.folder;
  if (op.type === "author_remove") return last.names.length === 1 && last.names[0] === op.name;
  return last.query === op.query;
}

function push(op) {
  op.date = op.date || today();
  if (cancelsLast(op)) S.pending.pop();
  else S.pending.push(op);
  ls.set("pending", S.pending);
  recompute();
  render();
}

// ------------------------------------------------------------------ GitHub sync
function gh() {
  const repo = ls.get("repo", S.cfg.repo || ""), branch = ls.get("branch", S.cfg.branch || "main"), token = ls.get("token", "");
  return { repo, branch, token, dir: S.cfg.data_dir || "data" };
}

async function api(path, opts = {}) {
  const { token } = gh();
  const r = await fetch(`https://api.github.com${path}`, {
    ...opts,
    headers: { Accept: "application/vnd.github+json", Authorization: `Bearer ${token}`, "X-GitHub-Api-Version": "2022-11-28", ...(opts.headers || {}) },
  });
  if (!r.ok) { const e = new Error(`${r.status} ${(await r.text()).slice(0, 200)}`); e.status = r.status; throw e; }
  return r.status === 204 ? null : r.json();
}

function b64decode(s) {
  const bin = atob(s.replace(/\n/g, ""));
  return new TextDecoder().decode(Uint8Array.from(bin, (c) => c.charCodeAt(0)));
}

async function readRemote(repo, dir, name, ref) {
  try {
    const res = await api(`/repos/${repo}/contents/${dir}/${name}?ref=${encodeURIComponent(ref)}`);
    return JSON.parse(b64decode(res.content));
  } catch (e) {
    if (e.status === 404) return clone(DEFAULTS[name]);
    throw e;
  }
}

async function loadEditableFromRepo() {
  const { repo, branch, token, dir } = gh();
  if (!repo || !token) return false;
  try {
    const files = await Promise.all(EDIT_FILES.map((n) => readRemote(repo, dir, n, branch)));
    EDIT_FILES.forEach((n, i) => { S.base[n] = files[i]; });
    return true;
  } catch (e) {
    console.warn("could not read repo files", e);
    return false;
  }
}

async function sync() {
  const { repo, branch, token, dir } = gh();
  if (!S.pending.length) { toast("Nothing to sync"); return; }
  if (!repo || !token) { openSettings(); toast("Add your repository and token first"); return; }
  const btn = $("#sync-btn");
  btn.disabled = true;
  try {
    for (let attempt = 0; attempt < 3; attempt++) {
      const ref = await api(`/repos/${repo}/git/ref/heads/${branch}`);
      const head = ref.object.sha;
      const commit = await api(`/repos/${repo}/git/commits/${head}`);
      const touched = [...new Set(S.pending.map((o) => OP_FILE[o.type]))];
      const files = {};
      for (const n of EDIT_FILES) files[n] = touched.includes(n) ? await readRemote(repo, dir, n, head) : clone(S.base[n] || DEFAULTS[n]);
      normalizeFiles(files);
      S.pending.forEach((op) => applyOp(files, op));
      const tree = await api(`/repos/${repo}/git/trees`, {
        method: "POST",
        body: JSON.stringify({
          base_tree: commit.tree.sha,
          tree: touched.map((n) => ({ path: `${dir}/${n}`, mode: "100644", type: "blob", content: JSON.stringify(files[n], null, 1) + "\n" })),
        }),
      });
      const summary = summarizeOps(S.pending);
      const newCommit = await api(`/repos/${repo}/git/commits`, {
        method: "POST",
        body: JSON.stringify({ message: `Dashboard edits: ${summary}`, tree: tree.sha, parents: [head] }),
      });
      try {
        await api(`/repos/${repo}/git/refs/heads/${branch}`, { method: "PATCH", body: JSON.stringify({ sha: newCommit.sha, force: false }) });
      } catch (e) {
        if (e.status === 422 && attempt < 2) continue; // branch moved; rebuild on the new head
        throw e;
      }
      S.base = files;
      S.pending = [];
      ls.set("pending", []);
      ls.set("base", { at: Date.now(), files: S.base });
      recompute();
      render();
      toast(`Synced: ${summary}`);
      return;
    }
  } catch (e) {
    console.error(e);
    toast(`Sync failed: ${e.message}`);
  } finally {
    btn.disabled = false;
  }
}

function summarizeOps(ops) {
  const n = {};
  ops.forEach((o) => { n[o.type] = (n[o.type] || 0) + 1; });
  const label = { delete: "deleted", undelete: "restored", save: "saved", unsave: "removed from folder", folder_create: "new folder",
    folder_rename: "renamed folder", folder_delete: "deleted folder", folder_topic: "folder topic", author_add: "author add",
    author_remove: "author removed", mypaper_add: "my paper added", mypaper_remove: "my paper removed" };
  return Object.entries(n).map(([k, v]) => `${v} ${label[k] || k}`).join(", ");
}

// ------------------------------------------------------------------ BibTeX (mirrors arxiv_radar/bibtex.py)
function ascii(s) { return (s || "").normalize("NFKD").replace(/[̀-ͯ]/g, ""); }
function bibYear(p) {
  for (const k of ["published", "date"]) if (p[k]) return String(p[k]).slice(0, 4);
  const m = /^(\d{2})(\d{2})\./.exec(p.id || "");
  return m ? `20${m[1]}` : "";
}
function citeKey(p) {
  const a = (p.authors && p.authors[0]) || "anon";
  const last = ascii(a.split(",")[0].trim().split(/\s+/).pop()).toLowerCase().replace(/[^a-z]/g, "") || "anon";
  const word = ascii(p.title).split(/\s+/).map((w) => w.toLowerCase().replace(/[^a-z]/g, "")).find((w) => w.length > 3) || "paper";
  return `${last}${bibYear(p)}${word}`;
}
function bibEsc(s) { return String(s || "").replace(/\s+/g, " ").trim().replace(/([&%#_$])/g, "\\$1"); }
function toBibtex(p) {
  const f = [["title", `{${bibEsc(p.title)}}`], ["author", (p.authors || []).map(bibEsc).join(" and ")], ["year", bibYear(p)],
    ["eprint", p.id], ["archivePrefix", "arXiv"], ["primaryClass", p.primary_category || ""], ["url", p.abs_url || `https://arxiv.org/abs/${p.id}`]];
  if (p.doi) f.push(["doi", p.doi]);
  return `@misc{${citeKey(p)},\n${f.filter(([, v]) => v).map(([k, v]) => `  ${k} = {${v}}`).join(",\n")}\n}`;
}
function download(name, text, type = "text/plain") {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], { type }));
  a.download = name;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ------------------------------------------------------------------ paper card
function followedSet() {
  return new Set((S.view["authors.json"]?.authors || []).map((a) => lastName(a.name)));
}

function card(p, opts = {}) {
  const deleted = !!S.view["feedback.json"].deleted[p.id];
  const folders = Object.entries(S.view["library.json"].folders).filter(([, f]) => f.papers.includes(p.id)).map(([k]) => k);
  const followedNames = new Set((p.followed_authors || []).map(lastName));
  const authors = (p.authors || []).slice(0, 12).map((a) => (followedNames.has(lastName(a)) ? `<b>${esc(a)}</b>` : esc(a))).join(", ")
    + ((p.authors || []).length > 12 ? `, … (+${p.authors.length - 12})` : "");
  const topics = S.index.topics || [];
  const thr = S.index.threshold ?? 0.5;
  const scores = p.scores ? topics.map((t) => {
    const v = p.scores[t] ?? 0;
    return `<span class="score ${v >= thr ? "hit" : ""}" title="${esc(t)}: ${v.toFixed(2)}">${esc(t)} <span class="bar"><i style="width:${Math.round(v * 100)}%"></i></span></span>`;
  }).join("") : "";
  const why = [];
  if (p.why) {
    Object.entries(p.why.keywords || {}).forEach(([t, k]) => why.push(`keywords (${esc(t)}): ${k.map(esc).join(", ")}`));
    if (p.why.closest && p.why.closest.title) why.push(`similar to <a href="https://arxiv.org/abs/${esc(p.why.closest.id)}" target="_blank" rel="noopener">${esc(p.why.closest.title)}</a>`);
  }
  return `<article class="card ${deleted ? "deleted" : ""}" data-id="${esc(p.id)}">
    <h3><a href="${esc(p.abs_url || `https://arxiv.org/abs/${p.id}`)}" target="_blank" rel="noopener">${esc(p.title)}</a></h3>
    <div class="authors">${authors}</div>
    <div class="meta">
      <span>${esc(p.id)}</span>
      ${p.primary_category ? `<span class="pill">${esc(p.primary_category)}</span>` : ""}
      ${p.announce_type === "cross" ? `<span class="pill">cross-list</span>` : ""}
      ${(p.followed_authors || []).length ? `<span class="pill follow">followed: ${p.followed_authors.map(esc).join(", ")}</span>` : ""}
      ${folders.map((f) => `<span class="pill saved">📁 ${esc(f)}</span>`).join("")}
      ${opts.date ? `<span>${esc(opts.date)}</span>` : ""}
    </div>
    ${scores ? `<div class="scores">${scores}</div>` : ""}
    ${p.tldr ? `<p class="tldr">${esc(p.tldr)}</p>` : ""}
    ${why.length ? `<div class="why">${why.join(" · ")}</div>` : ""}
    ${p.abstract ? `<details class="abs"><summary>Abstract</summary><p>${esc(p.abstract)}</p></details>` : ""}
    <div class="actions">
      <span class="save-menu"><button class="btn small" data-act="save-open">📁 Save</button></span>
      ${opts.inFolder ? `<button class="btn small" data-act="unsave" data-folder="${esc(opts.inFolder)}">Remove from folder</button>`
        : deleted ? `<button class="btn small" data-act="undelete">Restore</button>` : `<button class="btn small" data-act="delete">🗑 Delete</button>`}
      <a class="btn small" href="${esc(p.pdf_url || `https://arxiv.org/pdf/${p.id}`)}" target="_blank" rel="noopener">PDF</a>
    </div>
  </article>`;
}

// Papers rendered in the current view, by id, so card actions can find their metadata.
let cardPapers = {};

function bindCards(root) {
  root.querySelectorAll(".card").forEach((el) => {
    const p = cardPapers[el.dataset.id];
    el.querySelectorAll("[data-act]").forEach((b) => {
      b.onclick = (ev) => {
        ev.stopPropagation();
        const act = b.dataset.act;
        if (act === "delete") {
          push({ type: "delete", id: p.id, paper: paperMeta(p) });
          toast("Deleted", { label: "Undo", run: () => push({ type: "undelete", id: p.id }) });
        } else if (act === "undelete") push({ type: "undelete", id: p.id });
        else if (act === "unsave") push({ type: "unsave", id: p.id, folder: b.dataset.folder });
        else if (act === "save-open") openSaveMenu(b.parentElement, p);
      };
    });
  });
}

function openSaveMenu(anchor, p) {
  document.querySelectorAll(".save-pop").forEach((x) => x.remove());
  const lib = S.view["library.json"];
  const pop = document.createElement("div");
  pop.className = "save-pop";
  pop.innerHTML = Object.keys(lib.folders).map((f) => {
    const has = lib.folders[f].papers.includes(p.id);
    return `<button type="button" data-f="${esc(f)}">${has ? "✓ " : ""}${esc(f)}</button>`;
  }).join("") + `<form class="new"><input placeholder="New folder" aria-label="New folder name"><button class="btn small">Add</button></form>`;
  anchor.appendChild(pop);
  pop.querySelectorAll("button[data-f]").forEach((b) => {
    b.onclick = (ev) => {
      ev.stopPropagation();
      const f = b.dataset.f;
      if (lib.folders[f].papers.includes(p.id)) push({ type: "unsave", id: p.id, folder: f });
      else { push({ type: "save", id: p.id, folder: f, paper: paperMeta(p) }); toast(`Saved to ${f}`); }
    };
  });
  const form = pop.querySelector("form");
  form.onclick = (ev) => ev.stopPropagation();
  form.onsubmit = (ev) => {
    ev.preventDefault();
    const name = form.querySelector("input").value.trim();
    if (!name) return;
    push({ type: "folder_create", name });
    push({ type: "save", id: p.id, folder: name, paper: paperMeta(p) });
    toast(`Saved to ${name}`);
  };
  setTimeout(() => form.querySelector("input").focus(), 0);
}
document.addEventListener("click", () => document.querySelectorAll(".save-pop").forEach((x) => x.remove()));

// ------------------------------------------------------------------ views
async function loadDay(date) {
  if (!S.dayCache[date]) S.dayCache[date] = await getJSON(`data/days/${date}.json`, null);
  return S.dayCache[date];
}

async function renderDaily(v) {
  if (!S.index.days.length) {
    v.innerHTML = `<div class="empty">No days yet. The daily GitHub Action writes the first one after its next run.</div>`;
    return;
  }
  S.day = S.day || S.index.days[0].date;
  const d = await loadDay(S.day);
  if (!d) { v.innerHTML = `<div class="empty">Could not load ${esc(S.day)}.</div>`; return; }
  const f = S.filters;
  const thr = S.index.threshold ?? 0.5;
  if (f.min == null) f.min = 0;
  const deleted = S.view["feedback.json"].deleted;
  const q = f.q.toLowerCase();
  const all = d.papers;
  const counts = { all: all.filter((p) => !deleted[p.id]).length };
  (S.index.topics || []).forEach((t) => { counts[t] = all.filter((p) => !deleted[p.id] && (p.scores?.[t] ?? 0) >= thr).length; });
  const list = all.filter((p) => {
    if (!f.showDeleted && deleted[p.id]) return false;
    if (f.followedOnly && !(p.followed_authors || []).length) return false;
    const s = f.topic === "all" ? p.score : (p.scores?.[f.topic] ?? 0);
    if (f.topic !== "all" && s < thr && !(p.followed_authors || []).length) return false;
    if (s < f.min && !(p.followed_authors || []).length) return false;
    if (q && !`${p.title} ${p.abstract} ${(p.authors || []).join(" ")}`.toLowerCase().includes(q)) return false;
    return true;
  });
  if (f.topic !== "all") list.sort((a, b) => (b.scores?.[f.topic] ?? 0) - (a.scores?.[f.topic] ?? 0));
  cardPapers = Object.fromEntries(all.map((p) => [p.id, p]));
  const sources = d.score_source || {};
  const untrained = Object.entries(sources).filter(([, s]) => s !== "model").map(([t]) => t);
  const idx = S.index.days.findIndex((x) => x.date === S.day);
  v.innerHTML = `
    <div class="toolbar">
      <button class="btn small" id="prev" ${idx >= S.index.days.length - 1 ? "disabled" : ""} aria-label="Previous day">←</button>
      <select id="day">${S.index.days.map((x) => `<option ${x.date === S.day ? "selected" : ""}>${esc(x.date)}</option>`).join("")}</select>
      <button class="btn small" id="next" ${idx <= 0 ? "disabled" : ""} aria-label="Next day">→</button>
      <input type="search" class="grow" id="q" placeholder="Search title, abstract, authors" value="${esc(f.q)}">
    </div>
    <div class="chips">
      <button class="chip ${f.topic === "all" ? "on" : ""}" data-topic="all">All<span class="n">${counts.all}</span></button>
      ${(S.index.topics || []).map((t) => `<button class="chip ${f.topic === t ? "on" : ""}" data-topic="${esc(t)}">${esc(t)}<span class="n">${counts[t]}</span></button>`).join("")}
    </div>
    <div class="toolbar">
      <label class="check">Min score <input type="range" id="min" min="0" max="1" step="0.05" value="${f.min}"> <span id="minv">${Number(f.min).toFixed(2)}</span></label>
      <label class="check"><input type="checkbox" id="followed" ${f.followedOnly ? "checked" : ""}> Followed authors only</label>
      <label class="check"><input type="checkbox" id="showdel" ${f.showDeleted ? "checked" : ""}> Show deleted</label>
    </div>
    <div class="stat">${list.length} shown · ${d.counts?.shortlisted ?? all.length} shortlisted from ${d.counts?.fetched ?? "?"} new papers in ${esc((d.categories || []).join(", "))}
      ${untrained.length ? ` · <span title="Save or delete a few papers to train these topics">ranking by similarity for: ${untrained.map(esc).join(", ")}</span>` : ""}</div>
    <div class="cards">${list.map((p) => card(p)).join("") || `<div class="empty">No papers match these filters.</div>`}</div>`;
  $("#day").onchange = (e) => { S.day = e.target.value; render(); };
  $("#prev").onclick = () => { S.day = S.index.days[idx + 1].date; render(); };
  $("#next").onclick = () => { S.day = S.index.days[idx - 1].date; render(); };
  $("#q").oninput = debounce((e) => { f.q = e.target.value; render().then(() => { const el = $("#q"); el.focus(); el.setSelectionRange(el.value.length, el.value.length); }); }, 250);
  $("#min").oninput = (e) => { $("#minv").textContent = Number(e.target.value).toFixed(2); };
  $("#min").onchange = (e) => { f.min = Number(e.target.value); render(); };
  $("#followed").onchange = (e) => { f.followedOnly = e.target.checked; render(); };
  $("#showdel").onchange = (e) => { f.showDeleted = e.target.checked; render(); };
  v.querySelectorAll("[data-topic]").forEach((b) => { b.onclick = () => { f.topic = b.dataset.topic; render(); }; });
  bindCards(v);
}

function debounce(fn, ms) { let t; return (...a) => { clearTimeout(t); t = setTimeout(() => fn(...a), ms); }; }

function renderAuthors(v) {
  const authors = S.view["authors.json"].authors;
  const pendingNames = new Set(S.pending.filter((o) => o.type === "author_add").flatMap((o) => o.names));
  const backfilled = new Set(S.matches.backfilled || []);
  cardPapers = {};
  const items = authors.map((a) => {
    const m = S.matches.authors?.[a.name] || [];
    m.forEach((p) => { cardPapers[p.id] = p; });
    const open = S.openAuthor === a.name;
    return `<li>
      <div class="list-head">
        <button class="btn small" data-open="${esc(a.name)}" aria-expanded="${open}">${open ? "▾" : "▸"}</button>
        <span class="title">${esc(a.name)}</span>
        <span class="muted small">${m.length} paper${m.length === 1 ? "" : "s"}</span>
        ${pendingNames.has(a.name) ? `<span class="pending-note">not synced</span>` : !backfilled.has(a.name) ? `<span class="pending-note">backfill on next run</span>` : ""}
        <button class="btn small danger" data-remove="${esc(a.name)}">Remove</button>
      </div>
      ${open ? `<div class="cards" style="margin-top:10px">${m.map((p) => card(p, { date: p.date })).join("") || `<div class="empty">No papers yet.</div>`}</div>` : ""}
    </li>`;
  }).join("");
  v.innerHTML = `
    <section class="panel">
      <h2>Follow authors</h2>
      <p class="muted small">One name per line, or comma-separated. Matching ignores accents and middle names and accepts initials ("J. Müller" matches "Jörg Muller").
        New papers by these authors are always shown in Daily, and the last ${30} days are backfilled after you sync.</p>
      <form id="add-authors" class="row gap">
        <textarea id="author-names" rows="3" placeholder="Yann LeCun&#10;Fei-Fei Li"></textarea>
        <button class="btn primary">Follow</button>
      </form>
    </section>
    <section class="panel">
      <h2>Followed (${authors.length})</h2>
      ${authors.length ? `<ul class="list">${items}</ul>` : `<div class="empty">You are not following anyone yet.</div>`}
    </section>`;
  $("#add-authors").onsubmit = (e) => {
    e.preventDefault();
    const raw = $("#author-names").value;
    const names = raw.split(/\n/).flatMap((l) => (l.includes(",") && !/^[^,]+,\s*[^,\s]+$/.test(l.trim()) ? l.split(",") : [l]))
      .map((n) => n.trim()).filter(Boolean);
    if (!names.length) return;
    push({ type: "author_add", names });
    toast(`Following ${names.length} author${names.length === 1 ? "" : "s"}`);
  };
  v.querySelectorAll("[data-open]").forEach((b) => { b.onclick = () => { S.openAuthor = S.openAuthor === b.dataset.open ? null : b.dataset.open; render(); }; });
  v.querySelectorAll("[data-remove]").forEach((b) => { b.onclick = () => push({ type: "author_remove", name: b.dataset.remove }); });
  bindCards(v);
}

function sparkline(hist) {
  if (!hist || hist.length < 2) return "";
  const w = 90, h = 24, ys = hist.map((x) => x.count), max = Math.max(...ys), min = Math.min(...ys);
  const pts = ys.map((y, i) => `${(i / (ys.length - 1)) * w},${h - 2 - ((y - min) / ((max - min) || 1)) * (h - 4)}`).join(" ");
  return `<svg class="spark" width="${w}" height="${h}" viewBox="0 0 ${w} ${h}" aria-label="citations over time"><polyline points="${pts}"/></svg>`;
}

function renderMyPapers(v) {
  const mine = S.view["my_papers.json"].papers;
  const cites = S.citations.papers || {};
  const lastVisit = ls.get("cites-visit", null);
  const pendingQ = new Set(S.pending.filter((o) => o.type === "mypaper_add").map((o) => o.query));
  let total = 0, totalNew = 0;
  const rows = mine.map((m) => {
    const c = cites[m.query] || {};
    const fresh = lastVisit ? (c.citing || []).filter((x) => x.first_seen && x.first_seen > lastVisit).length : 0;
    total += c.count || 0;
    totalNew += fresh;
    const open = S.openCite === m.query;
    const title = c.resolved?.title || m.query;
    return `<li>
      <div class="list-head">
        <button class="btn small" data-open="${esc(m.query)}" aria-expanded="${open}">${open ? "▾" : "▸"}</button>
        <span class="title">${c.resolved?.url ? `<a href="${esc(c.resolved.url)}" target="_blank" rel="noopener">${esc(title)}</a>` : esc(title)}
          ${m.label ? `<span class="muted small"> · ${esc(m.label)}</span>` : ""}
          ${c.resolved?.year ? `<span class="muted small"> · ${esc(c.resolved.year)}</span>` : ""}</span>
        ${sparkline(c.history)}
        <span class="count" title="citations">${c.count ?? "–"}</span>
        ${fresh ? `<span class="new-badge">+${fresh} new</span>` : ""}
        <button class="btn small danger" data-remove="${esc(m.query)}">Remove</button>
      </div>
      ${pendingQ.has(m.query) ? `<div class="pending-note">Not synced yet. Counts appear after the next run.</div>` : ""}
      ${!c.count && !c.error && !pendingQ.has(m.query) && !c.checked ? `<div class="pending-note">Waiting for the next run.</div>` : ""}
      ${c.error ? `<div class="err">${esc(c.error)}</div>` : ""}
      ${open ? `<ol class="citing">${(c.citing || []).map((x) => `<li>${x.first_seen && lastVisit && x.first_seen > lastVisit ? `<span class="new-badge">new</span> ` : ""}<a href="${esc(x.url)}" target="_blank" rel="noopener">${esc(x.title || x.id)}</a>
          <span class="muted">${esc((x.authors || []).slice(0, 4).join(", "))}${(x.authors || []).length > 4 ? " et al." : ""}${x.year ? ` · ${esc(x.year)}` : ""}</span></li>`).join("") || "<li class='muted'>No citing works found yet.</li>"}</ol>` : ""}
    </li>`;
  }).join("");
  v.innerHTML = `
    <section class="panel">
      <h2>Track citations</h2>
      <p class="muted small">Add an arXiv ID, a DOI, or a title. Counts come from OpenAlex and refresh daily (they can lag a few weeks behind Google Scholar).</p>
      <form id="add-paper" class="row gap">
        <input id="paper-q" class="grow" style="flex:2 1 220px" placeholder="2504.08385 · 10.1145/… · Paper title">
        <input id="paper-label" style="flex:1 1 120px" placeholder="Label (optional)">
        <button class="btn primary">Add</button>
      </form>
    </section>
    <section class="panel">
      <div class="list-head" style="margin-bottom:8px"><h2 style="margin:0;flex:1">My papers (${mine.length})</h2>
        <span class="muted small">total</span> <span class="count">${total}</span> ${totalNew ? `<span class="new-badge">+${totalNew} since last visit</span>` : ""}
        <button class="btn small" id="mark-seen">Mark all seen</button></div>
      <div class="muted small">${S.citations.updated ? `Updated ${esc(S.citations.updated)}` : ""}</div>
      ${mine.length ? `<ul class="list">${rows}</ul>` : `<div class="empty">No papers yet.</div>`}
    </section>`;
  $("#add-paper").onsubmit = (e) => {
    e.preventDefault();
    const q = $("#paper-q").value.trim();
    if (!q) return;
    push({ type: "mypaper_add", query: q, label: $("#paper-label").value.trim() || null });
  };
  $("#mark-seen").onclick = () => { ls.set("cites-visit", today()); render(); };
  if (!lastVisit) ls.set("cites-visit", today());
  v.querySelectorAll("[data-open]").forEach((b) => { b.onclick = () => { S.openCite = S.openCite === b.dataset.open ? null : b.dataset.open; render(); }; });
  v.querySelectorAll("[data-remove]").forEach((b) => { b.onclick = () => push({ type: "mypaper_remove", query: b.dataset.remove }); });
}

function renderLibrary(v) {
  const lib = S.view["library.json"];
  const names = Object.keys(lib.folders);
  if (S.folder && !lib.folders[S.folder]) S.folder = null;
  S.folder = S.folder || names[0] || null;
  const fo = S.folder ? lib.folders[S.folder] : null;
  const papers = fo ? fo.papers.map((id) => lib.papers[id]).filter(Boolean) : [];
  cardPapers = Object.fromEntries(papers.map((p) => [p.id, p]));
  const topics = S.index.topics || [];
  v.innerHTML = `<div class="layout-lib">
    <aside class="panel">
      <h2>Folders</h2>
      <div class="folders">${names.map((n) => `<button class="folder ${n === S.folder ? "on" : ""}" data-folder="${esc(n)}"><span>${esc(n)}</span><span class="muted">${lib.folders[n].papers.length}</span></button>`).join("") || `<span class="muted small">No folders yet.</span>`}</div>
      <form id="new-folder" class="row gap" style="margin-top:10px">
        <input id="folder-name" placeholder="New folder" style="flex:1">
        <button class="btn small">Add</button>
      </form>
    </aside>
    <section>
      ${fo ? `<div class="panel">
        <div class="toolbar" style="margin:0">
          <h2 style="margin:0;flex:1 1 160px">${esc(S.folder)}</h2>
          <label class="check">Trains topic
            <select id="folder-topic"><option value="">(the paper's own topics)</option>${topics.map((t) => `<option ${fo.topic === t ? "selected" : ""}>${esc(t)}</option>`).join("")}</select>
          </label>
          <button class="btn small" id="rename">Rename</button>
          <button class="btn small" id="bib">BibTeX</button>
          <button class="btn small" id="json">JSON</button>
          <button class="btn small danger" id="del-folder">Delete folder</button>
        </div>
        <p class="muted small" style="margin:8px 0 0">Papers saved here count as positive examples for the classifier.</p>
      </div>
      <div class="cards">${papers.map((p) => card(p, { inFolder: S.folder, date: p.saved ? `saved ${p.saved}` : null })).join("") || `<div class="empty">Save papers here from the Daily or Authors tabs.</div>`}</div>`
      : `<div class="empty">Create a folder, or save a paper from the Daily tab.</div>`}
    </section></div>`;
  v.querySelectorAll("[data-folder]").forEach((b) => { b.onclick = () => { S.folder = b.dataset.folder; render(); }; });
  $("#new-folder").onsubmit = (e) => {
    e.preventDefault();
    const n = $("#folder-name").value.trim();
    if (!n) return;
    push({ type: "folder_create", name: n });
    S.folder = n;
    render();
  };
  if (fo) {
    $("#folder-topic").onchange = (e) => push({ type: "folder_topic", name: S.folder, topic: e.target.value || null });
    $("#rename").onclick = () => {
      const to = prompt("Rename folder", S.folder);
      if (to && to.trim() && to.trim() !== S.folder) { push({ type: "folder_rename", from: S.folder, to: to.trim() }); S.folder = to.trim(); render(); }
    };
    $("#del-folder").onclick = () => {
      if (confirm(`Delete folder "${S.folder}"? Its papers are removed from the library unless saved in another folder.`)) push({ type: "folder_delete", name: S.folder });
    };
    $("#bib").onclick = () => download(`${S.folder}.bib`, papers.map(toBibtex).join("\n\n") + "\n", "application/x-bibtex");
    $("#json").onclick = () => download(`${S.folder}.json`, JSON.stringify(papers, null, 1), "application/json");
  }
  bindCards(v);
}

// ------------------------------------------------------------------ routing + settings
const VIEWS = { daily: renderDaily, authors: renderAuthors, "my-papers": renderMyPapers, library: renderLibrary };

async function render() {
  const tab = (location.hash || "#daily").slice(1);
  const name = VIEWS[tab] ? tab : "daily";
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
  await VIEWS[name]($("#view"));
}

function applyTheme() {
  const t = ls.get("theme", "");
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}

function openSettings() {
  const { repo, branch, token } = gh();
  $("#set-repo").value = repo;
  $("#set-branch").value = branch;
  $("#set-token").value = token;
  $("#set-theme").value = ls.get("theme", "");
  $("#settings").showModal();
}

async function init() {
  applyTheme();
  [S.cfg, S.index, S.matches, S.citations] = await Promise.all([
    getJSON("config.json", {}),
    getJSON("data/index.json", { days: [], topics: [] }),
    getJSON("data/author_matches.json", { authors: {}, backfilled: [] }),
    getJSON("data/citations.json", { papers: {} }),
  ]);
  const site = await Promise.all(EDIT_FILES.map((n) => getJSON(`data/${n}`, clone(DEFAULTS[n]))));
  EDIT_FILES.forEach((n, i) => { S.base[n] = site[i]; });
  // The deployed snapshot can be older than your last sync; prefer the repo, then the last synced copy.
  if (!(await loadEditableFromRepo())) {
    const synced = ls.get("base", null);
    const deployed = Date.parse(S.index.generated || 0) || 0;
    if (synced && synced.files && synced.at > deployed) Object.assign(S.base, synced.files);
  }
  S.pending = ls.get("pending", []);
  recompute();
  window.addEventListener("hashchange", render);
  $("#sync-btn").onclick = sync;
  $("#settings-btn").onclick = openSettings;
  $("#set-save").onclick = () => {
    ls.set("repo", $("#set-repo").value.trim());
    ls.set("branch", $("#set-branch").value.trim() || "main");
    ls.set("token", $("#set-token").value.trim());
    ls.set("theme", $("#set-theme").value);
    applyTheme();
    loadEditableFromRepo().then(() => { recompute(); render(); });
  };
  $("#set-discard").onclick = () => {
    if (confirm(`Discard ${S.pending.length} unsynced edit(s)?`)) { S.pending = []; ls.set("pending", []); recompute(); render(); $("#settings").close(); }
  };
  await render();
}

init();
