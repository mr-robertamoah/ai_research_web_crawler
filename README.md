# AmaliTech Intelligence Platform

A unified system for scraping websites and using LLMs to extract, score, and prioritise intelligence — producing Excel, CSV, Markdown outputs, and auto-publishing to Confluence across six pipelines.

---

## Table of Contents

- [Architecture](#architecture)
- [Pipelines](#pipelines)
- [Running with Docker](#running-with-docker)
- [Scraper — `scraper_new.py`](#scraper--scraper_newpy)
- [Analyser — `analyse_new.py`](#analyser--analyse_newpy)
- [Confluence Publisher — `confluence_publish.py`](#confluence-publisher--confluence_publishpy)
- [Strategic Intelligence — `generate_executive_summary.py`](#strategic-intelligence--generate_executive_summarypy)
- [News Monitoring — Cron Setup](#news-monitoring--cron-setup)
- [Environment Variables](#environment-variables)
- [Output Files](#output-files)
- [Adding a New Pipeline](#adding-a-new-pipeline)
- [Tips & Troubleshooting](#tips--troubleshooting)

---

## Architecture

```
project/
├── scraper_new.py                    # Unified scraper — SCRAPE_MODE selects pipeline
├── analyse_new.py                    # Unified analyser — ANALYSE_MODE selects pipeline
├── confluence_publish.py             # Publishes all pipeline outputs to Confluence
├── generate_executive_summary.py     # Generates executive summary + hypothesis deep-dives
├── run_news.sh                       # Cron wrapper for news monitoring
├── lib/
│   ├── core.py                       # AI backend (Groq/Claude), tier config, state helpers
│   ├── excel.py                      # Shared openpyxl helpers
│   ├── scraper_core.py               # BFS crawl engine, checkpoint, OCR, PDF extraction
│   ├── keywords/
│   │   ├── competitor.py
│   │   ├── legacy.py
│   │   ├── ai_consulting.py
│   │   ├── client_intel.py
│   │   ├── competitor_spend.py
│   │   └── news_monitoring.py
│   └── pipelines/
│       ├── competitor.py
│       ├── legacy.py
│       ├── ai_consulting.py
│       ├── client_intel.py
│       ├── competitor_spend.py
│       └── news_monitoring.py
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── urls.txt                          # Input: competitor pipeline
├── legacy_modernization_urls.txt     # Input: legacy pipeline
├── ai_consulting_urls.txt            # Input: AI consulting pipeline
├── client_intel_urls.txt             # Input: client intelligence pipeline
├── competitor_spend_urls.txt         # Input: competitor spend pipeline
├── news_monitoring_urls.txt          # Input: news monitoring pipeline
├── sites/                            # Competitor scraper output
├── output/                           # Competitor analyser output
├── legacy/                           # Legacy scraper output
├── legacy_output/                    # Legacy analyser output
├── ai_sites/                         # AI consulting scraper output
├── ai_output/                        # AI consulting analyser output
├── client_sites/                     # Client intel scraper output
├── client_output/                    # Client intel analyser output
├── comp_spend_sites/                 # Competitor spend scraper output
├── comp_spend_output/                # Competitor spend analyser output
├── news_sites/                       # News monitoring scraper output
└── news_output/                      # News monitoring analyser output
```

---

## Pipelines

| `SCRAPE_MODE` / `ANALYSE_MODE` | Purpose | Input file | Sites dir | Output dir |
|---|---|---|---|---|
| `competitor` | Scrape and score competitor AI services | `urls.txt` | `sites/` | `output/` |
| `legacy` | Research AI-assisted legacy modernisation | `legacy_modernization_urls.txt` | `legacy/` | `legacy_output/` |
| `ai_consulting` | Map AI consulting market (services, pricing, formats) | `ai_consulting_urls.txt` | `ai_sites/` | `ai_output/` |
| `client_intel` | Extract AI spend signals from client/prospect websites | `client_intel_urls.txt` | `client_sites/` | `client_output/` |
| `competitor_spend` | Where competitors are investing in AI (vendors, R&D, pricing) | `competitor_spend_urls.txt` | `comp_spend_sites/` | `comp_spend_output/` |
| `news_monitoring` | Monitor tech/AI news blogs for alerts and trends | `news_monitoring_urls.txt` | `news_sites/` | `news_output/` |

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

# 2. Analyse + publish to Confluence
docker compose exec \
  -e ANALYSE_MODE=<mode> \
  -e AI_BACKEND=claude \
  -e ANTHROPIC_API_KEY=<key> \
  -e CLAUDE_MODEL=claude-haiku-4-5-20251001 \
  -e APP_DIR=/app \
  scraper python3 /app/input/analyse_new.py --max-pages 20 --publish
```

Replace `<mode>` with any pipeline name from the table above.

### Generate strategic intelligence (executive summary + hypothesis deep-dives)

```bash
docker compose exec \
  -e ANTHROPIC_API_KEY=<key> \
  -e CLAUDE_MODEL=claude-sonnet-4-5 \
  -e APP_DIR=/app \
  scraper python3 /app/input/generate_executive_summary.py
```

This produces 6 documents (1 executive summary + 5 hypothesis analyses) and publishes them to Confluence under `Research → Strategic Intelligence`.

---

## Scraper — `scraper_new.py`

### How it works

- Reads URLs from the input file for the selected mode
- BFS crawls each site up to `MAX_DEPTH`, scoring pages by keyword hits
- Saves pages that meet `MIN_RELEVANCE` to `pages_text.csv` (incremental — crash-safe)
- Writes `checkpoint.json` per site — interrupted runs resume automatically
- `MAX_SITE_MINUTES` caps time per site then moves to the next
- `client_intel` mode additionally seeds priority paths (`/investors`, `/news`, `/careers` etc.) and extracts PDFs

### Input file formats

**`urls.txt` / `competitor_spend_urls.txt`** — one URL per line, `#` lines ignored:
```
https://www.accenture.com
# boutique firms
https://www.fractal.ai
```

**`legacy_modernization_urls.txt` / `ai_consulting_urls.txt` / `news_monitoring_urls.txt`** — same format.

**`client_intel_urls.txt`** — pipe-separated with client type and name:
```
existing|Deutsche Bank|https://www.db.com
potential|BMW Group|https://www.bmwgroup.com
```

### Scraper environment variables

| Variable | Default | Description |
|---|---|---|
| `SCRAPE_MODE` | `competitor` | Pipeline to run |
| `MAX_DEPTH` | `2` | Link depth to crawl |
| `MIN_RELEVANCE` | `2` | Min keyword hits to save a page |
| `MAX_SITE_MINUTES` | `0` | Max minutes per site (0 = unlimited) |
| `FRESH` | `0` | Set to `1` to ignore checkpoints and re-crawl |
| `OCR` | `0` | Set to `1` to enable OCR on images |
| `INPUT_FILE` | _(auto-detect)_ | Override input file name |
| `PDF_MAX_PAGES` | `10` | Max pages to extract per PDF (client_intel only) |

---

## Analyser — `analyse_new.py`

### How it works

- Reads scraped output from the sites directory for the selected mode
- Sorts pages by `relevance_score` descending, takes top `--max-pages`
- Calls the AI backend to extract structured data per source
- Scores services, infers pricing, or classifies signals depending on mode
- Writes per-source Excel workbooks + consolidated outputs
- State file tracks processed sources — re-runs only process new sources
- `--publish` flag auto-publishes to Confluence after analysis

### CLI flags

| Flag | Description |
|---|---|
| `--rerun-all` | Clear state and reprocess everything |
| `--source <name>` | Process only one source |
| `--max-pages <n>` | Limit pages per source (default: 5 on Groq free, 20 on Claude) |
| `--dry-run` | Show pending sources without calling the API |
| `--publish` | Publish outputs to Confluence after analysis |

### Analyser environment variables

| Variable | Default | Description |
|---|---|---|
| `ANALYSE_MODE` | `competitor` | Pipeline to run |
| `AI_BACKEND` | `groq` | `groq` \| `claude` |
| `GROQ_API_KEY` | — | Required if `AI_BACKEND=groq` |
| `GROQ_MODEL` | `llama-3.1-8b-instant` | Any Groq model ID |
| `GROQ_TIER` | `free` | `free` (6k TPM) \| `paid` (4x limits) |
| `ANTHROPIC_API_KEY` | — | Required if `AI_BACKEND=claude` |
| `CLAUDE_MODEL` | `claude-haiku-4-5-20251001` | Any Anthropic model ID |
| `APP_DIR` | script dir | Base path — set to `/app` in Docker |

> **Claude auto-uses paid-tier limits.** 20 pages / 24k content window / 8192 output tokens.

---

## Confluence Publisher — `confluence_publish.py`

Publishes all pipeline outputs to the Confluence Research folder. Called automatically via `--publish` on the analyser, or run standalone.

```bash
# Publish all pipelines
docker compose exec scraper python3 /app/input/confluence_publish.py

# Publish one pipeline
docker compose exec scraper python3 /app/input/confluence_publish.py --mode research

# Publish competitor spend only
docker compose exec scraper python3 /app/input/confluence_publish.py --mode competitor_spend

# Dry run
docker compose exec scraper python3 /app/input/confluence_publish.py --dry-run
```

### Confluence environment variables (in `.env`)

```
CONFLUENCE_BASE_URL=https://yourorg.atlassian.net/wiki
CONFLUENCE_EMAIL=your@email.com
CONFLUENCE_API_TOKEN=your_token
CONFLUENCE_SPACE_KEY=AH
CONFLUENCE_RESEARCH_PAGE_ID=<numeric page id>
```

### Confluence structure

```
Research/
├── Competitors/              ← per-competitor pages + hypothesis tracker + market summary
├── Competitor AI Spend/      ← per-competitor spend pages + vendor usage + pricing signals
├── Clients & Prospects/      ← per-client AI signal pages + vendor usage + potential matches
├── Market & Trends/          ← AI consulting overview + pricing + legacy brief
└── Strategic Intelligence/   ← Executive Summary + H1–H5 hypothesis deep-dives
```

---

## Strategic Intelligence — `generate_executive_summary.py`

Generates a cross-pipeline executive summary and five hypothesis deep-dives using Claude Sonnet. Draws from all six pipelines including competitor spend data.

```bash
docker compose exec \
  -e ANTHROPIC_API_KEY=<key> \
  -e CLAUDE_MODEL=claude-sonnet-4-5 \
  -e APP_DIR=/app \
  scraper python3 /app/input/generate_executive_summary.py
```

**Outputs (saved locally + published to Confluence):**
- `YYYYMMDD_executive_summary.md` — full cross-pipeline synthesis with hypothesis verdicts as centrepiece
- `YYYYMMDD_hypothesis_1_*.md` through `hypothesis_5_*.md` — per-hypothesis deep-dives with per-competitor breakdown

**Hypothesis verdicts** are read from the H-page MD files (ground truth) rather than recomputed, ensuring the executive summary always matches the individual hypothesis pages.

---

## News Monitoring — Cron Setup

The news monitoring pipeline scrapes tech/AI blogs twice daily, extracts articles, scores them for priority, fires Slack alerts for high-priority items, and deduplicates across all runs.

### Configure your news sources

Edit `news_monitoring_urls.txt` — one URL per line:

```
# AI & Machine Learning
https://techcrunch.com/category/artificial-intelligence/
https://huggingface.co/blog

# Cloud providers
https://aws.amazon.com/blogs/aws/
https://azure.microsoft.com/en-us/blog/
https://cloud.google.com/blog/

# DevOps / Platform Engineering
https://www.cncf.io/blog/
https://kubernetes.io/blog/

# Security
https://krebsonsecurity.com
https://www.darkreading.com

# Add your own sources here
```

### Configure alert keywords

Edit `lib/keywords/news_monitoring.py` — add terms to `ALERT_KEYWORDS` to trigger Slack alerts:

```python
ALERT_KEYWORDS = [
    "critical", "cve", "vulnerability", "zero day",
    "major release", "breaking change",
    # Add your own alert triggers here
]
```

### Set up Slack alerts (optional)

Add to `.env`:
```
SLACK_WEBHOOK_URL=https://hooks.slack.com/services/T.../B.../XXX
```

If not set, alerts print to console only.

### Set up the cron job

```bash
crontab -e
```

Add (runs at 08:00 and 18:00 UTC daily):
```
0 8,18 * * * /path/to/ai_research_web_crawler/run_news.sh >> /path/to/ai_research_web_crawler/news_cron.log 2>&1
```

To run at different times — e.g. 07:00 and 19:00 UTC:
```
0 7,19 * * * /path/to/ai_research_web_crawler/run_news.sh >> /path/to/ai_research_web_crawler/news_cron.log 2>&1
```

To run three times daily at 08:00, 13:00, 18:00:
```
0 8,13,18 * * * /path/to/ai_research_web_crawler/run_news.sh >> /path/to/ai_research_web_crawler/news_cron.log 2>&1
```

Verify the cron is registered:
```bash
crontab -l
```

### Run manually

```bash
./run_news.sh
```

Or step by step:
```bash
# Scrape
docker compose exec -e SCRAPE_MODE=news_monitoring -e MAX_DEPTH=1 \
  -e MIN_RELEVANCE=1 -e FRESH=1 -e NEWS_SITES_DIR=/app/news_sites \
  scraper python3 /app/input/scraper_new.py

# Analyse
docker compose exec \
  -e ANALYSE_MODE=news_monitoring \
  -e AI_BACKEND=groq \
  -e GROQ_API_KEY=<key> \
  -e APP_DIR=/app -e NEWS_SITES_DIR=/app/news_sites -e NEWS_OUTPUT_DIR=/app/news_output \
  scraper python3 /app/input/analyse_new.py --max-pages 5 --rerun-all
```

### News output files (`news_output/`)

| File | Description |
|---|---|
| `news_YYYYMMDD.xlsx` | Daily Excel — all articles with priority, vendors, categories |
| `news_run_YYYYMMDD_HHMM_*.md` | Per-run markdown summary — numbered list with keywords, priority, summary, link |
| `news_seen_urls.json` | Deduplication store — all URLs ever processed |

---

## Output Files

### Competitor (`output/`)
| File | Description |
|---|---|
| `<competitor>_competitor_<backend>.xlsx` | Per-competitor scored workbook |
| `YYYYMMDD_competitor_long_list_<backend>.xlsx` | All competitors + Comparison Matrix + Hypothesis Tracker |
| `YYYYMMDD_competitor_brief_<backend>.md` | Q&A research brief |
| `YYYYMMDD_competitor_market_summary_<backend>.md` | Market summary with clients, industries, pricing signals, hypothesis verdicts |
| `YYYYMMDD_competitor_executive_brief_<backend>.md` | C-suite brief |
| `competitor_all_priority_<backend>.csv` | Flat CSV |

### Competitor Spend (`comp_spend_output/`)
| File | Description |
|---|---|
| `<competitor>_competitor_spend_<backend>.xlsx` | Per-competitor spend workbook |
| `YYYYMMDD_competitor_spend_long_list_<backend>.xlsx` | All spend signals |
| `YYYYMMDD_comp_spend_market_summary_<backend>.md` | Vendor usage, pricing signals, per-competitor spend profiles |
| `YYYYMMDD_comp_spend_executive_brief_<backend>.md` | C-suite brief on competitor AI spending |
| `competitor_spend_all_priority_<backend>.csv` | Flat CSV |

### AI Consulting (`ai_output/`)
| File | Description |
|---|---|
| `YYYYMMDD_ai_consulting_long_list_<backend>.xlsx` | Long list + By Service Type |
| `YYYYMMDD_ai_consulting_brief_<backend>.md` | Q&A research brief |
| `YYYYMMDD_ai_market_summary_<backend>.md` | Delivery formats, pricing, target audiences, named clients |
| `YYYYMMDD_ai_executive_brief_<backend>.md` | C-suite brief |
| `ai_consulting_all_priority_<backend>.csv` | Flat CSV |

### Legacy (`legacy_output/`)
| File | Description |
|---|---|
| `YYYYMMDD_legacy_long_list_<backend>.xlsx` | Consolidated long list |
| `YYYYMMDD_legacy_brief_<backend>.md` | Research brief answering 4 pillar questions |
| `legacy_all_priority_<backend>.csv` | Flat CSV |

### Client Intelligence (`client_output/`)
| File | Description |
|---|---|
| `YYYYMMDD_client_intel_long_list_<backend>.xlsx` | All signals per client |
| `YYYYMMDD_client_market_summary_<backend>.md` | Per-client AI spend profiles, vendor breakdown |
| `YYYYMMDD_client_executive_brief_<backend>.md` | C-suite brief |
| `YYYYMMDD_potential_clients_<backend>.md` | Potential clients matched to existing client profiles |
| `client_intel_all_priority_<backend>.csv` | Flat CSV |

### Strategic Intelligence (`output/`)
| File | Description |
|---|---|
| `YYYYMMDD_executive_summary.md` | Cross-pipeline executive summary with hypothesis verdicts |
| `YYYYMMDD_hypothesis_1_*.md` through `hypothesis_5_*.md` | Per-hypothesis deep-dives |

---

## Adding a New Pipeline

1. **Keyword taxonomy** — `lib/keywords/<name>.py` with `KEYWORD_GROUPS = {...}`
2. **Pipeline logic** — `lib/pipelines/<name>.py` implementing `extract_services()`, `build_rows()`, and optionally `generate_brief()`, `write_brief_md()`, `write_market_summary_md()`, `write_executive_brief_md()`
3. **Register in `scraper_new.py`** — add to `_MODES` dict and `SITES_DIR` env var mapping
4. **Register in `analyse_new.py`** — add to `_MODES` and `_pipeline` import dicts; add sheet layout to `_write_sheet` and `_write_detail_workbook`
5. **Add volume mounts** to `docker-compose.yml`
6. **Add to `.gitignore`** — `<name>_sites/*` and `<name>_output/*`

---

## Tips & Troubleshooting

**Resuming an interrupted scrape**
Re-run the same command — checkpoint system skips already-visited URLs. Use `FRESH=1` to start from scratch.

**Groq 429 rate limit errors**
Handled automatically with retry backoff. Use `--max-pages 5` and `GROQ_TIER=free`.

**Output files owned by root (Docker)**
```bash
sudo chown -R $USER:$USER output/ legacy_output/ ai_output/ client_output/ comp_spend_output/ news_output/
```

**A site returns 0 pages**
Site likely blocks bots or is JS-rendered. Check `pages_text.csv` — if empty, content isn't accessible via static crawl.

**Confluence API token expired**
Create a new token at `https://id.atlassian.com/manage-profile/security/api-tokens` and update `CONFLUENCE_API_TOKEN` in `.env`.

**News cron not running when machine is off**
The cron job simply doesn't run — no catch-up. Re-run `run_news.sh` manually after the machine comes back online if needed.

---

*AmaliTech Benchmarking Team — Internal Research Tooling*
