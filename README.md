# AmaliTech Competitor Intelligence Pipeline

A two-stage pipeline that (1) deep-crawls competitor websites and (2) uses an LLM to extract, score, and prioritise AI services — producing Excel and Markdown outputs to inform AmaliTech's AI service portfolio strategy.

---

## Table of Contents

- [Project Structure](#project-structure)
- [Stage 1 — Scraper](#stage-1--scraper)
- [Stage 2 — Analysis (Groq)](#stage-2--analysis-groq)
- [Running with Docker](#running-with-docker)
- [Environment Variables](#environment-variables)
- [Output Files](#output-files)
- [Tips & Troubleshooting](#tips--troubleshooting)

---

## Project Structure

```
project/
├── scraper.py            # Stage 1 — web crawler
├── analyse_groq.py       # Stage 2 — LLM extraction, scoring, Excel/MD output
├── manual_ingest.py      # Optional — ingest screenshots / text files
├── requirements.txt      # Python dependencies
├── Dockerfile
├── docker-compose.yml
├── competitors.csv       # Input: one URL per row
├── sites/                # Scraper output (one timestamped folder per competitor)
└── output/               # Analysis output (Excel, CSV, Markdown, state files)
```

---

## Stage 1 — Scraper

`scraper.py` deep-crawls each URL in `competitors.csv`, saves page text and images, and runs OCR. Results go into `sites/<domain>_<timestamp>/`.

### Input format

`competitors.csv` — auto-detects the URL column (`url`, `website`, `site`, `link`, `domain`, `competitor_url`):

```csv
competitor_name,website
Accenture,https://www.accenture.com
Nearshore,https://www.nearshore.com
```

Or a plain `.txt` file with one URL per line (lines starting with `#` are ignored).

### Run locally

```bash
pip install -r requirements.txt
python scraper.py
```

### Run in Docker

```bash
docker compose up -d
docker compose exec scraper python scraper.py
# With options:
docker compose exec -e MAX_DEPTH=5 scraper python scraper.py
```

### Scraper environment variables

| Variable | Default | Description |
|---|---|---|
| `MAX_DEPTH` | `3` | Link depth to crawl from the starting URL |
| `OCR_ENGINE` | `easyocr` | `easyocr` or `pytesseract` |
| `INPUT_FILE` | _(auto-detect)_ | Force a specific input file |

### Output per competitor

```
sites/accenture-com_2025-03-16_14-30-00/
├── pages/                 ← saved HTML files
├── images/                ← downloaded images
├── pages_text.csv         ← one row per page (url, page_title, depth, clean_text)
└── ocr_output.csv         ← one row per image (image_path, image_url, source_page_url, extracted_text)
```

---

## Stage 2 — Analysis (Groq)

`analyse_groq.py` reads the scraped `sites/` folders and for each competitor:

1. **Extracts** AI services using `llama-3.1-8b-instant` via the Groq API
2. **Scores** each service across 5 dimensions against AmaliTech's strategic context
3. **Assesses** 5 strategic hypotheses (pricing, partnerships, verticals, etc.)
4. **Writes** per-competitor Excel workbooks + a consolidated long-list workbook + a Markdown summary

### Scoring dimensions

| Dimension | Weight | What it measures |
|---|---|---|
| Strategic Fit | 30% | Alignment with AmaliTech's target industries and capabilities |
| Market Impact | 25% | Revenue potential and market size |
| Effort | 20% | Delivery feasibility (now / 6-12mo / 1-2yr roadmap) |
| Differentiation | 15% | Uniqueness vs. competitors |
| Market Credibility | 10% | Client trust signals, certifications, partnerships |

### Run (Docker — recommended)

```bash
docker compose exec \
  -e GROQ_API_KEY=<your_key> \
  -e GROQ_MODEL=llama-3.1-8b-instant \
  -e APP_DIR=/app \
  scraper python3 /app/input/analyse_groq.py
```

**Options:**

| Flag | Description |
|---|---|
| `--rerun-all` | Clear state and reprocess all competitors |
| `--competitor <name>` | Process only one competitor (fuzzy match on folder name) |
| `--max-pages <n>` | Limit pages read per competitor (default: 50) |
| `--dry-run` | Show what would be processed without calling the API |

### Groq rate limits (free tier)

- 6,000 tokens/minute for `llama-3.1-8b-instant`
- The script retries with exponential backoff (`15 × attempt` seconds) and truncates content to stay within limits
- Content is sampled using AI-keyword-weighted page selection so relevant pages are always included even in large scrapes

---

## Running with Docker

### First-time setup

```bash
docker compose build   # ~3-5 min (downloads EasyOCR model)
docker compose up -d
```

### Scrape all competitors

```bash
docker compose exec scraper python scraper.py
```

### Run analysis

```bash
docker compose exec \
  -e GROQ_API_KEY=<your_key> \
  -e GROQ_MODEL=llama-3.1-8b-instant \
  -e APP_DIR=/app \
  scraper python3 /app/input/analyse_groq.py
```

### Manual ingest (screenshots / LinkedIn posts)

Place files under `manual/<competitor_name>/images/` and/or `manual/<competitor_name>/texts/`, then:

```bash
docker compose exec scraper python manual_ingest.py
# Single competitor:
docker compose exec -e COMPETITOR=acme scraper python manual_ingest.py
```

### Stop / clean up

```bash
docker compose down
docker compose down --rmi all   # also removes the built image
```

---

## Environment Variables

### Analysis (`analyse_groq.py`)

| Variable | Required | Description |
|---|---|---|
| `GROQ_API_KEY` | Yes | Groq API key |
| `GROQ_MODEL` | No | Model name (default: `llama-3.1-8b-instant`) |
| `APP_DIR` | No | Base path for outputs (default: script directory). Set to `/app` in Docker. |

### Manual ingest

| Variable | Default | Description |
|---|---|---|
| `MANUAL_DIR` | `./manual` | Input root containing competitor folders |
| `OUTPUT_DIR` | `./sites` | Where output folders are written |
| `COMPETITOR` | _(all)_ | Process only one competitor folder |
| `OCR_ENGINE` | `easyocr` | `easyocr` or `pytesseract` |
| `SKIP_OCR` | _unset_ | Set to `1` to skip OCR |

---

## Output Files

All analysis outputs go to `output/`:

| File | Description |
|---|---|
| `<competitor>_services_scored_groq.xlsx` | Per-competitor workbook with scored services |
| `YYYYMMDD_initiative_long_list_groq.xlsx` | Consolidated workbook: long list + comparison matrix + hypothesis tracker |
| `YYYYMMDD_services_summary_groq.md` | Markdown summary grouped by competitor |
| `all_competitors_priority_groq.csv` | Flat CSV of all scored services |
| `processed_folders_groq.json` | State file — tracks which folders have been processed |
| `hypothesis_tracker_groq.json` | Raw hypothesis assessment results |

### Excel sheets in the long-list workbook

- **Long List** — all services ranked by priority score, with tier, plain-English summary, pricing signals, client wins, tech stack, data confidence
- **Comparison Matrix** — one row per competitor with average scores per dimension
- **Hypothesis Tracker** — evidence for/against each of the 5 strategic hypotheses, colour-coded by verdict

---

## Tips & Troubleshooting

**A competitor returns 0 services**
The extraction uses AI-keyword-weighted page sampling to ensure relevant pages are included even when they appear deep in the scraped content. If a competitor still returns 0 services, the scrape itself may be thin — check `sites/<folder>/pages_text.csv` for content. Consider re-scraping or using manual ingest.

**Groq 429 rate limit errors**
These are expected on the free tier and are handled automatically with retry backoff. The run will slow down but complete. Upgrade to a paid Groq tier or switch to a model with higher TPM limits to speed things up.

**JSON parse errors in LLM output**
The parser handles: markdown code fences, `<think>` blocks, pipe-separated enum values, and truncated arrays. If a competitor still fails, re-run with `--competitor <name>` to retry just that one.

**Scraper skipping sites or returning errors**
Some sites block automated requests. Check logs for `Skip` messages. Try increasing `REQUEST_DELAY` in `scraper.py`.

**EasyOCR slow on first local run**
EasyOCR downloads its model (~100MB) on first use. In Docker this is cached at build time.

**Running on Windows locally**
```powershell
$env:MAX_DEPTH="5"; python scraper.py
```

---

*AmaliTech Benchmarking Team — Internal Research Tooling*
