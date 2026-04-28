"""
scraper.py — unified scraper runner.

SCRAPE_MODE controls which keyword taxonomy and output directory to use:
  SCRAPE_MODE=competitor    → lib/keywords/competitor.py  → sites/
  SCRAPE_MODE=legacy        → lib/keywords/legacy.py      → legacy/
  SCRAPE_MODE=ai_consulting → lib/keywords/ai_consulting.py → ai_sites/

All other behaviour (BFS, checkpoint, OCR, relevance scoring) is identical.

Environment variables:
  SCRAPE_MODE       competitor | legacy | ai_consulting (default: competitor)
  MAX_DEPTH         default: 2
  MIN_RELEVANCE     default: 2
  MAX_SITE_MINUTES  default: 0 (unlimited)
  FRESH             set to 1 to ignore checkpoints
  OCR               set to 1 to enable OCR
  OCR_ENGINE        easyocr | pytesseract
  INPUT_FILE        override input file name
  APP_DIR           base path (set to /app in Docker)

Usage:
  python scraper.py
  SCRAPE_MODE=legacy python scraper.py
  docker compose exec -e SCRAPE_MODE=ai_consulting scraper python3 /app/input/scraper.py
"""

from __future__ import annotations

import logging
import os
import re
import urllib.parse
from pathlib import Path

import requests

from lib.scraper_core import crawl, load_ocr, folder_name, normalise
from lib.scraper_core import find_input_file as _find_input_file, extract_urls

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("scraper")

# ── CONFIG ────────────────────────────────────────────────────────────────────
SCRAPE_MODE      = os.getenv("SCRAPE_MODE", "competitor").lower().strip()
MAX_DEPTH        = int(os.getenv("MAX_DEPTH", 2))
MIN_RELEVANCE    = int(os.getenv("MIN_RELEVANCE", 2))
MAX_SITE_MINUTES = int(os.getenv("MAX_SITE_MINUTES", 0))
FRESH            = os.getenv("FRESH", "0").strip() in ("1", "true", "yes")
OCR_ENABLED      = os.getenv("OCR", "0").strip() in ("1", "true", "yes")
OCR_ENGINE       = os.getenv("OCR_ENGINE", "easyocr").lower().strip()
INPUT_FILE_ENV   = os.getenv("INPUT_FILE", "")
SCRIPT_DIR       = Path(os.getenv("APP_DIR", Path(__file__).parent.resolve()))

# ── MODE → keywords + dirs + default input file ───────────────────────────────
_MODES = {
    "competitor":       ("lib.keywords.competitor",       "sites",       "urls.txt"),
    "legacy":           ("lib.keywords.legacy",            "legacy",      "legacy_modernization_urls.txt"),
    "ai_consulting":    ("lib.keywords.ai_consulting",     "ai_sites",    "ai_consulting_urls.txt"),
    "client_intel":     ("lib.keywords.client_intel",      "client_sites","client_intel_urls.txt"),
    "news_monitoring":  ("lib.keywords.news_monitoring",   "news_sites",  "news_monitoring_urls.txt"),
    "competitor_spend": ("lib.keywords.competitor_spend",  "comp_spend_sites", "competitor_spend_urls.txt"),
}

if SCRAPE_MODE not in _MODES:
    raise ValueError(f"Unknown SCRAPE_MODE '{SCRAPE_MODE}'. Choose: {list(_MODES)}")

_kw_module, _sites_subdir, _default_input = _MODES[SCRAPE_MODE]

import importlib
KEYWORD_GROUPS = importlib.import_module(_kw_module).KEYWORD_GROUPS

SITES_DIR = Path(os.getenv(
    {"competitor": "SITES_DIR", "legacy": "LEGACY_DIR",
     "ai_consulting": "AI_SITES_DIR", "client_intel": "CLIENT_SITES_DIR",
     "news_monitoring": "NEWS_SITES_DIR",
     "competitor_spend": "COMP_SPEND_SITES_DIR"}[SCRAPE_MODE],
    str(SCRIPT_DIR / _sites_subdir)
))


def find_input_file() -> Path:
    if INPUT_FILE_ENV:
        p = SCRIPT_DIR / INPUT_FILE_ENV
        if p.exists():
            return p
        raise FileNotFoundError(f"INPUT_FILE '{INPUT_FILE_ENV}' not found.")
    preferred = SCRIPT_DIR / _default_input
    if preferred.exists():
        return preferred
    # Fall back to scraper_core auto-detect
    return _find_input_file(SCRIPT_DIR, exclude_names={"scraper.py"})


