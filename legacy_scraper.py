"""
Legacy Modernization Research Scraper
=======================================
A focused scraper for researching AI-assisted legacy system modernisation:
  - COBOL / mainframe → modern language (Java, Python, C#)
  - Java version migration (8 → 11/17/21)
  - Related tools, platforms, and services

Key behaviours:
  - Saves pages_text.csv and ocr_output.csv after EVERY page and EVERY image
    so no work is lost if the scraper is interrupted mid-crawl.
  - Writes a checkpoint file (checkpoint.json) per site folder tracking every
    visited URL and its outcome. On restart, already-visited URLs are skipped
    automatically so the crawl resumes from where it left off.
  - Set FRESH=1 to ignore the checkpoint and start the site from scratch.
  - Keyword filtering: only saves pages that mention modernisation topics.
  - Relevance score per page based on keyword density.

Environment variables:
  MAX_DEPTH       Max crawl depth (default: 2)
  OCR             Set to 1 to enable OCR on images (default: off)
  OCR_ENGINE      easyocr | pytesseract (default: easyocr)
  INPUT_FILE      Override input file name
  MIN_RELEVANCE   Min keyword hits to save a page (default: 2)
  FRESH           Set to 1 to ignore checkpoints and re-crawl all sites
  MAX_SITE_MINUTES  Max minutes to spend per site (default: 0 = unlimited)

Usage:
  python legacy_scraper.py
  MAX_DEPTH=3 python legacy_scraper.py
  FRESH=1 python legacy_scraper.py          # ignore all checkpoints
  FRESH=1 INPUT_FILE=my_urls.txt python legacy_scraper.py

Output per site:
  legacy/<host>_<timestamp>/
    pages/                raw HTML files
    images/               downloaded images (only if OCR=1)
    pages_text.csv        written after every page — safe to read mid-crawl
    ocr_output.csv        written after every image — safe to read mid-crawl
    checkpoint.json       visited URLs + outcomes — enables resume on restart
"""

from __future__ import annotations

import csv
import hashlib
import json
import logging
import os
import re
import time
import urllib.parse
from collections import deque
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd
import requests
from bs4 import BeautifulSoup

# ── LOGGING ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("legacy_scraper")

# ── CONFIG ────────────────────────────────────────────────────────────────────
SCRIPT_DIR    = Path(__file__).parent.resolve()
SITES_DIR     = Path(os.getenv("LEGACY_DIR", "/app/legacy"))
MAX_DEPTH        = int(os.getenv("MAX_DEPTH", 2))
OCR_ENABLED      = os.getenv("OCR", "0").strip() in ("1", "true", "yes")
OCR_ENGINE       = os.getenv("OCR_ENGINE", "easyocr").lower().strip()
INPUT_FILE       = os.getenv("INPUT_FILE", "")
MIN_RELEVANCE    = int(os.getenv("MIN_RELEVANCE", 2))
FRESH            = os.getenv("FRESH", "0").strip() in ("1", "true", "yes")
MAX_SITE_MINUTES = int(os.getenv("MAX_SITE_MINUTES", 0))  # 0 = unlimited

REQUEST_TIMEOUT = 20
REQUEST_DELAY   = 1.2
USER_AGENT      = (
    "Mozilla/5.0 (compatible; AmaliTechResearchBot/1.0; +https://amalitech.com)"
)
HEADERS = {"User-Agent": USER_AGENT}

# Image settings (only used if OCR_ENABLED)
MIN_IMAGE_W  = 100
MIN_IMAGE_H  = 100
SKIP_EXT     = {".svg", ".ico", ".gif", ".webp", ".bmp", ".tiff"}
CONTENT_TAGS = {"article", "section", "main", "div", "p"}

# CSV column definitions — defined once so headers are always consistent
PAGE_FIELDS = [
    "url", "page_title", "depth", "relevance_score",
    "keyword_hits", "keyword_groups", "clean_text",
]
OCR_FIELDS = [
    "image_path", "image_url", "source_page_url", "extracted_text",
]

