# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

arXiv Radar: a personal daily arXiv feed. A Python pipeline (`arxiv_radar/`) runs on GitHub Actions every weekday,
ranks new papers against the user's topics with a local model, and writes JSON into `data/`. A static, no-build
dashboard (`site/`: `index.html`, `app.js`, `style.css`) is deployed to GitHub Pages together with `data/`.
README.md has the user-facing description of every feature.

## Commands

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'                       # enough for the fast tests
pip install torch --index-url https://download.pytorch.org/whl/cpu && pip install -e '.[ml]'   # SetFit/SPECTER2/flan-t5

python -m pytest -q                           # fast suite (slow tests are deselected by default in pyproject)
python -m pytest -q tests/test_awards.py::test_drama_detection   # one test
HF_HUB_OFFLINE=1 python -m pytest -q -m slow  # SetFit end to end with a tiny BERT built locally (needs the ml extra)
node --check site/app.js                      # the only check for the dashboard script (CI runs it)

# No network, no models: recorded sample day + hashing embedder
python -m arxiv_radar --config tests/fixtures/config.test.yaml run --offline tests/fixtures/sample_day --dry-run --out out

python -m arxiv_radar run | refresh | citations | retrain | restat YYYY-MM-DD | export-bib FOLDER
python -m arxiv_radar build-site --out _site && python -m http.server -d _site   # dashboard locally
```

There is no linter configured.

## Architecture

### Pipeline (`pipeline.run`)
1. **Fetch.** With `awards.enabled` it reads the RSS feed of every arXiv archive (`awards.ALL_ARCHIVES`). Otherwise it
   reads only the user's categories. Metadata comes from the arXiv API in batches of 100. Every arXiv request goes
   through one `ArxivClient` with a shared 3 s `RateLimiter`; keep it that way.
2. `fetch.filter_by_categories` narrows the listing to the user's categories and recomputes `announce_type`.
   The full listing is only passed to `awards.compute`; it is never stored.
3. Followed authors are matched by normalized name (`authors.name_key` / `same_person`).
4. **Embed and pre-filter.** SPECTER2 embeddings, then `prefilter.score`: similarity to each topic's description,
   seed papers and positive papers, plus keyword nudges. The top N are shortlisted.
5. **Classify.** `classify.classify` runs SetFit one-vs-rest on the topics that have at least
   `min_positives_per_topic` positives. Other topics keep `prefilter.percentile_scores`, a within-day rank (relative,
   not absolute). The per-topic source is written to `score_source`.
6. **Assemble.** Each entry gets `scores`, `score` (best topic), `combined` (noisy-OR across topics; Daily sorts by
   it) and `why`. Then come INSPIRE links, TL;DRs, `stats.compute` and `awards.compute`.
7. **Write.** `days/<date>.json` and `index.json`. Then the side tasks in `_finish_side_tasks`: author matches and
   backfill, INSPIRE recheck of recent days, citations.

Training signals, all read through `Store`:
- **Positives:** topic `seed_ids`, papers saved in Library folders, and "I care" papers (`Store.positive_papers`).
- **Negatives:** deleted papers, plus each recent day's `background` sample (never-shortlisted papers). The current
  day's background is excluded.

The model is cached in `.cache/model` and retrained only when `classify.training_hash` changes. The awards use no
model: they are rules and a fixed-weight title score (`awards.WEIGHTS`).

### Data contract between pipeline and dashboard
- **Dashboard-owned** (edited in the browser, committed on Sync): `feedback.json`, `library.json`, `authors.json`,
  `my_papers.json`, `settings.json`.
- **Pipeline-owned:** `days/*`, `index.json`, `author_matches.json`, `citations.json`, `seeds.json`.
- `store.py`'s docstring documents the shapes.
- `settings.json` `categories` and `topics` override `config.yaml`. Go through `Store.categories()` and
  `pipeline.effective_config()`; don't read `cfg.categories` / `cfg.topics` directly in run paths. `cfg.categories`
  is still the base the dashboard editor compares against.

### Dashboard (`site/app.js`)
- Edits are recorded as **ops** in localStorage. The same `applyOp` function renders the view (base data + pending
  ops) and builds the Sync commit: it re-reads the remote files, re-applies the ops, and makes one commit via the
  Git Data API, retrying when the ref moved.
- **Adding a new editable field** means touching:
  - `DEFAULTS` in both `store.py` and `app.js`
  - `OP_FILE`, `applyOp` and the `summarizeOps` labels
  - `push()`, only if repeated edits should collapse or cancel
- Day files keep the topic names that were current when they were written. The dashboard reads topics from
  `d.topics` / `p.scores`, not from `index.json`, so old days still render after a rename.
- Some logic is mirrored between Python and JS: BibTeX (`bibtex.py` / `toBibtex`), combined score
  (`pipeline.combined_score` / `combinedScore`), and arXiv-ID normalization. Change both sides.
- `build_site` copies `site/` and `data/`, writes `config.json` (repo, branch, effective topics), and appends content
  hashes to the `app.js` / `style.css` URLs. Without those hashes, Pages caching hides new deploys.
- KaTeX is loaded from cdnjs and runs on elements with class `tex`.

### Workflows
`.github/workflows/daily.yml` has two modes:
- `run`: scheduled, or `workflow_dispatch`.
- `refresh`: on pushes to `main` that touch `site/**`, `arxiv_radar/**`, `config.yaml`, or the dashboard-owned data
  files. It backfills authors, rechecks INSPIRE, refreshes citations and redeploys,
  without fetching papers.

The bot's own data commits use `GITHUB_TOKEN`, so they don't retrigger the workflow. `test.yml` runs pytest and
`node --check`.

## Testing notes
- Use the fixtures in `tests/fixtures/` (recorded RSS/Atom in real arXiv formats) and `httpx.MockTransport` for
  OpenAlex/INSPIRE. Tests must not hit the network.
- `embedding.backend: hashing` skips model downloads; with it, `classify` deliberately falls back to similarity.
- For dashboard changes:
  1. `build-site` into a scratch dir and serve it.
  2. Drive it with Playwright, desktop and a phone viewport.
  3. Check for horizontal overflow and console errors.
  4. If cdnjs is unreachable, serve KaTeX from a local copy.
