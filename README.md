# arXiv Radar

A personal daily arXiv feed. Every weekday a GitHub Action:

1. fetches the new papers in your chosen arXiv categories,
2. ranks and tags them against your own **topics** with a local model (no paid API),
3. tracks papers by **authors you follow** and **citations to your own papers**,
4. publishes a dashboard to GitHub Pages.

On the dashboard you **delete** papers you don't care about, mark papers **I care** (a training signal
that doesn't file the paper anywhere), and **save** good ones into **folders**. All three train the
classifier on the next run. Paper links open the INSPIRE-HEP record when there is one, and arXiv otherwise.
LaTeX in titles and abstracts is rendered with KaTeX.

| Tab | What it does |
| --- | --- |
| **Daily** | Today's papers ranked by score, each with a gauge showing its score against your threshold, why it matched, and the abstract. Papers that reached a topic's threshold (or have a followed author) come first; the rest are folded under *N papers below your threshold*. Filter by topic, text, and under **Filters** by category, minimum score, followed authors. Delete (with undo), mark *I care*, or save to a folder. Mark the day as checked when you're done. |
| (Daily, top) | A one-line summary: how many papers were selected, and a bar of them by category. Open it for a pie chart, followed-author, saved, I care and deleted counts, and frequent terms. |
| **Calendar** | A month view of announcement days: which ones you've checked, which you haven't, and how many papers each had. Tap a day to open it in Daily. |
| **Stats** | Analytics for a day. *General*: everything fetched (categories, title terms, active authors, authors per paper, similarity to your topics). *Personalized*: what passed your filters (per-topic counts, score distribution, pass rate by category, top papers). |
| **Authors** | Follow one name or paste a list. Their new papers are always shown in Daily; the last 30 days are backfilled when you add someone. |
| **My papers** | Add your papers by arXiv ID, DOI or title. Shows citation counts over time, new citations since your last visit, and the citing papers. |
| **Library** | Your folders. Rename, delete, link a folder to a topic, mark it as a **classifier category**, export BibTeX or JSON. |

## How ranking works

```
RSS (today's IDs) → arXiv API (metadata, ≥3 s between requests)
  → SPECTER2 embeddings
  → pre-filter: similarity to each topic's description, seed papers and saved papers, plus keyword nudges → top N
  → SetFit classifier (SPECTER2 fine-tuned few-shot, one-vs-rest per topic) → score per topic
```

* **Cold start.** Until a topic has `min_positives_per_topic` examples (seed papers + saved papers),
  its score is the paper's similarity *rank* within the day, so it's relative, not absolute. The Daily tab
  says which topics are still in this mode.
* **Training data.** Positives are seed papers and saved papers. A saved paper counts for its folder's
  topic if the folder is linked to one, otherwise for the topics it was tagged with when you saved it.
  Negatives are deleted papers plus a random sample of papers that were never shortlisted.
* **Classifier categories.** Tick *Classifier category* on a Library folder to make it a topic of its own, named
  after the folder. From the next run every paper gets a score for it, the shortlist includes papers close to it,
  and Daily shows it as a filter chip. The folder's papers are its examples (cold start applies until it has
  `min_positives_per_topic` of them). A folder named like an existing topic adds its papers to that topic.
  Unticking it removes the topic from later days.
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

### Topics

Add, edit, rename or delete topics under **Settings → Topics**: a name, a description, example arXiv IDs (3+ so it can
be trained) and keywords. They are stored in `data/settings.json` and override `topics` in `config.yaml` from the next run.
Folders linked to a renamed topic follow the new name.

Daily's **Combined** ranking orders papers by the chance they match at least one topic (noisy-OR of the topic scores).
With one topic it equals that topic's score, and papers that fit several topics rank higher. The **Category** filter
narrows Daily to one arXiv category (primary or cross-list) or a whole archive (`astro-ph.*`).

On a phone, Daily, Calendar and Library sit in a bar at the bottom of the screen; Stats, Authors and My papers are
under **More**.

### Changing categories

Edit the list under **Settings → arXiv categories** on the dashboard and press **Sync**. It is stored in
`data/settings.json` and overrides `categories` in `config.yaml` from the next daily run.

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
python -m arxiv_radar restat 2026-11-05         # analytics for a day processed before Stats existed
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
| `days/YYYY-MM-DD.json` | pipeline | shortlisted papers with scores and reasons |
| `index.json` | pipeline | list of days, topics, threshold |
| `author_matches.json` | pipeline | papers per followed author |
| `citations.json` | pipeline | counts, history and citing works for your papers |
| `seeds.json` | pipeline | metadata cache for topic seed papers |
| `feedback.json` | dashboard | deleted and "I care" papers, days you've checked |
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
