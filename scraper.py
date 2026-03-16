"""
Amalitech Competitor Research Scraper
======================================
Reads a list of competitor URLs from a CSV or TXT file in the same directory,
deep-crawls each site, saves page text and relevant images, and runs OCR on images.

Environment variables:
  MAX_DEPTH   : How many link levels deep to crawl (default: 3)
  OCR_ENGINE  : 'pytesseract' or 'easyocr' (default: easyocr)
  INPUT_FILE  : Override auto-detection and specify input file name

Usage:
  python scraper.py

Requirements:
  pip install requests beautifulsoup4 pillow pandas lxml easyocr
  For pytesseract: pip install pytesseract + install Tesseract on your system
"""

import os
import re
import csv
import time
import logging
import hashlib
import urllib.parse
from io import BytesIO
from datetime import datetime
from pathlib import Path
from collections import deque

import requests
import pandas as pd
from bs4 import BeautifulSoup
from PIL import Image

# ── LOGGING ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("scraper")

# ── CONFIGURATION ─────────────────────────────────────────────────────────────
MAX_DEPTH   = int(os.getenv("MAX_DEPTH", 3))
OCR_ENGINE  = os.getenv("OCR_ENGINE", "easyocr").lower().strip()
INPUT_FILE  = os.getenv("INPUT_FILE", "")          # optional override
SCRIPT_DIR  = Path(__file__).parent.resolve()
SITES_DIR   = SCRIPT_DIR / "sites"

REQUEST_TIMEOUT   = 15       # seconds per request
REQUEST_DELAY     = 1.0      # seconds between requests (be polite)
MIN_IMAGE_WIDTH   = 100      # px — skip smaller images
MIN_IMAGE_HEIGHT  = 100      # px
SKIP_EXTENSIONS   = {".svg", ".ico", ".gif", ".webp", ".bmp", ".tiff"}
CONTENT_TAGS      = {"article", "section", "main", "div"}   # image must live inside one of these
USER_AGENT        = (
    "Mozilla/5.0 (compatible; AmaliTechResearchBot/1.0; +https://amalitech.com)"
)

HEADERS = {"User-Agent": USER_AGENT}

# ── OCR SETUP ─────────────────────────────────────────────────────────────────
def load_ocr():
    """Load the OCR engine once at startup."""
    if OCR_ENGINE == "pytesseract":
        try:
            import pytesseract
            log.info("OCR engine: pytesseract")
            return ("pytesseract", pytesseract)
        except ImportError:
            log.warning("pytesseract not installed — falling back to easyocr")

    try:
        import easyocr
        reader = easyocr.Reader(["en"], gpu=False, verbose=False)
        log.info("OCR engine: easyocr")
        return ("easyocr", reader)
    except ImportError:
        log.error(
            "easyocr not installed. Run: pip install easyocr\n"
            "Or set OCR_ENGINE=pytesseract and install pytesseract."
        )
        return (None, None)


def run_ocr(ocr_tuple, image_path: Path) -> str:
    """Extract text from an image file. Returns empty string on failure."""
    engine_name, engine = ocr_tuple
    if engine is None:
        return ""
    try:
        if engine_name == "pytesseract":
            return engine.image_to_string(str(image_path)).strip()
        else:
            results = engine.readtext(str(image_path), detail=0)
            return " ".join(results).strip()
    except Exception as e:
        log.debug(f"OCR failed for {image_path.name}: {e}")
        return ""


# ── INPUT FILE DETECTION ──────────────────────────────────────────────────────
def find_input_file() -> Path:
    """Auto-detect a CSV or TXT input file in the script directory."""
    if INPUT_FILE:
        p = SCRIPT_DIR / INPUT_FILE
        if p.exists():
            return p
        raise FileNotFoundError(f"INPUT_FILE '{INPUT_FILE}' not found in {SCRIPT_DIR}")

    candidates = list(SCRIPT_DIR.glob("*.csv")) + list(SCRIPT_DIR.glob("*.txt"))
    # Exclude this script itself and any output files
    candidates = [
        c for c in candidates
        if c.name != Path(__file__).name and "scraper" not in c.name.lower()
    ]
    if not candidates:
        raise FileNotFoundError(
            f"No CSV or TXT input file found in {SCRIPT_DIR}.\n"
            "Create a file with one competitor URL per line, or a CSV with a URL column."
        )
    if len(candidates) > 1:
        log.warning(f"Multiple input files found: {[c.name for c in candidates]}. Using: {candidates[0].name}")
    return candidates[0]