# ── KEYWORD TAXONOMY ──────────────────────────────────────────────────────────
KEYWORD_GROUPS = {
    "mainframe_modernization": [
        "mainframe", "cobol", "pl/i", "pl1", "jcl", "cics", "vsam", "idms",
        "zos", "z/os", "as/400", "ibm z", "ibm system", "legacy mainframe",
        "mainframe migration", "mainframe modernization", "mainframe refactor",
        "mainframe rehost", "mainframe replatform", "cobol migration",
        "cobol modernization", "cobol refactoring", "cobol to java",
        "cobol to python", "cobol translation", "cobol conversion",
        "mainframe exit", "mainframe offload", "monolith decompos",
    ],
    "java_migration": [
        "java 8", "java 11", "java 17", "java 21", "java 25",
        "jdk 8", "jdk 11", "jdk 17", "jdk 21",
        "java upgrade", "java migration", "java modernization",
        "java version", "spring boot migration", "spring boot 3",
        "jakarta ee", "java ee migration", "openrewrite",
        "amazon q code transformation", "amazon q transform",
        "github copilot modernization", "github copilot app modernization",
        "lts upgrade", "java lts", "maven upgrade", "gradle upgrade",
        "spring framework 6", "junit upgrade", "ant to maven",
    ],
    "ai_assisted_modernization": [
        "ai-assisted modernization", "ai assisted modernization",
        "generative ai modernization", "gen ai modernization",
        "agentic ai modernization", "llm modernization",
        "ai refactoring", "ai code migration", "ai code transformation",
        "watsonx code assistant", "aws transform", "aws blu age",
        "aws mainframe modernization", "github copilot modernization",
        "amazon q developer", "codeconcise", "swimm", "moderne",
        "openrewrite", "kodesage", "heirloom", "persistent systems",
        "multi-agent cobol", "ai legacy", "genai legacy",
        "code transformation agent", "automated refactoring",
        "automated code conversion", "automated migration",
    ],
    "legacy_services": [
        "legacy modernization", "application modernization",
        "legacy migration", "legacy transformation",
        "technical debt", "code refactoring", "system refactoring",
        "monolith to microservices", "microservices migration",
        "replatforming", "rehosting", "reengineering",
        "cloud migration", "digital transformation",
        "application portfolio", "legacy code", "legacy system",
        "business logic extraction", "reverse engineering",
        "code analysis", "dependency mapping", "code documentation",
        "strangler pattern", "incremental modernization",
    ],
    "tools_and_platforms": [
        "ibm watsonx", "watsonx code assistant for z",
        "aws mainframe modernization service", "aws transform",
        "azure mainframe migration", "google mainframe",
        "openrewrite", "moderne", "swimm", "kodesage",
        "micro focus", "opentext mainframe", "blu age",
        "tmaxsoft", "openframe", "heirloom computing",
        "cast highlight", "sonarqube", "tsri",
        "hexaware amaze", "kyndryl modernization",
        "github legacy modernization agents",
        "persistent modernization", "infosys modernization",
        "capgemini admnext", "tcs mastercraft",
    ],
}

ALL_KEYWORDS: list[str] = [
    kw for group in KEYWORD_GROUPS.values() for kw in group
]


# ── KEYWORD MATCHING ──────────────────────────────────────────────────────────
def score_text(text: str) -> tuple[int, list[str]]:
    lower = text.lower()
    seen: set[str] = set()
    hits: list[str] = []
    for kw in ALL_KEYWORDS:
        if kw in lower and kw not in seen:
            seen.add(kw)
            hits.append(kw)
    return len(hits), hits


