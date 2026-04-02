# AmaliTech Intelligence Pipeline

A unified system for scraping websites and using LLMs to extract, score, and prioritise intelligence — producing Excel, CSV, and Markdown outputs across four pipelines.

---

## Table of Contents

- [Architecture](#architecture)
- [Pipelines](#pipelines)
- [Running with Docker](#running-with-docker)
- [Scraper — `scraper_new.py`](#scraper--scraper_newpy)
- [Analyser — `analyse_new.py`](#analyser--analyse_newpy)
- [Environment Variables](#environment-variables)
- [Output Files](#output-files)
- [Adding a New Pipeline](#adding-a-new-pipeline)
- [Tips & Troubleshooting](#tips--troubleshooting)

---

## Architecture

```
project/
├── scraper_new.py              # Unified scraper — SCRAPE_MODE selects pipeline
├── analyse_new.py              # Unified analyser — ANALYSE_MODE selects pipeline
├── lib/
│   ├── core.py                 # AI backend (Groq/Claude), tier config, state helpers
│   ├── excel.py                # Shared openpyxl helpers
│   ├── scraper_core.py         # BFS crawl engine, checkpoint, OCR, PDF extraction
│   ├── keywords/
│   │   ├── competitor.py       # Keyword taxonomy for competitor scraping
│   │   ├── legacy.py           # Keyword taxonomy for legacy modernisation
│   │   ├── ai_consulting.py    # Keyword taxonomy for AI consulting
│   │   └── client_intel.py     # Keyword taxonomy for client intelligence
│   └── pipelines/
│       ├── competitor.py       # Extraction prompt, scoring, hypothesis tracking
│       ├── legacy.py           # Extraction prompt, 7-dim scoring, research brief
│       ├── ai_consulting.py    # Extraction prompt, pricing inference, service grouping
│       └── client_intel.py     # Extraction prompt, signal typing, spend profiles
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── competitors.csv             # Input: competitor pipeline
├── legacy_modernization_urls.txt  # Input: legacy pipeline
├── ai_consulting_urls.txt      # Input: AI consulting pipeline
├── client_intel_urls.txt       # Input: client intelligence pipeline
├── sites/                      # Competitor scraper output
├── output/                     # Competitor analyser output
├── legacy/                     # Legacy scraper output
├── legacy_output/              # Legacy analyser output
├── ai_sites/                   # AI consulting scraper output
├── ai_output/                  # AI consulting analyser output
├── client_sites/               # Client intel scraper output
└── client_output/              # Client intel analyser output
```

---

## Pipelines

| `SCRAPE_MODE` / `ANALYSE_MODE` | Purpose | Input file | Sites dir | Output dir |
|---|---|---|---|---|
| `competitor` | Scrape and score competitor AI services | `competitors.csv` | `sites/` | `output/` |
| `legacy` | Research AI-assisted legacy modernisation | `legacy_modernization_urls.txt` | `legacy/` | `legacy_output/` |
| `ai_consulting` | Map AI consulting market (services, pricing, formats) | `ai_consulting_urls.txt` | `ai_sites/` | `ai_output/` |
| `client_intel` | Extract AI spend signals from client/prospect websites | `client_intel_urls.txt` | `client_sites/` | `client_output/` |

---

## Running with Docker

### First-time setup

```bash
docker compose build   # ~3-5 min
docker compose up -d
```

### Scrape then analyse — any pipeline

```bash
# 1. Scrape
docker compose exec \
  -e SCRAPE_MODE=<mode> \
  -e MAX_DEPTH=2 -e MIN_RELEVANCE=1 -e MAX_SITE_MINUTES=20 \
  scraper python3 /app/input/scraper_new.py

# 2. Analyse
docker compose exec \
  -e ANALYSE_MODE=<mode> \
  -e AI_BACKEND=claude \
  -e ANTHROPIC_API_KEY=<key> \
  -e CLAUDE_MODEL=claude-haiku-4-5-20251001 \
  -e APP_DIR=/app \
  scraper python3 /app/input/analyse_new.py --max-pages 5
```

Replace `<mode>` with `competitor`, `legacy`, `ai_consulting`, or `client_intel`.

---

## Scraper — `scraper_new.py`

### How it works

- Reads URLs from the input file for the selected mode
- BFS crawls each site up to `MAX_DEPTH`, scoring pages by keyword hits
- Saves pages that meet `MIN_RELEVANCE` to `pages_text.csv` (incremental — crash-safe)
- Writes `checkpoint.json` per site — interrupted runs resume automatically
- `MAX_SITE_MINUTES` caps time per site then moves to the next
- `client_intel` mode additionally: seeds priority paths (`/investors`, `/news`, `/careers` etc.) at depth 0, and extracts text from PDFs (first `PDF_MAX_PAGES` pages)

### Input file formats

**`competitors.csv`** — auto-detects URL column (`url`, `website`, `site`, `link`):
```csv
competitor_name,website
Accenture,https://www.accenture.com
```

**`legacy_modernization_urls.txt` / `ai_consulting_urls.txt`** — one URL per line, `#` lines ignored:
```
https://www.ibm.com/consulting
# AI tools
https://www.openrewrite.org
```

**`client_intel_urls.txt`** — pipe-separated with client type and name:
```
existing|Deutsche Bank|https://www.db.com
potential|BMW Group|https://www.bmwgroup.com
```

### Scraper environment variables

| Variable | Default | Description |
|---|---|---|
| `SCRAPE_MODE` | `competitor` | `competitor` \| `legacy` \| `ai_consulting` \| `client_intel` |
| `MAX_DEPTH` | `2` | Link depth to crawl |
| `MIN_RELEVANCE` | `2` | Min keyword hits to save a page |
| `MAX_SITE_MINUTES` | `0` | Max minutes per site (0 = unlimited) |
| `FRESH` | `0` | Set to `1` to ignore checkpoints and re-crawl |
| `OCR` | `0` | Set to `1` to enable OCR on images |
| `OCR_ENGINE` | `easyocr` | `easyocr` or `pytesseract` |
| `INPUT_FILE` | _(auto-detect)_ | Override input file name |
| `PDF_MAX_PAGES` | `10` | Max pages to extract per PDF (client_intel only) |

### Output per site

```
<sites_dir>/<host>_<timestamp>/
├── pages/            ← saved HTML files
├── pages_text.csv    ← url, title, depth, relevance_score, keyword_hits, keyword_groups, clean_text, source_type
├── pdf_text.csv      ← same schema + source_type=pdf  (client_intel only)
├── client_meta.json  ← {client_type, client_name, url}  (client_intel only)
└── checkpoint.json   ← visited URLs — enables resume on restart
```

---

## Analyser — `analyse_new.py`

### How it works

- Reads scraped output from the sites directory for the selected mode
- Sorts pages by `relevance_score` descending, takes top `--max-pages`
- Calls the AI backend to extract structured data per source
- Scores services (competitor and legacy modes) or infers pricing (ai_consulting) or classifies signals (client_intel)
- Writes per-source Excel workbooks + consolidated outputs
- State file tracks processed sources — re-runs only process new sources

### CLI flags

| Flag | Description |
|---|---|
| `--rerun-all` | Clear state and reprocess everything |
| `--source <name>` | Process only one source (fuzzy match on folder name) |
| `--max-pages <n>` | Limit pages per source (default: 5 on Groq free, 20 on paid/Claude) |
| `--dry-run` | Show pending sources without calling the API |
| `--weights <w>` | Comma-separated dimension weights (competitor/legacy only) |

### Analyser environment variables

| Variable | Default | Description |
|---|---|---|
| `ANALYSE_MODE` | `competitor` | `competitor` \| `legacy` \| `ai_consulting` \| `client_intel` |
| `AI_BACKEND` | `groq` | `groq` \| `claude` |
| `GROQ_API_KEY` | — | Required if `AI_BACKEND=groq` |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Any Groq model ID |
| `GROQ_TIER` | `free` | `free` (6k TPM) \| `paid` (4x limits); ignored when `AI_BACKEND=claude` |
| `ANTHROPIC_API_KEY` | — | Required if `AI_BACKEND=claude` |
| `CLAUDE_MODEL` | `claude-haiku-4-5-20251001` | Any Anthropic model ID |
| `APP_DIR` | script dir | Base path — set to `/app` in Docker |

> **Claude auto-uses paid-tier limits.** When `AI_BACKEND=claude`, `GROQ_TIER` is ignored and 20 pages / 24k content window / 8192 output tokens apply automatically.

> **Output files never overwrite across backends.** All outputs are suffixed with the backend name — e.g. `*_groq.xlsx` and `*_claude.xlsx` are written separately.

---

## Output Files

### Competitor (`output/`)

| File | Description |
|---|---|
| `<competitor>_competitor_<backend>.xlsx` | Per-competitor scored workbook with 7-dim scores |
| `YYYYMMDD_competitor_long_list_<backend>.xlsx` | All competitors + Comparison Matrix + Hypothesis Tracker sheets |
| `YYYYMMDD_competitor_brief_<backend>.md` | Q&A research brief (4 questions) |
| `YYYYMMDD_competitor_market_summary_<backend>.md` | Market summary by category, clients, industries, pricing signals, hypothesis verdicts |
| `YYYYMMDD_competitor_executive_brief_<backend>.md` | C-suite brief |
| `competitor_all_priority_<backend>.csv` | Flat CSV of all scored services |
| `hypothesis_tracker_<backend>.json` | Per-competitor hypothesis verdicts |

### Legacy (`legacy_output/`)

| File | Description |
|---|---|
| `<source>_legacy_<backend>.xlsx` | Per-source scored workbook |
| `YYYYMMDD_legacy_long_list_<backend>.xlsx` | Consolidated long list |
| `YYYYMMDD_legacy_brief_<backend>.md` | Research brief answering 4 pillar questions |
| `legacy_all_priority_<backend>.csv` | Flat CSV |

### AI Consulting (`ai_output/`)

| File | Description |
|---|---|
| `<source>_ai_consulting_<backend>.xlsx` | Per-source workbook with pricing inference |
| `YYYYMMDD_ai_consulting_long_list_<backend>.xlsx` | Long list + By Service Type comparison sheet |
| `YYYYMMDD_ai_consulting_brief_<backend>.md` | Q&A research brief |
| `YYYYMMDD_ai_market_summary_<backend>.md` | Delivery formats, pricing models, target audiences, industries, named clients |
| `YYYYMMDD_ai_executive_brief_<backend>.md` | C-suite brief |
| `ai_consulting_all_priority_<backend>.csv` | Flat CSV |

### Client Intelligence (`client_output/`)

| File | Description |
|---|---|
| `<client>_client_intel_<backend>.xlsx` | Per-client signal workbook |
| `YYYYMMDD_client_intel_long_list_<backend>.xlsx` | All signals per client |
| `YYYYMMDD_client_intel_brief_<backend>.md` | Q&A research brief |
| `YYYYMMDD_client_market_summary_<backend>.md` | Per-client AI spend profiles, vendor breakdown, budget signals |
| `YYYYMMDD_client_executive_brief_<backend>.md` | C-suite brief |
| `YYYYMMDD_potential_clients_<backend>.md` | Potential clients matched to existing client profiles |
| `client_intel_all_priority_<backend>.csv` | Flat CSV |

---

## Adding a New Pipeline

Adding a new pipeline requires four small files and two one-line additions.

### 1. Keyword taxonomy — `lib/keywords/<name>.py`

```python
KEYWORD_GROUPS = {
    "group_one": ["keyword a", "keyword b"],
    "group_two": ["keyword c", "keyword d"],
}
```

### 2. Pipeline logic — `lib/pipelines/<name>.py`

Must implement:

```python
DIMENSIONS = [...]          # list of scoring dimensions (or [] if no scoring)

def extract_services(name: str, content: str) -> list[dict]:
    ...  # call call_ai(), return list of dicts

def build_rows(source: str, services: list[dict]) -> list[dict]:
    ...  # return list of row dicts with at least: source, priority_score, priority_tier

# Optional — if present, analyse_new.py will call them automatically:
def generate_brief(all_content: str) -> dict: ...
def write_brief_md(brief, all_rows, path): ...
def write_market_summary_md(all_rows, path): ...
def write_executive_brief_md(brief, all_rows, path): ...
```

### 3. Register in `scraper_new.py`

```python
_MODES = {
    ...
    "<name>": ("lib.keywords.<name>", "<name>_sites", "<name>_urls.txt"),
}
```

And add the env var key:
```python
SITES_DIR = Path(os.getenv(
    {..., "<name>": "<NAME>_SITES_DIR"}[SCRAPE_MODE],
    ...
))
```

### 4. Register in `analyse_new.py`

```python
_MODES = {
    ...
    "<name>": ("<NAME>_SITES_DIR", "<name>_sites", "<NAME>_OUTPUT_DIR", "<name>_output"),
}

_pipeline = importlib.import_module({
    ...
    "<name>": "lib.pipelines.<name>",
}[ANALYSE_MODE])
```

### 5. Add volume mounts to `docker-compose.yml`

```yaml
- ./<name>_sites:/app/<name>_sites
- ./<name>_output:/app/<name>_output
```

### 6. Add to `.gitignore`

```
<name>_sites/*
<name>_output/*
```

That's it. The scraper, analyser, state management, incremental CSV flush, and output generation all work automatically.

---

## Tips & Troubleshooting

**Resuming an interrupted scrape**
Re-run the same command — the checkpoint system skips already-visited URLs. Use `FRESH=1` only to start from scratch.

**Groq 429 rate limit errors**
Expected on the free tier — handled automatically with retry backoff. Use `--max-pages 5` and `GROQ_TIER=free`.

**JSON parse errors from LLM**
The parser handles markdown fences, `<think>` blocks, and truncated arrays. Re-run a single source with `--source <name> --rerun-all`.

**Output files owned by root (Docker)**
```bash
sudo chown -R $USER:$USER output/ legacy_output/ ai_output/ client_output/
```

**A site returns 0 pages**
The site likely blocks bots or is JS-rendered. Check `pages_text.csv` — if empty, the content isn't accessible via static crawl.

**PDF extraction not working**
Ensure `pdfplumber` is installed in the container:
```bash
docker compose exec scraper pip install pdfplumber
```

---

*AmaliTech Benchmarking Team — Internal Research Tooling*