def main():
    log.info("=" * 65)
    log.info(f"AmaliTech Scraper  [mode={SCRAPE_MODE}]")
    log.info(f"  MAX_DEPTH     : {MAX_DEPTH}")
    log.info(f"  MIN_RELEVANCE : {MIN_RELEVANCE}")
    log.info(f"  FRESH         : {'yes' if FRESH else 'no'}")
    log.info(f"  MAX_SITE_MINS : {'unlimited' if MAX_SITE_MINUTES == 0 else str(MAX_SITE_MINUTES) + ' min'}")
    log.info(f"  OUTPUT        : {SITES_DIR}")
    log.info("=" * 65)

    input_file = find_input_file()

    # client_intel uses pipe-separated format: client_type|client_name|url
    if SCRAPE_MODE == "client_intel":
        entries = _parse_client_intel_urls(input_file)
    else:
        entries = [(None, None, u) for u in extract_urls(input_file)]

    if not entries:
        log.error("No URLs found. Exiting.")
        return

    ocr_tuple = load_ocr(OCR_ENABLED, OCR_ENGINE)
    session   = requests.Session()
    session.headers.update({"User-Agent": "Mozilla/5.0 (compatible; AmaliTechResearchBot/1.0; +https://amalitech.com)"})
    adapter = requests.adapters.HTTPAdapter(max_retries=2)
    session.mount("http://", adapter); session.mount("https://", adapter)

    SITES_DIR.mkdir(exist_ok=True)

    for i, (client_type, client_name, url) in enumerate(entries, 1):
        label = f"{client_name} ({client_type})" if client_name else url
        log.info(f"\n[{i}/{len(entries)}] {label}  →  {url}")
        existing_folder = None
        if not FRESH:
            host_slug = re.sub(r"[^a-zA-Z0-9]", "-", urllib.parse.urlparse(url).netloc.lower()).strip("-")
            matches = sorted(SITES_DIR.glob(f"{host_slug}_*"))
            if matches:
                existing_folder = matches[-1]
                log.info(f"  Resuming: {existing_folder.name}")
        site_dir = existing_folder or (SITES_DIR / folder_name(url))
        site_dir.mkdir(parents=True, exist_ok=True)

        # Save client metadata alongside scraped data
        if client_name:
            (site_dir / "client_meta.json").write_text(
                __import__("json").dumps({"client_type": client_type, "client_name": client_name, "url": url},
                                         indent=2), encoding="utf-8"
            )

        try:
            extra_seeds = _CLIENT_INTEL_PATHS if SCRAPE_MODE == "client_intel" else []
            crawl(url, site_dir, KEYWORD_GROUPS, ocr_tuple, session,
                  max_depth=MAX_DEPTH, min_relevance=MIN_RELEVANCE,
                  fresh=FRESH, max_site_minutes=MAX_SITE_MINUTES,
                  extra_seed_paths=extra_seeds,
                  pdf_max_pages=int(os.getenv("PDF_MAX_PAGES", 10)) if SCRAPE_MODE == "client_intel" else 0)
        except Exception as e:
            log.error(f"  Failed: {url} — {e}")

    log.info(f"\nAll URLs processed. Results in: {SITES_DIR}/")


# ── CLIENT INTEL HELPERS ──────────────────────────────────────────────────────
# High-value paths to seed at depth 0 before main BFS
_CLIENT_INTEL_PATHS = [
    "/investors", "/investor-relations", "/ir",
    "/news", "/press", "/press-releases", "/newsroom",
    "/insights", "/reports", "/annual-report",
    "/careers", "/jobs",
    "/strategy", "/about/strategy",
]


def _parse_client_intel_urls(file_path: Path) -> list[tuple[str, str, str]]:
    """Parse pipe-separated client_intel_urls.txt → [(client_type, client_name, url)]."""
    entries = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split("|")
        if len(parts) == 3:
            client_type, client_name, url = [p.strip() for p in parts]
            if not url.startswith("http"):
                url = "https://" + url
            entries.append((client_type, client_name, url))
        elif line.startswith("http"):
            entries.append((None, None, line))
    log.info(f"Loaded {len(entries)} client URLs from {file_path.name}")
    return entries


if __name__ == "__main__":
    main()
