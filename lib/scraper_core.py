"""
lib/scraper_core.py — shared BFS crawl engine used by all scrape modes.
Caller provides KEYWORD_GROUPS and config; this module handles everything else.
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

import requests
from bs4 import BeautifulSoup

log = logging.getLogger("scraper")

REQUEST_TIMEOUT = 20
REQUEST_DELAY   = 1.2
HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AmaliTechResearchBot/1.0; +https://amalitech.com)"}

MIN_IMAGE_W  = 100; MIN_IMAGE_H = 100
SKIP_EXT     = {".svg", ".ico", ".gif", ".webp", ".bmp", ".tiff"}
CONTENT_TAGS = {"article", "section", "main", "div", "p"}

PAGE_FIELDS = ["url", "page_title", "depth", "relevance_score", "keyword_hits", "keyword_groups", "clean_text"]
OCR_FIELDS  = ["image_path", "image_url", "source_page_url", "extracted_text"]


# ── KEYWORD SCORING ───────────────────────────────────────────────────────────
def score_text(text: str, keyword_groups: dict) -> tuple[int, list[str]]:
    lower = text.lower()
    seen: set[str] = set()
    hits: list[str] = []
    for kws in keyword_groups.values():
        for kw in kws:
            if kw in lower and kw not in seen:
                seen.add(kw); hits.append(kw)
    return len(hits), hits


def group_hits(hits: list[str], keyword_groups: dict) -> dict:
    return {g: [h for h in hits if h in kws] for g, kws in keyword_groups.items()
            if any(h in kws for h in hits)}


# ── CHECKPOINT ────────────────────────────────────────────────────────────────
def load_checkpoint(site_dir: Path, fresh: bool) -> dict:
    cp = site_dir / "checkpoint.json"
    if fresh:
        return {}
    if cp.exists():
        try:
            data = json.loads(cp.read_text(encoding="utf-8"))
            log.info(f"  Checkpoint: {len(data)} visited URL(s).")
            return data
        except Exception as e:
            log.warning(f"  Checkpoint unreadable ({e}) — starting fresh.")
    return {}


def save_checkpoint(site_dir: Path, checkpoint: dict) -> None:
    cp = site_dir / "checkpoint.json"
    tmp = site_dir / "checkpoint.json.tmp"
    try:
        tmp.write_text(json.dumps(checkpoint, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(cp)
    except Exception as e:
        log.warning(f"  Could not save checkpoint: {e}")


# ── CSV APPEND ────────────────────────────────────────────────────────────────
def append_csv_row(csv_path: Path, row: dict, fields: list[str]) -> None:
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ── OCR ───────────────────────────────────────────────────────────────────────
def load_ocr(ocr_enabled: bool, ocr_engine: str):
    if not ocr_enabled:
        return None, None
    if ocr_engine == "pytesseract":
        try:
            import pytesseract
            return "pytesseract", pytesseract
        except ImportError:
            pass
    try:
        import easyocr
        return "easyocr", easyocr.Reader(["en"], gpu=False, verbose=False)
    except ImportError:
        log.error("easyocr not installed.")
        return None, None


def run_ocr(ocr_tuple, image_path: Path) -> str:
    engine_name, engine = ocr_tuple
    if engine is None:
        return ""
    try:
        if engine_name == "pytesseract":
            return engine.image_to_string(str(image_path)).strip()
        return " ".join(engine.readtext(str(image_path), detail=0)).strip()
    except Exception:
        return ""


# ── URL HELPERS ───────────────────────────────────────────────────────────────
def normalise(url: str) -> str:
    p = urllib.parse.urlparse(url)
    return urllib.parse.urlunparse(
        p._replace(scheme=p.scheme.lower(), netloc=p.netloc.lower(),
                   path=p.path.rstrip("/") or "/", fragment=""))


def same_domain(url: str, base: str) -> bool:
    host = urllib.parse.urlparse(url).netloc.lower()
    return host == base or host.endswith("." + base)


def slug(url: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]", "_", urllib.parse.urlparse(url).path.strip("/"))[:80] or "index"


def folder_name(url: str) -> str:
    host = urllib.parse.urlparse(url).netloc.lower()
    return f"{re.sub(r'[^a-zA-Z0-9]', '-', host).strip('-')}_{datetime.now().strftime('%Y-%m-%d_%H-%M-%S')}"


# ── IMAGE HELPERS ─────────────────────────────────────────────────────────────
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
    if not (CONTENT_TAGS & {p.name for p in img_tag.parents if p.name}):
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
        return data if w >= MIN_IMAGE_W and h >= MIN_IMAGE_H else None
    except Exception:
        return None


# ── TEXT EXTRACTION ───────────────────────────────────────────────────────────
def clean_text(soup: BeautifulSoup) -> str:
    for tag in soup(["script", "style", "noscript", "nav", "footer"]):
        tag.decompose()
    return re.sub(r"\s+", " ", soup.get_text(separator=" ", strip=True)).strip()


# ── CORE CRAWL ────────────────────────────────────────────────────────────────
def crawl(start_url: str, site_dir: Path, keyword_groups: dict,
          ocr_tuple, session: requests.Session,
          max_depth: int, min_relevance: int, fresh: bool,
          max_site_minutes: int = 0,
          extra_seed_paths: list = None,
          pdf_max_pages: int = 0) -> None:

    pages_dir = site_dir / "pages"
    pages_dir.mkdir(parents=True, exist_ok=True)
    if ocr_tuple[0]:
        (site_dir / "images").mkdir(exist_ok=True)

    pages_csv   = site_dir / "pages_text.csv"
    ocr_csv     = site_dir / "ocr_output.csv"
    pdf_csv     = site_dir / "pdf_text.csv"
    base_domain = urllib.parse.urlparse(start_url).netloc.lower()

    checkpoint = load_checkpoint(site_dir, fresh)
    visited    = set(checkpoint.keys())
    image_hashes: set[str] = set()

    if fresh:
        for f in (pages_csv, ocr_csv, pdf_csv):
            if f.exists():
                f.unlink()

    # Seed queue: priority paths first (depth 0), then main URL
    queue: deque[tuple[str, int]] = deque()
    if extra_seed_paths:
        base = start_url.rstrip("/")
        for path in extra_seed_paths:
            seed = normalise(base + path)
            if seed not in visited:
                queue.append((seed, 0))
    norm_start = normalise(start_url)
    if norm_start not in visited:
        queue.append((norm_start, 0))

    saved = skipped = 0
    crawl_start = time.time()

    while queue:
        if max_site_minutes > 0 and (time.time() - crawl_start) / 60 >= max_site_minutes:
            log.warning(f"  Time limit reached — stopping with {len(queue)} URLs remaining.")
            break

        url, depth = queue.popleft()
        norm = normalise(url)
        if norm in visited or depth > max_depth:
            continue
        visited.add(norm)

        try:
            time.sleep(REQUEST_DELAY)
            resp = session.get(url, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            content_type = resp.headers.get("Content-Type", "")

            # ── PDF handling ──
            if pdf_max_pages > 0 and "application/pdf" in content_type:
                pdf_text = _extract_pdf_text(resp.content, pdf_max_pages)
                if pdf_text.strip():
                    score, hits = score_text(pdf_text, keyword_groups)
                    groups = group_hits(hits, keyword_groups)
                    if score >= min_relevance:
                        saved += 1
                        log.info(f"    [{depth}] PDF SAVE (score={score}): {url}")
                        append_csv_row(pdf_csv, {
                            "url": url, "page_title": url.split("/")[-1], "depth": depth,
                            "relevance_score": score, "keyword_hits": "; ".join(hits[:20]),
                            "keyword_groups": "; ".join(groups.keys()), "clean_text": pdf_text,
                            "source_type": "pdf",
                        }, PAGE_FIELDS + ["source_type"])
                        checkpoint[norm] = {"outcome": "saved_pdf", "depth": depth, "relevance_score": score}
                    else:
                        checkpoint[norm] = {"outcome": "skipped_low_relevance", "depth": depth}
                save_checkpoint(site_dir, checkpoint)
                continue

            if "text/html" not in content_type:
                checkpoint[norm] = {"outcome": "skipped_non_html", "depth": depth}
                save_checkpoint(site_dir, checkpoint)
                continue
        except Exception as e:
            log.debug(f"    Fail {url}: {e}")
            checkpoint[norm] = {"outcome": "failed", "depth": depth, "error": str(e)[:120]}
            save_checkpoint(site_dir, checkpoint)
            continue

        soup  = BeautifulSoup(resp.text, "lxml")
        title = (soup.title.string or "").strip() if soup.title else ""
        text  = clean_text(soup)
        score, hits = score_text(text, keyword_groups)
        groups      = group_hits(hits, keyword_groups)

        # Detect page type for client_intel signal tagging
        source_type = _detect_source_type(url)

        if score < min_relevance:
            skipped += 1
            checkpoint[norm] = {"outcome": "skipped_low_relevance", "depth": depth, "relevance_score": score}
        else:
            saved += 1
            log.info(f"    [{depth}] SAVE (score={score}, type={source_type}): {url}")
            (pages_dir / f"{slug(url)}.html").write_text(resp.text, encoding="utf-8", errors="replace")
            append_csv_row(pages_csv, {
                "url": url, "page_title": title, "depth": depth,
                "relevance_score": score, "keyword_hits": "; ".join(hits[:20]),
                "keyword_groups": "; ".join(groups.keys()), "clean_text": text,
                "source_type": source_type,
            }, PAGE_FIELDS + ["source_type"])
            checkpoint[norm] = {"outcome": "saved", "depth": depth,
                                 "relevance_score": score, "keyword_groups": list(groups.keys())}

            if ocr_tuple[0]:
                images_dir = site_dir / "images"
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
                    ext      = Path(urllib.parse.urlparse(img_url).path).suffix.lower() or ".jpg"
                    img_path = images_dir / f"{img_hash[:12]}{ext}"
                    img_path.write_bytes(img_data)
                    append_csv_row(ocr_csv, {
                        "image_path": str(img_path.relative_to(site_dir)),
                        "image_url": img_url, "source_page_url": url,
                        "extracted_text": run_ocr(ocr_tuple, img_path),
                    }, OCR_FIELDS)

        save_checkpoint(site_dir, checkpoint)

        if depth < max_depth:
            for a in soup.find_all("a", href=True):
                href = a["href"].strip()
                abs_href = normalise(urllib.parse.urljoin(url, href))
                if not same_domain(abs_href, base_domain) or abs_href in visited:
                    continue
                # Queue PDFs if pdf extraction enabled
                if pdf_max_pages > 0 and abs_href.lower().endswith(".pdf"):
                    queue.append((abs_href, depth + 1))
                elif not abs_href.lower().endswith(".pdf"):
                    queue.append((abs_href, depth + 1))

    log.info(f"  Done: {saved} saved, {skipped} skipped → {site_dir.name}")


def _detect_source_type(url: str) -> str:
    url_lower = url.lower()
    if any(p in url_lower for p in ["/investor", "/ir/", "/annual", "/earnings"]):
        return "investor_relations"
    if any(p in url_lower for p in ["/news", "/press", "/newsroom", "/media"]):
        return "news_press"
    if any(p in url_lower for p in ["/career", "/jobs", "/vacancies", "/hiring"]):
        return "careers"
    if any(p in url_lower for p in ["/insight", "/report", "/research", "/whitepaper"]):
        return "insights_reports"
    if url_lower.endswith(".pdf"):
        return "pdf"
    return "general"


def _extract_pdf_text(content: bytes, max_pages: int) -> str:
    try:
        import pdfplumber
        from io import BytesIO
        with pdfplumber.open(BytesIO(content)) as pdf:
            pages = pdf.pages[:max_pages]
            return "\n\n".join(p.extract_text() or "" for p in pages).strip()
    except ImportError:
        log.warning("pdfplumber not installed — PDF extraction skipped. Run: pip install pdfplumber")
        return ""
    except Exception as e:
        log.debug(f"PDF extraction failed: {e}")
        return ""


# ── INPUT FILE HELPERS (used by scraper_new.py) ───────────────────────────────
def find_input_file(script_dir: Path, exclude_names: set = None) -> Path:
    exclude_names = exclude_names or set()
    candidates = [
        c for c in list(script_dir.glob("*.csv")) + list(script_dir.glob("*.txt"))
        if "scraper" not in c.name.lower() and c.name not in exclude_names
    ]
    if not candidates:
        raise FileNotFoundError("No input file found.")
    if len(candidates) > 1:
        log.warning(f"Multiple input files — using: {candidates[0].name}")
    return candidates[0]


def extract_urls(file_path: Path) -> list[str]:
    import pandas as pd
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
            (c for c in df.columns if any(k in c.lower() for k in ["url","website","site","link"])), None
        )
        if not url_col:
            raise ValueError(f"No URL column found in {file_path.name}")
        urls = df[url_col].dropna().astype(str).str.strip().tolist()
    cleaned = [("https://" + u if not u.startswith("http") else u) for u in urls if u.strip()]
    log.info(f"Loaded {len(cleaned)} URLs from {file_path.name}")
    return cleaned
