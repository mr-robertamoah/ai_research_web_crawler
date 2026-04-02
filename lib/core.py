"""
lib/core.py — shared AI backend, tier config, state, CSV, and JSON helpers.
All three pipelines import from here.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import re
import time
from pathlib import Path

log = logging.getLogger("analyse")

# ── AI BACKEND ────────────────────────────────────────────────────────────────
AI_BACKEND   = os.getenv("AI_BACKEND", "groq").lower().strip()
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GROQ_MODEL   = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant")
GROQ_URL     = "https://api.groq.com/openai/v1/chat/completions"
CLAUDE_KEY   = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-haiku-4-5-20251001")

# ── TIER CONFIG ───────────────────────────────────────────────────────────────
# Claude has no TPM limit — always use paid-tier limits when AI_BACKEND=claude
# GROQ_TIER=free  → 5 pages, 1k chars/page, 6k content window
# GROQ_TIER=paid  → 20 pages, 4k chars/page, 24k content window
_TIER           = "paid" if AI_BACKEND == "claude" else os.getenv("GROQ_TIER", "free").lower().strip()
TIER_MAX_PAGES  = 20    if _TIER == "paid" else 5
TIER_PAGE_CHARS = 4_000 if _TIER == "paid" else 1_000
TIER_CONTENT    = 24_000 if _TIER == "paid" else 6_000
TIER_MAX_TOKENS = 8192  if AI_BACKEND == "claude" else (4096 if _TIER == "paid" else 2048)


# ── AI CALLS ──────────────────────────────────────────────────────────────────
def _call_groq(system: str, user: str, max_tokens: int, retries: int = 5) -> str:
    import requests as req
    if not GROQ_API_KEY:
        raise EnvironmentError("GROQ_API_KEY not set.")
    headers = {"Authorization": f"Bearer {GROQ_API_KEY}", "Content-Type": "application/json"}
    payload = {"model": GROQ_MODEL, "max_tokens": max_tokens, "temperature": 0.1,
               "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}
    for attempt in range(retries):
        try:
            resp = req.post(GROQ_URL, headers=headers, json=payload, timeout=120)
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"].strip()
        except Exception as e:
            if attempt == retries - 1:
                raise
            wait = 20 * (attempt + 1)
            log.warning(f"Groq error ({e}) — retrying in {wait}s")
            time.sleep(wait)
    raise RuntimeError("Groq: all retries exhausted.")


def _call_claude(system: str, user: str, max_tokens: int, retries: int = 3) -> str:
    try:
        import anthropic
    except ImportError:
        raise ImportError("Run: pip install anthropic")
    if not CLAUDE_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY not set.")
    client = anthropic.Anthropic(api_key=CLAUDE_KEY)
    for attempt in range(retries):
        try:
            resp = client.messages.create(model=CLAUDE_MODEL, max_tokens=max_tokens,
                                          system=system, messages=[{"role": "user", "content": user}])
            return resp.content[0].text.strip()
        except Exception as e:
            if "rate" in str(e).lower():
                time.sleep(30 * (attempt + 1))
            elif attempt == retries - 1:
                raise
            else:
                time.sleep(10)
    raise RuntimeError("Claude: all retries exhausted.")


def call_ai(system: str, user: str, max_tokens: int = None) -> str:
    """Route to Groq or Claude based on AI_BACKEND env var."""
    mt = max_tokens or TIER_MAX_TOKENS
    return _call_claude(system, user, mt) if AI_BACKEND == "claude" else _call_groq(system, user, mt)


def parse_json(raw: str, context: str = "") -> dict | list | None:
    clean = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
    clean = re.sub(r"^```(?:json)?\s*", "", clean, flags=re.MULTILINE)
    clean = re.sub(r"\s*```$", "", clean, flags=re.MULTILINE).strip()
    # Normalise pipe-separated enum values inside JSON strings
    clean = re.sub(r'"([^"]*)\s*\|\s*([^"]*)"', lambda m: f'"{m.group(1).strip()}"', clean)
    try:
        return json.loads(clean)
    except json.JSONDecodeError:
        for pattern in (r"(\[.*\])", r"(\{.*\})"):
            m = re.search(pattern, clean, re.DOTALL)
            if m:
                try:
                    return json.loads(m.group(1))
                except Exception:
                    pass
        log.error(f"JSON parse error [{context}]: could not parse response")
        log.debug(f"Raw: {clean[:300]}")
        return None


# ── STATE ─────────────────────────────────────────────────────────────────────
def load_state(path: Path) -> dict:
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            log.warning("State file unreadable — starting fresh.")
    return {}


def save_state(path: Path, state: dict) -> None:
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")


def clear_state(path: Path) -> None:
    if path.exists():
        path.unlink()
        log.info("State cleared — all folders will be reprocessed.")


# ── CSV HELPERS ───────────────────────────────────────────────────────────────
def append_csv_row(csv_path: Path, row: dict, fields: list[str]) -> None:
    write_header = not csv_path.exists()
    with open(csv_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerow(row)


# ── CONTENT LOADING ───────────────────────────────────────────────────────────
def find_site_folders(sites_dir: Path, source_filter: str = "") -> list[Path]:
    if not sites_dir.exists():
        raise FileNotFoundError(f"Sites dir not found: {sites_dir}")
    folders = [
        p for p in sorted(sites_dir.iterdir())
        if p.is_dir() and (p / "pages_text.csv").exists()
        and (not source_filter or source_filter.lower() in p.name.lower())
    ]
    if not folders:
        raise FileNotFoundError(f"No site folders found under {sites_dir}.")
    return folders


def source_name(folder: Path) -> str:
    raw = folder.name.split("_")[0]
    raw = re.sub(r"-(com|ai|io|net|org|co|gov|edu)$", "", raw, flags=re.IGNORECASE)
    return raw.replace("-", " ").title()


def load_content(folder: Path, max_pages: int = None) -> str:
    max_pages = max_pages or TIER_MAX_PAGES
    csv_path  = folder / "pages_text.csv"
    if not csv_path.exists():
        return ""
    try:
        import pandas as pd
        df = pd.read_csv(csv_path, dtype=str).fillna("")
        if "relevance_score" in df.columns:
            df["relevance_score"] = pd.to_numeric(df["relevance_score"], errors="coerce").fillna(0)
            df = df.sort_values("relevance_score", ascending=False)
        df = df.head(max_pages)
        chunks = []
        for _, row in df.iterrows():
            text = row.get("clean_text", "").strip()
            if text:
                header = f"[PAGE: {row.get('page_title','')} | {row.get('url','')}]"
                chunks.append(f"{header}\n{text[:TIER_PAGE_CHARS]}")
        return "\n\n---\n\n".join(chunks)
    except Exception as e:
        log.warning(f"Could not read {csv_path}: {e}")
        return ""
