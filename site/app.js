/* arXiv Radar dashboard. Static, no build step.
 *
 * Read-only data comes from the deployed site (data/*.json).
 * User edits are recorded as "ops" in localStorage, applied on top of the data for display,
 * and committed to the repo in a single commit on Sync (GitHub Git Data API).
 */
"use strict";

const EDIT_FILES = ["feedback.json", "library.json", "authors.json", "my_papers.json", "settings.json"];
const DEFAULTS = {
  "feedback.json": { deleted: {}, cared: {} },
  "library.json": { folders: {}, papers: {} },
  "authors.json": { authors: [] },
  "my_papers.json": { papers: [] },
  "settings.json": {},
};
const OP_FILE = {
  delete: "feedback.json", undelete: "feedback.json", care: "feedback.json", uncare: "feedback.json",
  save: "library.json", unsave: "library.json", folder_create: "library.json",
  folder_rename: "library.json", folder_delete: "library.json", folder_topic: "library.json",
  author_add: "authors.json", author_remove: "authors.json",
  mypaper_add: "my_papers.json", mypaper_remove: "my_papers.json",
  categories_set: "settings.json",
};

const S = {
  cfg: {}, index: { days: [], topics: [] }, dayCache: {}, day: null,
  base: {}, view: {}, matches: { authors: {} }, citations: { papers: {} },
  pending: [], filters: { topic: "all", min: null, q: "", showDeleted: false, followedOnly: false },
  folder: null, openAuthor: null, openCite: null, statsDay: null, statsTab: "general",
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

// INSPIRE record when the pipeline found one, arXiv otherwise.
const arxivUrl = (p) => p.abs_url || `https://arxiv.org/abs/${p.id}`;
const paperUrl = (p) => p.inspire_url || arxivUrl(p);

// LaTeX in titles, summaries and abstracts (KaTeX auto-render, loaded from the CDN).
const TEX_OPTS = {
  delimiters: [
    { left: "$$", right: "$$", display: true }, { left: "\\[", right: "\\]", display: true },
    { left: "$", right: "$", display: false }, { left: "\\(", right: "\\)", display: false },
  ],
  throwOnError: false,
};
function typeset(root) {
  if (!window.renderMathInElement) return;
  root.querySelectorAll(".tex").forEach((el) => {
    try { window.renderMathInElement(el, TEX_OPTS); } catch (e) { /* leave the raw text */ }
  });
}

// ------------------------------------------------------------------ ops
function paperMeta(p) {
  const topics = (p.topics && p.topics.length) ? p.topics : (p.best_topic ? [p.best_topic] : []);
  const keep = ["id", "title", "authors", "abstract", "abs_url", "pdf_url", "primary_category", "categories", "published", "date", "doi", "tldr", "inspire_url"];
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
      delete fb.cared[op.id];
      break;
    case "undelete": delete fb.deleted[op.id]; break;
    case "care":
      fb.cared[op.id] = { date: op.date, title: op.paper.title, abstract: op.paper.abstract, topics: op.paper.topics || [] };
      delete fb.deleted[op.id];
      break;
    case "uncare": delete fb.cared[op.id]; break;
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
    case "categories_set":
      f["settings.json"].categories = op.categories;
      break;
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
const CANCELS = { undelete: "delete", uncare: "care", unsave: "save", author_remove: "author_add", mypaper_remove: "mypaper_add" };
function cancelsLast(op) {
  const last = S.pending[S.pending.length - 1];
  if (!last || CANCELS[op.type] !== last.type) return false;
  if (op.type === "undelete" || op.type === "uncare") return last.id === op.id;
  if (op.type === "unsave") return last.id === op.id && last.folder === op.folder;
  if (op.type === "author_remove") return last.names.length === 1 && last.names[0] === op.name;
  return last.query === op.query;
}

const sameSet = (a, b) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());

function push(op) {
  op.date = op.date || today();
  // Repeated category edits collapse into one; an edit back to the synced list cancels it.
  if (op.type === "categories_set") {
    const last = S.pending[S.pending.length - 1];
    if (last && last.type === "categories_set") S.pending.pop();
    const base = (S.base["settings.json"] || {}).categories || S.cfg.categories || [];
    if (sameSet(op.categories, base)) {
      ls.set("pending", S.pending); recompute(); render(); return;
    }
  }
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
  const label = { delete: "deleted", undelete: "restored", care: "marked I care", uncare: "unmarked I care", save: "saved", unsave: "removed from folder", folder_create: "new folder",
    folder_rename: "renamed folder", folder_delete: "deleted folder", folder_topic: "folder topic", author_add: "author add",
    author_remove: "author removed", mypaper_add: "my paper added", mypaper_remove: "my paper removed", categories_set: "categories changed" };
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
  const cared = !!S.view["feedback.json"].cared[p.id];
  const folders = Object.entries(S.view["library.json"].folders).filter(([, f]) => f.papers.includes(p.id)).map(([k]) => k);
  const followedNames = new Set((p.followed_authors || []).map(lastName));
  const authors = (p.authors || []).slice(0, 12).map((a) => (followedNames.has(lastName(a)) ? `<b>${esc(a)}</b>` : esc(a))).join(", ")
    + ((p.authors || []).length > 12 ? `, … (+${p.authors.length - 12})` : "");
  const topics = Object.keys(p.scores || {});
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
    <h3><a class="tex" href="${esc(paperUrl(p))}" target="_blank" rel="noopener" title="${p.inspire_url ? "Open on INSPIRE" : "Open on arXiv"}">${esc(p.title)}</a></h3>
    <div class="authors">${authors}</div>
    <div class="meta">
      <span>${esc(p.id)}</span>
      ${p.primary_category ? `<span class="pill">${esc(p.primary_category)}</span>` : ""}
      ${p.announce_type === "cross" ? `<span class="pill">cross-list</span>` : ""}
      ${(p.followed_authors || []).length ? `<span class="pill follow">followed: ${p.followed_authors.map(esc).join(", ")}</span>` : ""}
      ${cared ? `<span class="pill cared">★ I care</span>` : ""}
      ${folders.map((f) => `<span class="pill saved">▸ ${esc(f)}</span>`).join("")}
      ${opts.date ? `<span>${esc(opts.date)}</span>` : ""}
    </div>
    ${scores ? `<div class="scores">${scores}</div>` : ""}
    ${p.tldr ? `<p class="tldr tex">${esc(p.tldr)}</p>` : ""}
    ${why.length ? `<div class="why">${why.join(" · ")}</div>` : ""}
    ${p.abstract ? `<details class="abs"><summary>Abstract</summary><p class="tex">${esc(p.abstract)}</p></details>` : ""}
    <div class="actions">
      <span class="save-menu"><button class="btn small" data-act="save-open">+ Save</button></span>
      <button class="btn small ${cared ? "on" : ""}" data-act="${cared ? "uncare" : "care"}" aria-pressed="${cared}"
        title="Train the classifier on this paper without adding it to a folder">${cared ? "★" : "☆"} I care</button>
      ${opts.inFolder ? `<button class="btn small" data-act="unsave" data-folder="${esc(opts.inFolder)}">Remove from folder</button>`
        : deleted ? `<button class="btn small" data-act="undelete">Restore</button>` : `<button class="btn small" data-act="delete">× Delete</button>`}
      ${p.inspire_url ? `<a class="btn small" href="${esc(arxivUrl(p))}" target="_blank" rel="noopener">arXiv</a>` : ""}
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
        if (act === "care") { push({ type: "care", id: p.id, paper: paperMeta(p) }); toast("Marked: the classifier will learn from it"); }
        else if (act === "uncare") push({ type: "uncare", id: p.id });
        else if (act === "delete") {
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

// ------------------------------------------------------------------ daily summary (donut)
const SLICE_VARS = ["--cat-1", "--cat-2", "--cat-3", "--cat-4", "--cat-5"];

function donut(parts, total) {
  const R = 60, r = 36, C = 70;
  let a0 = -Math.PI / 2;
  const pt = (rad, a) => `${(C + rad * Math.cos(a)).toFixed(2)},${(C + rad * Math.sin(a)).toFixed(2)}`;
  const slices = parts.map((p) => {
    const frac = p.n / total;
    if (frac >= 0.9999) {
      return `<g><title>${esc(p.label)}: ${p.n} (100%)</title><circle cx="${C}" cy="${C}" r="${(R + r) / 2}" fill="none" stroke="var(${p.color})" stroke-width="${R - r}"/></g>`;
    }
    const a1 = a0 + frac * 2 * Math.PI, large = frac > 0.5 ? 1 : 0;
    const d = `M${pt(R, a0)} A${R},${R} 0 ${large} 1 ${pt(R, a1)} L${pt(r, a1)} A${r},${r} 0 ${large} 0 ${pt(r, a0)} Z`;
    a0 = a1;
    return `<path d="${d}" fill="var(${p.color})" stroke="var(--surface)" stroke-width="2"><title>${esc(p.label)}: ${p.n} (${pct(p.n, total)})</title></path>`;
  }).join("");
  return `<svg class="donut" viewBox="0 0 140 140" role="img" aria-label="Selected papers by category">${slices}
    <text x="70" y="68" text-anchor="middle" class="donut-n">${total}</text><text x="70" y="86" text-anchor="middle" class="donut-l">selected</text></svg>`;
}

function dailySummary(d, deleted) {
  const sel = d.papers.filter((p) => !deleted[p.id] && ((p.topics || []).length || (p.followed_authors || []).length));
  const fetched = d.counts?.fetched ?? d.papers.length;
  const open = ls.get("summary-open", true);
  if (!sel.length) return `<details class="panel summary" ${open ? "open" : ""}><summary>Today's selection</summary><p class="muted small">Nothing passed your filters today.</p></details>`;
  const byCat = {};
  sel.forEach((p) => { const c = p.primary_category || "(unknown)"; byCat[c] = (byCat[c] || 0) + 1; });
  const ranked = Object.entries(byCat).sort((a, b) => b[1] - a[1]);
  const top = ranked.length > 6 ? ranked.slice(0, 5) : ranked.slice(0, 6);
  const parts = top.slice(0, 5).map(([label, n], i) => ({ label, n, color: SLICE_VARS[i] }));
  const rest = ranked.slice(parts.length).reduce((a, [, n]) => a + n, 0);
  if (rest) parts.push({ label: ranked.length - parts.length === 1 ? ranked[parts.length][0] : "Other", n: rest, color: "--series-other" });
  const total = sel.length;
  const fb = S.view["feedback.json"], lib = S.view["library.json"];
  const ids = new Set(d.papers.map((p) => p.id));
  const savedToday = new Set(Object.values(lib.folders).flatMap((f) => f.papers).filter((id) => ids.has(id))).size;
  const caredToday = Object.keys(fb.cared).filter((id) => ids.has(id)).length;
  const deletedToday = Object.keys(fb.deleted).filter((id) => ids.has(id)).length;
  const topics = d.topics || [];
  const terms = (d.stats?.personal?.top_terms || []).slice(0, 4).map((t) => t[0]);
  const followed = sel.filter((p) => (p.followed_authors || []).length).length;
  return `<details class="panel summary" ${open ? "open" : ""}><summary>Today's selection · ${total} of ${fetched} papers (${pct(total, fetched)})</summary>
    <div class="summary-body">
      ${donut(parts, total)}
      <ul class="legend-list">${parts.map((p) => `<li><i class="sw" style="background:var(${p.color})"></i><span class="name">${esc(p.label)}</span><span class="v">${p.n}</span><span class="muted">${pct(p.n, total)}</span></li>`).join("")}</ul>
      <dl class="facts">
        ${topics.length > 1 ? topics.map((t) => `<dt>${esc(t)}</dt><dd>${sel.filter((p) => (p.topics || []).includes(t)).length}</dd>`).join("") : ""}
        <dt>Followed authors</dt><dd>${followed}</dd>
        <dt>Saved · I care · deleted</dt><dd>${savedToday} · ${caredToday} · ${deletedToday}</dd>
        ${terms.length ? `<dt>Frequent terms</dt><dd class="terms">${terms.map(esc).join(", ")}</dd>` : ""}
      </dl>
    </div></details>`;
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
  const dayTopics = d.topics || S.index.topics || [];
  if (f.topic !== "all" && !dayTopics.includes(f.topic)) f.topic = "all";
  dayTopics.forEach((t) => { counts[t] = all.filter((p) => !deleted[p.id] && (p.scores?.[t] ?? 0) >= thr).length; });
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
    ${dailySummary(d, deleted)}
    <div class="chips">
      <button class="chip ${f.topic === "all" ? "on" : ""}" data-topic="all">All<span class="n">${counts.all}</span></button>
      ${dayTopics.map((t) => `<button class="chip ${f.topic === t ? "on" : ""}" data-topic="${esc(t)}">${esc(t)}<span class="n">${counts[t]}</span></button>`).join("")}
    </div>
    <div class="toolbar">
      <label class="check">Min score <input type="range" id="min" min="0" max="1" step="0.05" value="${f.min}"> <span id="minv">${Number(f.min).toFixed(2)}</span></label>
      <label class="check"><input type="checkbox" id="followed" ${f.followedOnly ? "checked" : ""}> Followed authors only</label>
      <label class="check"><input type="checkbox" id="showdel" ${f.showDeleted ? "checked" : ""}> Show deleted</label>
    </div>
    <div class="stat">${list.length} shown · ${d.counts?.shortlisted ?? all.length} shortlisted from ${d.counts?.fetched ?? "?"} new papers in ${esc((d.categories || []).join(", "))}${categoriesNote(d)}
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
  const sum = v.querySelector("details.summary");
  if (sum) sum.ontoggle = () => ls.set("summary-open", sum.open);
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

// ------------------------------------------------------------------ stats
const fmt = (n) => Number(n).toLocaleString();
const pct = (a, b) => (b ? `${Math.round((100 * a) / b)}%` : "–");

function tiles(items) {
  return `<div class="kpis">${items.map(([label, value, sub]) =>
    `<div class="tile"><div class="tile-label">${esc(label)}</div><div class="tile-value">${esc(value)}</div>${sub ? `<div class="tile-sub">${esc(sub)}</div>` : ""}</div>`).join("")}</div>`;
}

// Horizontal bars: one row per item, value printed at the end (doubles as the table view).
function barList(title, rows, opts = {}) {
  if (!rows || !rows.length) return `<section class="panel chart"><h3>${esc(title)}</h3><p class="muted small">No data.</p></section>`;
  const max = Math.max(...rows.map((r) => (opts.value ? opts.value(r) : r[1]))) || 1;
  return `<section class="panel chart"><h3>${esc(title)}</h3>${opts.note ? `<p class="muted small">${esc(opts.note)}</p>` : ""}
    <div class="bars">${rows.map((r) => {
      const v = opts.value ? opts.value(r) : r[1];
      const label = opts.label ? opts.label(r) : fmt(r[1]);
      return `<div class="bar-row" title="${esc(r[0])}: ${esc(label)}">
        <span class="bar-name ${opts.tex ? "tex" : ""}">${esc(r[0])}</span>
        <span class="bar-track"><i style="width:${Math.max(1, (100 * v) / max)}%"></i></span>
        <span class="bar-val">${esc(label)}</span></div>`;
    }).join("")}</div></section>`;
}

// Vertical histogram; series = [{key, label, cls}] stacked bottom-up. Threshold draws a marker line.
function histogram(title, bins, series, opts = {}) {
  if (!bins || !bins.length) return "";
  const total = (b) => series.reduce((a, s) => a + (b[s.key] || 0), 0);
  const max = Math.max(...bins.map(total)) || 1;
  const legend = series.length > 1 ? `<div class="legend">${series.map((s) => `<span><i class="sw ${s.cls}"></i>${esc(s.label)}</span>`).join("")}</div>` : "";
  const cols = bins.map((b, i) => {
    const tip = `${b.label}: ${series.map((s) => `${s.label} ${b[s.key] || 0}`).join(", ")}`;
    const segs = series.map((s) => (b[s.key] ? `<i class="seg ${s.cls}" style="height:${(100 * b[s.key]) / max}%"></i>` : "")).join("");
    const mark = opts.markAt != null && i === opts.markAt ? `<span class="mark" title="threshold"></span>` : "";
    return `<div class="col" title="${esc(tip)}" tabindex="0">${mark}<div class="stack">${segs}</div></div>`;
  }).join("");
  const ticks = bins.map((b, i) => `<span>${i === 0 || i === bins.length - 1 || i === Math.floor(bins.length / 2) ? esc(b.tick ?? b.label) : ""}</span>`).join("");
  return `<section class="panel chart"><h3>${esc(title)}</h3>${opts.note ? `<p class="muted small">${esc(opts.note)}</p>` : ""}${legend}
    <div class="hist" role="img" aria-label="${esc(title)}">${cols}</div><div class="ticks">${ticks}</div>
    ${opts.xlabel ? `<div class="muted small center">${esc(opts.xlabel)}</div>` : ""}</section>`;
}

async function renderStats(v) {
  if (!S.index.days.length) { v.innerHTML = `<div class="empty">No days yet.</div>`; return; }
  S.statsDay = S.statsDay || S.index.days[0].date;
  const d = await loadDay(S.statsDay);
  const head = `<div class="toolbar">
      <select id="stats-day">${S.index.days.map((x) => `<option ${x.date === S.statsDay ? "selected" : ""}>${esc(x.date)}</option>`).join("")}</select>
      <div class="seg-ctl" role="tablist">
        <button role="tab" aria-selected="${S.statsTab === "general"}" data-st="general">General</button>
        <button role="tab" aria-selected="${S.statsTab === "personal"}" data-st="personal">Personalized</button>
      </div></div>`;
  let body;
  if (!d || !d.stats) {
    body = `<div class="empty">No analytics for this day. Days processed from now on include them.</div>`;
  } else if (S.statsTab === "general") {
    const g = d.stats.general, pers = d.stats.personal;
    body = tiles([
      ["Papers fetched", fmt(g.count), (d.categories || []).join(", ")],
      ["New submissions", fmt(g.new), pct(g.new, g.count)],
      ["Cross-lists", fmt(g.cross), pct(g.cross, g.count)],
      ["Passed your filters", fmt(pers.count), pct(pers.count, g.count)],
    ]) + `<div class="chart-grid">
      ${histogram("Similarity to your topics", (g.similarity_hist || []).map((b) => ({ ...b, label: `${b.lo.toFixed(2)}–${b.hi.toFixed(2)}`, tick: b.lo.toFixed(2) })),
        [{ key: "passed", label: "Passed filters", cls: "s-hit" }, { key: "other", label: "Other papers", cls: "s-other" }],
        { note: "Every fetched paper by its closest match to any topic (cosine similarity).", xlabel: "similarity" })}
      ${barList("Primary category", g.by_category)}
      ${barList("Common title terms", g.top_terms, { tex: true })}
      ${histogram("Authors per paper", g.authors_per_paper.map(([label, n]) => ({ label, n })), [{ key: "n", label: "Papers", cls: "s-hit" }], { xlabel: "authors" })}
      ${barList("Most active authors", g.top_authors, { note: "Authors with more than one paper today." })}
    </div>`;
  } else {
    const p = d.stats.personal, g = d.stats.general;
    const thrBin = Math.min(9, Math.floor((p.threshold ?? 0.5) * 10));
    body = tiles([
      ["Passed your filters", fmt(p.count), `${pct(p.count, g.count)} of ${fmt(g.count)}`],
      ...p.by_topic.map(([t, n]) => [t, fmt(n), "at or above threshold"]),
      ["Followed authors", fmt(p.followed), "papers"],
    ]) + `<div class="chart-grid">
      ${histogram("Score distribution (shortlist)", p.score_hist.map(([lo, n]) => ({ label: `${lo}–${(Number(lo) + 0.1).toFixed(1)}`, tick: lo, n })),
        [{ key: "n", label: "Papers", cls: "s-hit" }], { markAt: thrBin, note: `Best topic score of each shortlisted paper. The marker shows the threshold (${p.threshold}).`, xlabel: "score" })}
      ${barList("Pass rate by category", p.hit_rate_by_category, { value: (r) => r[1] / r[2], label: (r) => `${r[1]}/${r[2]} · ${pct(r[1], r[2])}`, note: "Share of each category's papers that passed (categories with 3+ papers)." })}
      ${barList("Common title terms", p.top_terms, { tex: true })}
      ${barList("Primary category", p.by_category)}
      <section class="panel chart"><h3>Top papers</h3><ol class="top-list">${(p.top || []).map((x) =>
        `<li><a class="tex" href="${esc(paperUrl(x))}" target="_blank" rel="noopener">${esc(x.title)}</a> <span class="muted small">${x.score != null ? Number(x.score).toFixed(2) : ""}</span></li>`).join("")}</ol></section>
    </div>`;
  }
  v.innerHTML = head + body;
  $("#stats-day").onchange = (e) => { S.statsDay = e.target.value; render(); };
  v.querySelectorAll("[data-st]").forEach((b) => { b.onclick = () => { S.statsTab = b.dataset.st; render(); }; });
}

// ------------------------------------------------------------------ routing + settings
const VIEWS = { daily: renderDaily, stats: renderStats, authors: renderAuthors, "my-papers": renderMyPapers, library: renderLibrary };

async function render() {
  const tab = (location.hash || "#daily").slice(1);
  const name = VIEWS[tab] ? tab : "daily";
  document.querySelectorAll(".tabs a").forEach((a) => a.classList.toggle("active", a.dataset.tab === name));
  await VIEWS[name]($("#view"));
  typeset($("#view"));
}

function applyTheme() {
  const t = ls.get("theme", "");
  if (t) document.documentElement.dataset.theme = t; else delete document.documentElement.dataset.theme;
}

// arXiv taxonomy (archives and subject classes) for validating category edits.
const ARXIV_CATS = (() => {
  const g = (pre, subs) => subs.split(" ").map((x) => `${pre}.${x}`);
  return ["astro-ph", ...g("astro-ph", "CO EP GA HE IM SR"), "cond-mat",
    ...g("cond-mat", "dis-nn mes-hall mtrl-sci other quant-gas soft stat-mech str-el supr-con"),
    "gr-qc", "hep-ex", "hep-lat", "hep-ph", "hep-th", "math-ph", "nlin", ...g("nlin", "AO CD CG PS SI"),
    "nucl-ex", "nucl-th", "physics", ...g("physics", "acc-ph ao-ph app-ph atm-clus atom-ph bio-ph chem-ph class-ph comp-ph data-an ed-ph flu-dyn gen-ph geo-ph hist-ph ins-det med-ph optics plasm-ph pop-ph soc-ph space-ph"),
    "quant-ph", "math", ...g("math", "AC AG AP AT CA CO CT CV DG DS FA GM GN GR GT HO IT KT LO MG MP NA NT OA OC PR QA RA RT SG SP ST"),
    "cs", ...g("cs", "AI AR CC CE CG CL CR CV CY DB DC DL DM DS ET FL GL GR GT HC IR IT LG LO MA MM MS NA NE NI OH OS PF PL RO SC SD SE SI SY"),
    "econ", ...g("econ", "EM GN TH"), "eess", ...g("eess", "AS IV SP SY"),
    "q-bio", ...g("q-bio", "BM CB GN MN NC OT PE QM SC TO"), "q-fin", ...g("q-fin", "CP EC GN MF PM PR RM ST TR"),
    "stat", ...g("stat", "AP CO ME ML OT TH")];
})();

const currentCategories = () => S.view["settings.json"]?.categories || S.cfg.categories || S.index.days.length && S.dayCache[S.index.days[0].date]?.categories || [];

function categoriesNote(d) {
  const cur = currentCategories();
  if (!cur.length || sameSet(cur, d.categories || [])) return "";
  return ` · <span class="pending-note">from the next run: ${esc(cur.join(", "))}</span>`;
}

function renderCatEditor() {
  const cats = currentCategories();
  $("#cat-chips").innerHTML = cats.map((c) => `<span class="chip cat">${esc(c)}<button type="button" data-cat="${esc(c)}" aria-label="Remove ${esc(c)}">×</button></span>`).join("")
    || `<span class="muted small">No categories: the daily run would fall back to config.yaml.</span>`;
  $("#cat-chips").querySelectorAll("[data-cat]").forEach((b) => {
    b.onclick = () => {
      const next = currentCategories().filter((c) => c !== b.dataset.cat);
      if (!next.length) { $("#cat-msg").textContent = "Keep at least one category."; return; }
      push({ type: "categories_set", categories: next });
      renderCatEditor();
    };
  });
}

function addCategory() {
  const input = $("#cat-input");
  const raw = input.value.trim();
  const match = ARXIV_CATS.find((c) => c.toLowerCase() === raw.toLowerCase());
  $("#cat-msg").textContent = "";
  if (!raw) return;
  if (!match) { $("#cat-msg").textContent = `"${raw}" is not an arXiv category (e.g. hep-th, astro-ph.CO, cs.LG).`; return; }
  const cur = currentCategories();
  if (cur.includes(match)) { $("#cat-msg").textContent = `${match} is already in the list.`; return; }
  push({ type: "categories_set", categories: [...cur, match] });
  input.value = "";
  renderCatEditor();
}

function openSettings() {
  const { repo, branch, token } = gh();
  $("#set-repo").value = repo;
  $("#set-branch").value = branch;
  $("#set-token").value = token;
  $("#set-theme").value = ls.get("theme", "");
  $("#cat-msg").textContent = "";
  renderCatEditor();
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
  $("#cat-list").innerHTML = ARXIV_CATS.map((c) => `<option value="${c}">`).join("");
  $("#cat-add").onclick = addCategory;
  $("#cat-input").onkeydown = (e) => { if (e.key === "Enter") { e.preventDefault(); addCategory(); } };
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
  // KaTeX loads with `defer`; typeset again once it is there.
  window.addEventListener("load", () => typeset($("#view")));
}

init();