def extract_urls(file_path: Path) -> list[str]:
    """Extract URLs from a CSV (any column containing urls/websites/site) or plain TXT."""
    suffix = file_path.suffix.lower()
    urls = []

    if suffix == ".txt":
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#"):
                    urls.append(line)

    elif suffix == ".csv":
        df = pd.read_csv(file_path, dtype=str)
        # Find URL column — flexible matching
        url_col = None
        priority = ["url", "website", "site", "link", "domain", "competitor_url"]
        for p in priority:
            for col in df.columns:
                if p in col.lower():
                    url_col = col
                    break
            if url_col:
                break
        if not url_col:
            # Fall back: first column that contains http
            for col in df.columns:
                sample = df[col].dropna().head(5).astype(str)
                if sample.str.contains("http", case=False).any():
                    url_col = col
                    break
        if not url_col:
            raise ValueError(
                f"Could not find a URL column in {file_path.name}.\n"
                f"Columns found: {list(df.columns)}\n"
                "Rename the URL column to 'url', 'website', or 'site'."
            )
        log.info(f"Using column '{url_col}' from {file_path.name}")
        urls = df[url_col].dropna().astype(str).str.strip().tolist()
    else:
        raise ValueError(f"Unsupported file type: {suffix}. Use .csv or .txt")

    # Normalise — ensure https scheme
    cleaned = []
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
def normalise_url(url: str) -> str:
    """Remove fragment, trailing slash, and lowercase scheme+host."""
    p = urllib.parse.urlparse(url)
    normalised = p._replace(
        scheme=p.scheme.lower(),
        netloc=p.netloc.lower(),
        fragment=""
    )
    path = normalised.path.rstrip("/") or "/"
    return urllib.parse.urlunparse(normalised._replace(path=path))


