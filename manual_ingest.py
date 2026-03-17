"""
Manual Ingest: screenshots + text files -> CSV per competitor

Expected input layout (default):
  manual/
    <competitor_name>/
      images/
        post-01.png
      texts/
        post-01.txt

Matching rule:
- Image and text are paired by filename stem (post-01.png <-> post-01.txt)

Outputs (per competitor):
  sites/<competitor>_manual_<timestamp>/
    images/
    texts/
    ocr_output.csv
    posts_text.csv

Environment variables:
  MANUAL_DIR  : override input root (default auto-detect)
  OUTPUT_DIR  : override output root (default: ./sites)
  COMPETITOR  : process only a single competitor directory name
  OCR_ENGINE  : easyocr | pytesseract (default: easyocr)
  SKIP_OCR    : set to 1 to skip OCR
"""

from __future__ import annotations

import csv
import os
import re
import shutil
from datetime import datetime
from pathlib import Path

from scraper import load_ocr, run_ocr  # reuse existing OCR setup

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff"}
TEXT_EXTS = {".txt", ".md"}

SCRIPT_DIR = Path(__file__).parent.resolve()


def _now_ts() -> str:
    return datetime.now().strftime("%Y-%m-%d_%H-%M-%S")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "competitor"


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _resolve_manual_dir() -> Path:
    explicit = os.getenv("MANUAL_DIR")
    if explicit:
        return Path(explicit).expanduser().resolve()

    candidates = [SCRIPT_DIR / "manual", Path("/app/input/manual")]
    for c in candidates:
        if c.exists():
            return c
    # default to ./manual even if it doesn't exist yet
    return SCRIPT_DIR / "manual"


def _safe_copy(src: Path, dest_dir: Path) -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / src.name
    if dest.exists():
        # avoid clobbering
        stem = dest.stem
        suffix = dest.suffix
        i = 2
        while True:
            candidate = dest_dir / f"{stem}__{i}{suffix}"
            if not candidate.exists():
                dest = candidate
                break
            i += 1
    shutil.copy2(src, dest)
    return dest


def _file_mtime_iso(path: Path) -> str:
    try:
        return datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds")
    except Exception:
        return ""


def _load_text_file(path: Path) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    return _normalize_text(content)


def _collect_files(dir_path: Path, exts: set[str]) -> list[Path]:
    if not dir_path.exists():
        return []
    return sorted([p for p in dir_path.iterdir() if p.is_file() and p.suffix.lower() in exts])


def ingest_competitor(competitor_dir: Path, output_root: Path, ocr_tuple, skip_ocr: bool):
    competitor_name = competitor_dir.name
    images_dir = competitor_dir / "images"
    texts_dir = competitor_dir / "texts"

    images = _collect_files(images_dir, IMAGE_EXTS)
    texts = _collect_files(texts_dir, TEXT_EXTS)

    if not images and not texts:
        print(f"[skip] {competitor_name}: no images or texts found")
        return

    out_dir = output_root / f"{_slugify(competitor_name)}_manual_{_now_ts()}"
    out_images = out_dir / "images"
    out_texts = out_dir / "texts"
    out_dir.mkdir(parents=True, exist_ok=True)

    # map text files by stem for pairing
    texts_by_stem = {p.stem.lower(): p for p in texts}

    ocr_rows = []
    posts_rows = []

    for img in images:
        stem = img.stem.lower()
        text_path = texts_by_stem.pop(stem, None)

        provided_text = _load_text_file(text_path) if text_path else ""
        ocr_text = "" if skip_ocr else run_ocr(ocr_tuple, img)

        copied_img = _safe_copy(img, out_images)
        copied_txt = _safe_copy(text_path, out_texts) if text_path else None

        source = "image_and_text" if text_path else "image_only"
        combined = _normalize_text("\n".join([t for t in [provided_text, ocr_text] if t]))

        ocr_rows.append({
            "competitor": competitor_name,
            "image_path": str(copied_img.relative_to(out_dir)),
            "source_image": str(img),
            "captured_at": _file_mtime_iso(img),
            "extracted_text": ocr_text,
        })

        posts_rows.append({
            "competitor": competitor_name,
            "source": source,
            "image_path": str(copied_img.relative_to(out_dir)),
            "text_path": str(copied_txt.relative_to(out_dir)) if copied_txt else "",
            "source_image": str(img),
            "source_text": str(text_path) if text_path else "",
            "captured_at": _file_mtime_iso(img),
            "ocr_text": ocr_text,
            "provided_text": provided_text,
            "combined_text": combined,
        })

    # leftover texts without images
    for stem, text_path in texts_by_stem.items():
        provided_text = _load_text_file(text_path)
        copied_txt = _safe_copy(text_path, out_texts)
        posts_rows.append({
            "competitor": competitor_name,
            "source": "text_only",
            "image_path": "",
            "text_path": str(copied_txt.relative_to(out_dir)),
            "source_image": "",
            "source_text": str(text_path),
            "captured_at": _file_mtime_iso(text_path),
            "ocr_text": "",
            "provided_text": provided_text,
            "combined_text": provided_text,
        })

    # Write CSVs
    ocr_csv = out_dir / "ocr_output.csv"
    posts_csv = out_dir / "posts_text.csv"

    if ocr_rows:
        with open(ocr_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(ocr_rows[0].keys()))
            writer.writeheader()
            writer.writerows(ocr_rows)

    if posts_rows:
        with open(posts_csv, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=list(posts_rows[0].keys()))
            writer.writeheader()
            writer.writerows(posts_rows)

    print(f"[done] {competitor_name}: {len(posts_rows)} rows -> {out_dir}")


def main():
    manual_dir = _resolve_manual_dir()
    output_root = Path(os.getenv("OUTPUT_DIR", str(SCRIPT_DIR / "sites"))).resolve()
    competitor_filter = os.getenv("COMPETITOR", "").strip()
    skip_ocr = os.getenv("SKIP_OCR", "").strip() in {"1", "true", "yes"}

    if not manual_dir.exists():
        print(f"Manual dir not found: {manual_dir}")
        print("Create it like: manual/<competitor>/images and manual/<competitor>/texts")
        return

    if competitor_filter:
        competitor_dirs = [manual_dir / competitor_filter]
    else:
        competitor_dirs = [p for p in manual_dir.iterdir() if p.is_dir()]

    if not competitor_dirs:
        print(f"No competitor folders found under: {manual_dir}")
        return

    ocr_tuple = (None, None) if skip_ocr else load_ocr()

    for cdir in sorted(competitor_dirs):
        if not cdir.exists():
            print(f"[skip] {cdir.name}: folder not found")
            continue
        ingest_competitor(cdir, output_root, ocr_tuple, skip_ocr)


if __name__ == "__main__":
    main()