def group_hits(hits: list[str]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for group, keywords in KEYWORD_GROUPS.items():
        matched = [h for h in hits if h in keywords]
        if matched:
            result[group] = matched
    return result


# ── CHECKPOINT ────────────────────────────────────────────────────────────────
def load_checkpoint(site_dir: Path) -> dict:
    """
    Load checkpoint.json from site_dir.
    Returns dict: { normalised_url: { "outcome": "saved|skipped|failed",
                                       "depth": int, "ts": ISO } }
    Returns empty dict if FRESH=1 or file does not exist.
    """
    cp_path = site_dir / "checkpoint.json"
    if FRESH:
        if cp_path.exists():
            log.info("  FRESH=1 — ignoring existing checkpoint.")
        return {}
    if cp_path.exists():
        try:
            data = json.loads(cp_path.read_text(encoding="utf-8"))
            visited_count = len(data)
            log.info(f"  Checkpoint loaded: {visited_count} previously visited URL(s).")
            return data
        except Exception as e:
            log.warning(f"  Checkpoint unreadable ({e}) — starting fresh for this site.")
    return {}


def save_checkpoint(site_dir: Path, checkpoint: dict) -> None:
    """Overwrite checkpoint.json atomically using a temp file."""
    cp_path  = site_dir / "checkpoint.json"
    tmp_path = site_dir / "checkpoint.json.tmp"
    try:
        tmp_path.write_text(
            json.dumps(checkpoint, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        tmp_path.replace(cp_path)   # atomic on POSIX; near-atomic on Windows
    except Exception as e:
        log.warning(f"  Could not save checkpoint: {e}")


# ── CSV HELPERS — incremental append ─────────────────────────────────────────
def append_csv_row(csv_path: Path, row: dict, fields: list[str]) -> None:
    """
    Append one row to a CSV file.
    Writes the header first if the file does not yet exist.
    Uses newline='' so csv.writer handles line endings correctly.
    """
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ── OCR ───────────────────────────────────────────────────────────────────────
def load_ocr():
    if not OCR_ENABLED:
        return None, None
    if OCR_ENGINE == "pytesseract":
        try:
            import pytesseract
            log.info("OCR: pytesseract")
            return "pytesseract", pytesseract
        except ImportError:
            log.warning("pytesseract not installed — falling back to easyocr")
    try:
        import easyocr
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        log.info("OCR: easyocr")
        return "easyocr", reader
    except ImportError:
        log.error("easyocr not installed. Run: pip install easyocr")
        return None, None


def run_ocr(ocr_tuple, image_path: Path) -> str:
    engine_name, engine = ocr_tuple
    if engine is None:
        return ""
    try:
        if engine_name == "pytesseract":
            return engine.image_to_string(str(image_path)).strip()
        results = engine.readtext(str(image_path), detail=0)
        return " ".join(results).strip()
    except Exception as e:
        log.debug(f"OCR failed for {image_path.name}: {e}")
        return ""


# ── INPUT FILE ────────────────────────────────────────────────────────────────
def find_input_file() -> Path:
    if INPUT_FILE:
        p = SCRIPT_DIR / INPUT_FILE
        if p.exists():
            return p
        raise FileNotFoundError(f"INPUT_FILE '{INPUT_FILE}' not found.")

    preferred = SCRIPT_DIR / "legacy_modernization_urls.txt"
    if preferred.exists():
        return preferred

    candidates = [
        c for c in
        list(SCRIPT_DIR.glob("*.csv")) + list(SCRIPT_DIR.glob("*.txt"))
        if "scraper" not in c.name.lower() and c.name != Path(__file__).name
    ]
    if not candidates:
        raise FileNotFoundError(
            "No input file found. Create legacy_modernization_urls.txt"
        )
    if len(candidates) > 1:
        log.warning(f"Multiple input files — using: {candidates[0].name}")
    return candidates[0]


def extract_urls(file_path: Path) -> list[str]:
    suffix = file_path.suffix.lower()
    urls: list[str] = []

    if suffix == ".txt":
        for line in file_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                urls.append(line)
    elif suffix == ".csv":
        df = pd.read_csv(file_path, dtype=str)
        url_col = next(
            (c for c in df.columns
             if any(k in c.lower() for k in ["url", "website", "site", "link"])),
            None,
        )
        if not url_col:
            url_col = next(
                (c for c in df.columns
                 if df[c].dropna().astype(str)
                    .str.contains("http", case=False).any()),
                None,
            )
        if not url_col:
            raise ValueError(f"No URL column found in {file_path.name}")
        urls = df[url_col].dropna().astype(str).str.strip().tolist()

    cleaned: list[str] = []
    for u in urls:
        u = u.strip()
        if not u:
            continue
        if not u.startswith("http"):
            u = "https://" + u
        cleaned.append(u)

    log.info(f"Loaded {len(cleaned)} URLs from {file_path.name}")
    return cleaned


# ── URL HELPERS ───────────────────────────────────────────────────────────────
def normalise(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(
        p._replace(
            scheme=p.scheme.lower(),
            netloc=p.netloc.lower(),
            path=p.path.rstrip("/") or "/",
            fragment="",
        )
    )


def same_domain(url: str, base: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host == base or host.endswith("." + base)


def slug(url: str) -> str:
    path = urllib.parse.urlparse(url).path.strip("/")
    return re.sub(r"[^a-zA-Z0-9_-]", "_", path)[:80] or "index"


def folder_name(url: str) -> str:
    host      = urllib.parse.urlparse(url).netloc.lower()
    host_slug = re.sub(r"[^a-zA-Z0-9]", "-", host).strip("-")
    ts        = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"{host_slug}_{ts}"


# ── TEXT EXTRACTION ───────────────────────────────────────────────────────────
def clean_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "nav", "footer"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    return re.sub(r"\s+", " ", text).strip()


# ── IMAGE HANDLING ────────────────────────────────────────────────────────────
def is_relevant_image(img_tag, base_url: str) -> tuple[bool, str]:
    src = img_tag.get("src") or img_tag.get("data-src") or ""
    if not src:
        return False, ""
    abs_url = urllib.parse.urljoin(base_url, src)
    parsed  = urllib.parse.urlparse(abs_url)
    if parsed.scheme not in ("http", "https"):
        return False, ""
    if Path(parsed.path).suffix.lower() in SKIP_EXT:
        return False, ""
    parent_tags = {p.name for p in img_tag.parents if p.name}
    if not (CONTENT_TAGS & parent_tags):
        return False, ""
    return True, abs_url


def download_image(img_url: str, session: requests.Session) -> bytes | None:
    try:
        from PIL import Image
        resp = session.get(img_url, timeout=REQUEST_TIMEOUT, stream=True)
        resp.raise_for_status()
        data = resp.content
        img  = Image.open(BytesIO(data))
        w, h = img.size
        if w < MIN_IMAGE_W or h < MIN_IMAGE_H:
            return None
        return data
    except Exception:
        return None


# ── CORE CRAWL ────────────────────────────────────────────────────────────────
def crawl(start_url: str, site_dir: Path, ocr_tuple, session: requests.Session):
    pages_dir  = site_dir / "pages"
    images_dir = site_dir / "images"
    pages_dir.mkdir(parents=True, exist_ok=True)
    if OCR_ENABLED:
        images_dir.mkdir(parents=True, exist_ok=True)

    pages_csv = site_dir / "pages_text.csv"
    ocr_csv   = site_dir / "ocr_output.csv"

    base_domain = urllib.parse.urlparse(start_url).netloc.lower()

    # ── Load checkpoint ──
    checkpoint = load_checkpoint(site_dir)

    # Rebuild visited set and image hashes from checkpoint
    visited: set[str]      = set(checkpoint.keys())
    image_hashes: set[str] = set()

    # If resuming, also reload image hashes from existing ocr_output.csv
    # to avoid downloading + OCRing the same image again
    if ocr_csv.exists() and not FRESH:
        try:
            existing_ocr = pd.read_csv(ocr_csv, dtype=str).fillna("")
            for _, row in existing_ocr.iterrows():
                img_path = site_dir / row.get("image_path", "")
                if img_path.exists():
                    try:
                        image_hashes.add(hashlib.md5(img_path.read_bytes()).hexdigest())
                    except Exception:
                        pass
        except Exception as e:
            log.warning(f"  Could not reload image hashes from ocr_output.csv: {e}")

    # If FRESH, wipe existing CSVs so we start clean
    if FRESH:
        for f in (pages_csv, ocr_csv):
            if f.exists():
                f.unlink()
                log.info(f"  FRESH — removed existing {f.name}")

    # Build initial queue — only enqueue start URL if not already visited
    queue: deque[tuple[str, int]] = deque()
    norm_start = normalise(start_url)
    if norm_start not in visited:
        queue.append((norm_start, 0))
    else:
        log.info(f"  Start URL already visited — will only enqueue unvisited child links.")
        # Still need to rebuild queue from checkpoint so we can follow
        # pending links that were queued but not yet visited
        # (checkpoint only tracks visited, not the pending queue,
        #  so we simply start the BFS fresh but skip visited URLs)
        queue.append((norm_start, 0))

    total_saved   = 0
    total_skipped = 0
    total_resumed = len(visited)

    if total_resumed:
        log.info(
            f"  Resuming crawl — {total_resumed} URL(s) already visited, "
            f"skipping them automatically."
        )

    log.info(
        f"  Crawling: {start_url}  "
        f"(depth={MAX_DEPTH}, min_relevance={MIN_RELEVANCE}, "
        f"fresh={'yes' if FRESH else 'no'})"
    )

    crawl_start = time.time()

    while queue:
        # ── Per-site time limit ──
        if MAX_SITE_MINUTES > 0:
            elapsed = (time.time() - crawl_start) / 60
            if elapsed >= MAX_SITE_MINUTES:
                log.warning(
                    f"  Time limit reached ({MAX_SITE_MINUTES} min) — "
                    f"stopping {start_url} with {len(queue)} URLs remaining in queue."
                )
                break

        url, depth = queue.popleft()
        norm = normalise(url)

        if norm in visited or depth > MAX_DEPTH:
            continue
        visited.add(norm)

        try:
            time.sleep(REQUEST_DELAY)
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            if "text/html" not in resp.headers.get("Content-Type", ""):
                checkpoint[norm] = {
                    "outcome": "skipped_non_html",
                    "depth": depth,
                    "ts": datetime.now().isoformat(timespec="seconds"),
                }
                save_checkpoint(site_dir, checkpoint)
                continue
        except Exception as e:
            log.debug(f"    Fail {url}: {e}")
            checkpoint[norm] = {
                "outcome": "failed",
                "depth":   depth,
                "error":   str(e)[:120],
                "ts":      datetime.now().isoformat(timespec="seconds"),
            }
            save_checkpoint(site_dir, checkpoint)
            continue

        soup  = BeautifulSoup(resp.text, "lxml")
        title = (soup.title.string or "").strip() if soup.title else ""
        text  = clean_text(soup)

        score, hits = score_text(text)
        groups      = group_hits(hits)

        if score < MIN_RELEVANCE:
            total_skipped += 1
            log.debug(f"    [{depth}] SKIP (score={score}): {url}")
            checkpoint[norm] = {
                "outcome":        "skipped_low_relevance",
                "depth":          depth,
                "relevance_score": score,
                "ts":             datetime.now().isoformat(timespec="seconds"),
            }
            save_checkpoint(site_dir, checkpoint)
        else:
            total_saved += 1
            log.info(f"    [{depth}] SAVE (score={score}): {url}")

            # ── Save raw HTML ──
            html_file = pages_dir / f"{slug(url)}.html"
            html_file.write_text(resp.text, encoding="utf-8", errors="replace")

            # ── Append page row to CSV immediately ──
            page_row = {
                "url":             url,
                "page_title":      title,
                "depth":           depth,
                "relevance_score": score,
                "keyword_hits":    "; ".join(hits[:20]),
                "keyword_groups":  "; ".join(groups.keys()),
                "clean_text":      text,
            }
            append_csv_row(pages_csv, page_row, PAGE_FIELDS)

            # ── Update checkpoint ──
            checkpoint[norm] = {
                "outcome":         "saved",
                "depth":           depth,
                "relevance_score": score,
                "keyword_groups":  list(groups.keys()),
                "ts":              datetime.now().isoformat(timespec="seconds"),
            }
            save_checkpoint(site_dir, checkpoint)

            # ── OCR images immediately (if enabled) ──
            if OCR_ENABLED:
                for img_tag in soup.find_all("img"):
                    relevant, img_url = is_relevant_image(img_tag, url)
                    if not relevant:
                        continue

                    img_data = download_image(img_url, session)
                    if img_data is None:
                        continue

                    img_hash = hashlib.md5(img_data).hexdigest()
                    if img_hash in image_hashes:
                        continue
                    image_hashes.add(img_hash)

                    ext       = (
                        Path(urllib.parse.urlparse(img_url).path).suffix.lower()
                        or ".jpg"
                    )
                    img_fname = f"{img_hash[:12]}{ext}"
                    img_path  = images_dir / img_fname
                    img_path.write_bytes(img_data)

                    # ── OCR and append row immediately ──
                    ocr_text = run_ocr(ocr_tuple, img_path)
                    ocr_row  = {
                        "image_path":      str(img_path.relative_to(site_dir)),
                        "image_url":       img_url,
                        "source_page_url": url,
                        "extracted_text":  ocr_text,
                    }
                    append_csv_row(ocr_csv, ocr_row, OCR_FIELDS)
                    log.debug(f"      Image saved + OCR'd: {img_fname}")

        # ── Enqueue internal links ──
        if depth < MAX_DEPTH:
            for a in soup.find_all("a", href=True):
                href     = a["href"].strip()
                abs_href = normalise(urllib.parse.urljoin(url, href))
                if same_domain(abs_href, base_domain) and abs_href not in visited:
                    queue.append((abs_href, depth + 1))

    log.info(
        f"  Done: {total_saved} saved, {total_skipped} skipped "
        f"(low relevance), {total_resumed} resumed/skipped from checkpoint "
        f"→ {site_dir.name}"
    )


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 65)
    log.info("AmaliTech Legacy Modernization Research Scraper")
    log.info(f"  MAX_DEPTH     : {MAX_DEPTH}")
    log.info(f"  MIN_RELEVANCE : {MIN_RELEVANCE}")
    log.info(
        f"  OCR           : "
        f"{'enabled (' + OCR_ENGINE + ')' if OCR_ENABLED else 'disabled'}"
    )
    log.info(f"  FRESH         : {'yes — checkpoints ignored' if FRESH else 'no — will resume interrupted crawls'}")
    log.info(f"  MAX_SITE_MINS : {'unlimited' if MAX_SITE_MINUTES == 0 else str(MAX_SITE_MINUTES) + ' min per site'}")
    log.info("=" * 65)

    input_file = find_input_file()
    urls       = extract_urls(input_file)

    if not urls:
        log.error("No URLs found. Exiting.")
        return

    ocr_tuple = load_ocr()
    session   = requests.Session()
    session.headers.update(HEADERS)
    adapter = requests.adapters.HTTPAdapter(max_retries=2)
    session.mount("http://",  adapter)
    session.mount("https://", adapter)

    SITES_DIR.mkdir(exist_ok=True)

    for i, url in enumerate(urls, 1):
        log.info(f"\n[{i}/{len(urls)}] {url}")

        # Find existing site folder for this URL if one exists and is not fresh
        existing_folder = None
        if not FRESH:
            host_slug = re.sub(
                r"[^a-zA-Z0-9]", "-",
                urllib.parse.urlparse(url).netloc.lower()
            ).strip("-")
            matches = sorted(SITES_DIR.glob(f"{host_slug}_*"))
            if matches:
                existing_folder = matches[-1]  # most recent
                log.info(f"  Existing folder found — resuming: {existing_folder.name}")

        site_dir = existing_folder or (SITES_DIR / folder_name(url))
        site_dir.mkdir(parents=True, exist_ok=True)

        try:
            crawl(url, site_dir, ocr_tuple, session)
        except Exception as e:
            log.error(f"  Failed: {url} — {e}")

    log.info("\nAll URLs processed. Results in: ./legacy/")


if __name__ == "__main__":
    main()
