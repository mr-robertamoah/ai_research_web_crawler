# AmaliTech Competitor Intelligence Pipeline

A two-stage pipeline that (1) deep-crawls competitor websites and (2) uses an LLM to extract, score, and prioritise AI services — producing Excel and Markdown outputs to inform AmaliTech's AI service portfolio strategy.

A separate **Legacy Modernisation pipeline** (stages 3 & 4) crawls legacy/mainframe modernisation sources and produces a scored long list answering the pillar lead's four research questions.

---

## Table of Contents

- [Project Structure](#project-structure)
- [Stage 1 — Competitor Scraper](#stage-1--competitor-scraper)
- [Stage 2 — Competitor Analysis (Groq)](#stage-2--competitor-analysis-groq)
- [Stage 3 — Legacy Scraper](#stage-3--legacy-scraper)
- [Stage 4 — Legacy Analysis](#stage-4--legacy-analysis)
- [Running with Docker](#running-with-docker)
- [Environment Variables](#environment-variables)
- [Output Files](#output-files)
- [Tips & Troubleshooting](#tips--troubleshooting)

---

## Project Structure

```
project/
├── scraper.py                      # Stage 1 — competitor web crawler
├── analyse_groq.py                 # Stage 2 — competitor LLM analysis
├── legacy_scraper.py               # Stage 3 — legacy modernisation web crawler
├── legacy_analyse.py               # Stage 4 — legacy LLM analysis (Groq or Claude)
├── manual_ingest.py                # Optional — ingest screenshots / text files
├── requirements.txt
├── Dockerfile
├── docker-compose.yml
├── competitors.csv                 # Input for Stage 1
├── legacy_modernization_urls.txt   # Input for Stage 3
├── sites/                          # Stage 1 output
├── output/                         # Stage 2 output
├── legacy/                         # Stage 3 output
└── legacy_output/                  # Stage 4 output
```

---

## Stage 1 — Competitor Scraper

`scraper.py` deep-crawls each URL in `competitors.csv`, saves page text and images, and runs OCR. Results go into `sites/<domain>_<timestamp>/`.

### Input format

`competitors.csv` — auto-detects the URL column (`url`, `website`, `site`, `link`, `domain`, `competitor_url`):

```csv
competitor_name,website
Accenture,https://www.accenture.com
Nearshore,https://www.nearshore.com
```

Or a plain `.txt` file with one URL per line (lines starting with `#` are ignored).

### Run in Docker

```bash
docker compose exec scraper python scraper.py
# With options:
docker compose exec -e MAX_DEPTH=5 scraper python scraper.py
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `MAX_DEPTH` | `3` | Link depth to crawl from the starting URL |
| `OCR_ENGINE` | `easyocr` | `easyocr` or `pytesseract` |
| `INPUT_FILE` | _(auto-detect)_ | Force a specific input file |

### Output per competitor

```
sites/accenture-com_2025-03-16_14-30-00/
├── pages/           ← saved HTML files
├── images/          ← downloaded images
├── pages_text.csv   ← one row per page (url, page_title, depth, clean_text)
└── ocr_output.csv   ← one row per image
```

---

## Stage 2 — Competitor Analysis (Groq)

`analyse_groq.py` reads `sites/` folders and for each competitor:

1. **Extracts** AI services using `llama-3.1-8b-instant` via the Groq API
2. **Scores** each service across 7 dimensions against AmaliTech's strategic context
3. **Assesses** strategic hypotheses
4. **Writes** per-competitor Excel workbooks + consolidated long-list + Markdown summary

### Run (Docker)

```bash
docker compose exec \
  -e GROQ_API_KEY=<your_key> \
  -e GROQ_MODEL=llama-3.1-8b-instant \
  -e APP_DIR=/app \
  scraper python3 /app/input/analyse_groq.py
```

| Flag | Description |
|---|---|
| `--rerun-all` | Clear state and reprocess all competitors |
| `--competitor <name>` | Process only one competitor (fuzzy match) |
| `--max-pages <n>` | Limit pages read per competitor (default: 50) |
| `--dry-run` | Show what would be processed without calling the API |

---

## Stage 3 — Legacy Scraper

`legacy_scraper.py` crawls URLs in `legacy_modernization_urls.txt` — covering AI-assisted COBOL/mainframe modernisation providers, tools, platforms, and research. Output goes to `legacy/`.

Key features:
- **Checkpoint system** — saves progress after every URL; interrupted runs resume automatically
- **`MAX_SITE_MINUTES`** — caps time spent per site then moves to the next; checkpoint preserves progress for later resume
- **Incremental writes** — flushes CSV after every page (crash-safe)
- **Relevance scoring** — pages scored by keyword hits; low-relevance pages skipped

### Input format

`legacy_modernization_urls.txt` — one URL per line, `#` lines ignored. Organised by category (service providers, AI tools, research, Java migration).

To scrape only a subset (e.g. corrections), create a separate `.txt` file and pass it via `INPUT_FILE`:

```bash
docker compose exec -e INPUT_FILE=corrections_urls.txt \
  -e MAX_SITE_MINUTES=60 -e LEGACY_DIR=/app/legacy \
  scraper python3 /app/input/legacy_scraper.py
```

### Run (Docker)

```bash
docker compose exec \
  -e MAX_SITE_MINUTES=60 \
  -e MAX_DEPTH=2 \
  -e MIN_RELEVANCE=1 \
  -e LEGACY_DIR=/app/legacy \
  scraper python3 /app/input/legacy_scraper.py
```

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `MAX_DEPTH` | `2` | Link depth to crawl |
| `MIN_RELEVANCE` | `2` | Min keyword hits to save a page |
| `MAX_SITE_MINUTES` | `0` | Max minutes per site (0 = unlimited) |
| `LEGACY_DIR` | `/app/legacy` | Output directory |
| `INPUT_FILE` | _(auto-detect)_ | Override input URL file |
| `FRESH` | `0` | Set to `1` to ignore checkpoints and re-crawl |
| `OCR` | `0` | Set to `1` to enable OCR on images |
| `OCR_ENGINE` | `easyocr` | `easyocr` or `pytesseract` |

### Output per site

```
legacy/www-ibm-com_2026-03-24_08-27-53/
├── pages/           ← saved HTML files
├── pages_text.csv   ← one row per page (url, title, depth, relevance_score, keyword_hits, clean_text)
└── checkpoint.json  ← visited URLs — enables resume on restart
```

---

## Stage 4 — Legacy Analysis

`legacy_analyse.py` reads `legacy/` folders and for each source:

1. **Extracts** legacy modernisation services/tools/products
2. **Scores** each across 7 dimensions (market impact, effort, scalability, revenue potential, market credibility, talent availability, strategic fit)
3. **Generates** a research brief answering the pillar lead's 4 questions:
   - Who is doing AI-assisted legacy/mainframe/COBOL modernisation?
   - Does it work — what is the maturity level?
   - What state-of-the-art tools and approaches exist (including academic)?
   - Java 8/11 → 17/21 migration using AI as a supportive hand
4. **Writes** per-source Excel workbooks + consolidated long-list + Markdown research brief

Supports **Groq** (default) or **Claude** as the AI backend.

### Run (Docker)

```bash
# Groq (default)
docker compose exec \
  -e GROQ_API_KEY=<your_key> \
  -e GROQ_MODEL=llama-3.1-8b-instant \
  -e APP_DIR=/app \
  -e LEGACY_DIR=/app/legacy \
  -e LEGACY_OUTPUT_DIR=/app/legacy_output \
  scraper python3 /app/input/legacy_analyse.py --max-pages 5

# Claude
docker compose exec \
  -e AI_BACKEND=claude \
  -e ANTHROPIC_API_KEY=<your_key> \
  -e APP_DIR=/app \
  -e LEGACY_DIR=/app/legacy \
  -e LEGACY_OUTPUT_DIR=/app/legacy_output \
  scraper python3 /app/input/legacy_analyse.py --max-pages 5
```

| Flag | Description |
|---|---|
| `--rerun-all` | Clear state and reprocess all sources |
| `--source <name>` | Process only one source (fuzzy match) |
| `--max-pages <n>` | Limit pages read per source (default: 40; use 5 on Groq free tier) |
| `--dry-run` | Show pending sources without calling the API |

### Environment variables

| Variable | Default | Description |
|---|---|---|
| `AI_BACKEND` | `groq` | `groq` or `claude` |
| `GROQ_API_KEY` | — | Required if `AI_BACKEND=groq` |
| `GROQ_MODEL` | `qwen/qwen3-32b` | Groq model name |
| `ANTHROPIC_API_KEY` | — | Required if `AI_BACKEND=claude` |
| `LEGACY_DIR` | `/app/legacy` | Input: legacy scraper output |
| `LEGACY_OUTPUT_DIR` | `/app/legacy_output` | Output directory |
| `APP_DIR` | script dir | Base path (set to `/app` in Docker) |

> **Groq free tier note**: Use `--max-pages 5` to stay within the 6,000 TPM limit. The script retries automatically on 429 errors.

---

## Running with Docker

### First-time setup

```bash
docker compose build   # ~3-5 min (downloads EasyOCR model)
docker compose up -d
```

### Full legacy pipeline

```bash
# Step 1 — scrape
docker compose exec \
  -e MAX_SITE_MINUTES=60 -e MAX_DEPTH=2 -e MIN_RELEVANCE=1 \
  -e LEGACY_DIR=/app/legacy \
  scraper python3 /app/input/legacy_scraper.py

# Step 2 — analyse
docker compose exec \
  -e GROQ_API_KEY=<your_key> -e GROQ_MODEL=llama-3.1-8b-instant \
  -e APP_DIR=/app -e LEGACY_DIR=/app/legacy -e LEGACY_OUTPUT_DIR=/app/legacy_output \
  scraper python3 /app/input/legacy_analyse.py --max-pages 5
```

### Manual ingest (screenshots / LinkedIn posts)

```bash
docker compose exec scraper python manual_ingest.py
docker compose exec -e COMPETITOR=acme scraper python manual_ingest.py
```

### Stop / clean up

```bash
docker compose down
docker compose down --rmi all
```

---

## Output Files

### Competitor analysis (`output/`)

| File | Description |
|---|---|
| `<competitor>_services_scored_groq.xlsx` | Per-competitor scored workbook |
| `YYYYMMDD_initiative_long_list_groq.xlsx` | Consolidated long list + comparison matrix + hypothesis tracker |
| `YYYYMMDD_services_summary_groq.md` | Markdown summary grouped by competitor |
| `all_competitors_priority_groq.csv` | Flat CSV of all scored services |
| `processed_folders_groq.json` | State file |

### Legacy analysis (`legacy_output/`)

| File | Description |
|---|---|
| `<source>_legacy_scored.xlsx` | Per-source scored workbook |
| `YYYYMMDD_legacy_long_list.xlsx` | Consolidated long list ranked by priority score |
| `YYYYMMDD_legacy_research_brief.md` | Research brief answering the 4 pillar questions |
| `legacy_all_priority.csv` | Flat CSV of all scored legacy services/tools |
| `legacy_processed_folders.json` | State file — enables incremental runs |

---

## Tips & Troubleshooting

**A source returns 0 services**
Check `legacy/<folder>/pages_text.csv` — the scrape may be thin (site blocked bots or content is JS-rendered). Re-scrape with `FRESH=1` or add better URLs to the input file.

**Groq 429 rate limit errors**
Expected on the free tier — handled automatically with retry backoff. Use `--max-pages 5` to reduce token usage per source.

**JSON parse errors in LLM output**
The parser handles markdown fences, `<think>` blocks, and truncated arrays. Re-run a single source with `--source <name>` to retry.

**Output files owned by root (Docker)**
Files written by the container are owned by root. Run `sudo chown -R $USER:$USER output/ legacy_output/` to regain edit access.

**Resuming an interrupted legacy scrape**
Just re-run the same command — the checkpoint system skips already-visited URLs automatically. Use `FRESH=1` only if you want to start the site from scratch.

**Running on Windows locally**
```powershell
$env:MAX_DEPTH="5"; python legacy_scraper.py
```

---

*AmaliTech Benchmarking Team — Internal Research Tooling*
