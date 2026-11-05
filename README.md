# arXiv Radar

A personal daily arXiv feed. Every weekday a GitHub Action:

1. fetches the new papers in your chosen arXiv categories,
2. ranks and tags them against your own **topics** with a local model (no paid API),
3. writes a one-sentence TL;DR for the top papers,
4. tracks papers by **authors you follow** and **citations to your own papers**,
5. publishes a dashboard to GitHub Pages.

On the dashboard you **delete** papers you don't care about and **save** good ones into
**folders**. Both are training signals: the classifier is retrained on them on the next run.

| Tab | What it does |
| --- | --- |
| **Daily** | Today's shortlisted papers with per-topic scores, why each matched, TL;DR, abstract. Filter by topic, score, followed authors, text. Delete (with undo) or save to a folder. |
| **Authors** | Follow one name or paste a list. Their new papers are always shown in Daily; the last 30 days are backfilled when you add someone. |
| **My papers** | Add your papers by arXiv ID, DOI or title. Shows citation counts over time, new citations since your last visit, and the citing papers. |
| **Library** | Your folders. Rename, delete, link a folder to a topic, export BibTeX or JSON. |

## How ranking works

```
RSS (today's IDs) → arXiv API (metadata, ≥3 s between requests)
  → SPECTER2 embeddings
  → pre-filter: similarity to each topic's description, seed papers and saved papers, plus keyword nudges → top N
  → SetFit classifier (SPECTER2 fine-tuned few-shot, one-vs-rest per topic) → score per topic
  → flan-t5-base TL;DR for the top K
```

* **Cold start.** Until a topic has `min_positives_per_topic` examples (seed papers + saved papers),
  its score is the paper's similarity *rank* within the day, so it's relative, not absolute. The Daily tab
  says which topics are still in this mode.
* **Training data.** Positives are seed papers and saved papers. A saved paper counts for its folder's
  topic if the folder is linked to one, otherwise for the topics it was tagged with when you saved it.
  Negatives are deleted papers plus a random sample of papers that were never shortlisted.
* The model only retrains when that data changes. Models are kept in the Actions cache, not in git.

## Setup

1. **Fork or copy this repo.**
2. `cp config.example.yaml config.yaml`, then edit it:
   * `categories`: e.g. `[cs.LG, cs.CL]` (there is no default).
   * `topics`: a name, a one-line description, 3–20 `seed_ids` (arXiv IDs of papers that are exactly
     what you want), and optional `keywords` / `negative_keywords`.
   * Commit `config.yaml`.
3. **Settings → Pages → Source: GitHub Actions.**
4. **Actions → Daily arXiv run → Run workflow** to create the first day (or wait for the schedule: 01:30 UTC Mon–Fri).
5. To edit from the dashboard, create a
   [fine-grained token](https://github.com/settings/personal-access-tokens/new) for **this repository only**
   with **Contents: Read and write**. Paste it in the dashboard's ⚙ settings. Edits are queued in your browser
   and committed in one commit when you press **Sync**. Syncing author or "my papers" changes triggers a quick
   refresh run that backfills and redeploys.
6. Optional repository secrets:
   * `OPENALEX_MAILTO`: your email, sent to OpenAlex as recommended for its polite pool.
   * `S2_API_KEY`: a Semantic Scholar API key, used as a fallback when OpenAlex can't find one of your papers.

The workflow's push trigger and Pages deployment assume your default branch is `main`. Change
`branches:` in `.github/workflows/daily.yml` and `github_branch` in `config.yaml` if it isn't.

## Run locally

```bash
python -m venv .venv && . .venv/bin/activate
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -e '.[ml,dev]'

python -m arxiv_radar run                       # fetch + classify today → data/
python -m arxiv_radar run --dry-run --out out   # same, but write to out/
python -m arxiv_radar refresh                   # backfill authors + citations only
python -m arxiv_radar retrain                   # train from current feedback
python -m arxiv_radar export-bib "Must read"    # BibTeX for a folder
python -m arxiv_radar build-site && python -m http.server -d _site   # dashboard at :8000
```

No-download dry run with the recorded sample day (hashing embeddings, no model):

```bash
python -m arxiv_radar --config tests/fixtures/config.test.yaml run --offline tests/fixtures/sample_day --dry-run
```

Tests: `pytest` (fast). `pytest -m slow` also trains a tiny SetFit model end to end (needs the `ml` extra).

## Data files (`data/`)

| File | Written by | Content |
| --- | --- | --- |
| `days/YYYY-MM-DD.json` | pipeline | shortlisted papers with scores, reasons, TL;DRs |
| `index.json` | pipeline | list of days, topics, threshold |
| `author_matches.json` | pipeline | papers per followed author |
| `citations.json` | pipeline | counts, history and citing works for your papers |
| `seeds.json` | pipeline | metadata cache for topic seed papers |
| `feedback.json` | dashboard | deleted papers |
| `library.json` | dashboard | folders and saved papers |
| `authors.json` | dashboard | followed authors |
| `my_papers.json` | dashboard | papers to track citations for |

## Limits and privacy

* **Public site.** A public Pages site shows your topics, follows and library to anyone with the URL.
  Pages for private repos needs a paid GitHub plan.
* **Token.** The token is kept in your browser's localStorage. Scope it to this repo only.
* **arXiv API terms.** One request every 3 s over one connection. The fetcher enforces this. A day costs about
  one RSS request per category plus one API request per 100 papers.
* **Runtime.** On a GitHub CPU runner, expect about 5–15 minutes for 1–2k papers. Embedding is most of it, and the
  first run also downloads the models (~1 GB, then cached). SetFit training adds a few minutes when your feedback changes.
* **Citations** come from OpenAlex. They can lag by days to weeks and differ from Google Scholar. If OpenAlex
  indexes the preprint and the published version separately, add both.
* **Author matching** is by name (last name + compatible first name/initial), so people with the same name can collide.
  Affiliations are not in arXiv metadata.

## Prior art

* [ArxivDigest](https://github.com/AutoLLM/ArxivDigest): LLM relevance ratings from a natural-language interest statement, emailed.
* [gpt_paper_assistant](https://github.com/tatsu-lab/gpt_paper_assistant): author matching + GPT scoring on GitHub Actions, posted to Pages/Slack.
* [zotero-arxiv-daily](https://github.com/TideDra/zotero-arxiv-daily): ranks new papers by embedding similarity to your Zotero library.
* [arxiv-sanity-lite](https://github.com/karpathy/arxiv-sanity-lite): per-tag SVMs over TF-IDF trained from your tags.
* [Scholar Inbox](https://www.scholar-inbox.com/) ([paper](https://arxiv.org/abs/2504.08385)): hosted recommender trained on your ratings.

This project combines the GitHub Actions + Pages setup of the first two with a local, feedback-trained classifier
(SPECTER2 + SetFit) in place of a paid LLM.