def same_domain(url: str, base_domain: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host == base_domain or host.endswith("." + base_domain)


def slug_from_url(url: str) -> str:
    """Create a safe filename from a URL path."""
    path = urllib.parse.urlparse(url).path.strip("/")
    slug = re.sub(r"[^a-zA-Z0-9_-]", "_", path) or "index"
    return slug[:80]


def site_folder_name(url: str) -> str:
    """E.g. 'accenture-com_2025-03-16_14-30-00'"""
    host = urllib.parse.urlparse(url).netloc.lower()
    host_slug = re.sub(r"[^a-zA-Z0-9]", "-", host).strip("-")
    ts = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    return f"{host_slug}_{ts}"


# ── IMAGE RELEVANCE CHECK ─────────────────────────────────────────────────────
def is_relevant_image(img_tag, base_url: str) -> tuple[bool, str]:
    """
    Returns (is_relevant, absolute_img_url).
    Skips decorative images: small size, bad extension, or outside content tags.
    """
    src = img_tag.get("src") or img_tag.get("data-src") or ""
    if not src:
        return False, ""

    abs_url = urllib.parse.urljoin(base_url, src)
    parsed = urllib.parse.urlparse(abs_url)

    # Skip non-http (data URIs etc.)
    if parsed.scheme not in ("http", "https"):
        return False, ""

    # Skip by extension
    ext = Path(parsed.path).suffix.lower()
    if ext in SKIP_EXTENSIONS:
        return False, ""

    # Must be inside a content tag
    parent_tags = {p.name for p in img_tag.parents if p.name}
    if not (CONTENT_TAGS & parent_tags):
        return False, ""

    return True, abs_url


def download_and_filter_image(img_url: str, session: requests.Session) -> bytes | None:
    """Download image bytes; return None if too small or download fails."""
    try:
        resp = session.get(img_url, timeout=REQUEST_TIMEOUT, stream=True)
        resp.raise_for_status()
        data = resp.content
        img = Image.open(BytesIO(data))
        w, h = img.size
        if w < MIN_IMAGE_WIDTH or h < MIN_IMAGE_HEIGHT:
            return None
        return data
    except Exception:
        return None


# ── PAGE TEXT EXTRACTION ──────────────────────────────────────────────────────
def extract_clean_text(soup: BeautifulSoup) -> str:
    """Remove scripts/styles and return clean readable text."""
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    text = soup.get_text(separator=" ", strip=True)
    # Collapse whitespace
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── CORE CRAWLER ─────────────────────────────────────────────────────────────
def crawl_site(start_url: str, site_dir: Path, ocr_tuple: tuple, session: requests.Session):
    """
    BFS crawl of a single competitor site.
    Writes pages_text.csv and ocr_output.csv into site_dir.
    """
    pages_dir  = site_dir / "pages"
    images_dir = site_dir / "images"
    pages_dir.mkdir(parents=True, exist_ok=True)
    images_dir.mkdir(parents=True, exist_ok=True)

    base_domain = urllib.parse.urlparse(start_url).netloc.lower()
    visited: set[str] = set()
    # Queue items: (url, depth)
    queue: deque[tuple[str, int]] = deque([(normalise_url(start_url), 0)])

    pages_rows: list[dict]  = []
    ocr_rows:   list[dict]  = []
    image_hashes: set[str]  = set()   # avoid duplicate images across pages

    log.info(f"  Starting crawl: {start_url}  (max depth={MAX_DEPTH})")

    while queue:
        url, depth = queue.popleft()
        norm = normalise_url(url)

        if norm in visited:
            continue
        if depth > MAX_DEPTH:
            continue

        visited.add(norm)

        try:
            time.sleep(REQUEST_DELAY)
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")
            if "text/html" not in content_type:
                continue
        except Exception as e:
            log.debug(f"    Skip {url}: {e}")
            continue

        log.info(f"    [{depth}] {url}")
        soup = BeautifulSoup(resp.text, "lxml")

        # ── Save raw HTML ──
        html_file = pages_dir / f"{slug_from_url(url)}.html"
        html_file.write_text(resp.text, encoding="utf-8", errors="replace")

        # ── Extract page text ──
        title = soup.title.string.strip() if soup.title and soup.title.string else ""
        clean_text = extract_clean_text(soup)
        pages_rows.append({
            "url":        url,
            "page_title": title,
            "depth":      depth,
            "clean_text": clean_text,
        })

        # ── Process images ──
        for img_tag in soup.find_all("img"):
            relevant, img_url = is_relevant_image(img_tag, url)
            if not relevant:
                continue

            img_data = download_and_filter_image(img_url, session)
            if img_data is None:
                continue

            # Deduplicate by content hash
            img_hash = hashlib.md5(img_data).hexdigest()
            if img_hash in image_hashes:
                continue
            image_hashes.add(img_hash)

            # Save image
            ext = Path(urllib.parse.urlparse(img_url).path).suffix.lower() or ".jpg"
            img_filename = f"{img_hash[:12]}{ext}"
            img_path = images_dir / img_filename
            img_path.write_bytes(img_data)

            # OCR
            ocr_text = run_ocr(ocr_tuple, img_path)
            ocr_rows.append({
                "image_path":      str(img_path.relative_to(site_dir)),
                "image_url":       img_url,
                "source_page_url": url,
                "extracted_text":  ocr_text,
            })
            log.debug(f"      Image saved: {img_filename}")

        # ── Enqueue internal links ──
        if depth < MAX_DEPTH:
            for a_tag in soup.find_all("a", href=True):
                href = a_tag["href"].strip()
                abs_href = urllib.parse.urljoin(url, href)
                abs_href = normalise_url(abs_href)
                if same_domain(abs_href, base_domain) and abs_href not in visited:
                    queue.append((abs_href, depth + 1))

    # ── Write CSVs ──
    pages_csv = site_dir / "pages_text.csv"
    ocr_csv   = site_dir / "ocr_output.csv"

    pd.DataFrame(pages_rows).to_csv(pages_csv, index=False, encoding="utf-8")
    pd.DataFrame(ocr_rows).to_csv(ocr_csv,   index=False, encoding="utf-8")

    log.info(f"  Done: {len(pages_rows)} pages, {len(ocr_rows)} images with OCR → {site_dir.name}")


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    log.info("=" * 60)
    log.info("Amalitech Competitor Research Scraper")
    log.info(f"  MAX_DEPTH  : {MAX_DEPTH}")
    log.info(f"  OCR_ENGINE : {OCR_ENGINE}")
    log.info("=" * 60)

    # 1. Load OCR engine
    ocr_tuple = load_ocr()

    # 2. Find and parse input file
    input_file = find_input_file()
    urls = extract_urls(input_file)

    if not urls:
        log.error("No URLs found in input file. Exiting.")
        return

    # 3. Set up session with retries
    session = requests.Session()
    session.headers.update(HEADERS)

    adapter = requests.adapters.HTTPAdapter(max_retries=2)
    session.mount("http://",  adapter)
    session.mount("https://", adapter)

    # 4. Crawl each site
    SITES_DIR.mkdir(exist_ok=True)

    for i, url in enumerate(urls, 1):
        log.info(f"\n[{i}/{len(urls)}] Crawling: {url}")
        folder_name = site_folder_name(url)
        site_dir    = SITES_DIR / folder_name
        site_dir.mkdir(parents=True, exist_ok=True)

        try:
            crawl_site(url, site_dir, ocr_tuple, session)
        except Exception as e:
            log.error(f"  Failed to crawl {url}: {e}")

    log.info("\nAll sites processed. Results in: ./sites/")


if __name__ == "__main__":
    main()
