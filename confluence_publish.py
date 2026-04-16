"""
confluence_publish.py — publishes pipeline outputs to Confluence.

Final structure:
  Research/
    Competitors/
      Competitor Index
      Competitor — Hypothesis Tracker
      Competitor — Market Summary
      Profiles/               ← folder-page
        Artefact, Wipro, ...  ← one page per competitor, clean names
    Clients & Prospects/
      Client Index
      Client Intel — Vendor & Tool Usage
      Client Intel — Potential Client Matches
      Profiles/               ← folder-page
        Siemens, BMW Group, ... ← one page per client, clean names
    Market & Trends/
      AI Consulting Market Overview
      AI Consulting — Pricing & Format Analysis
      Legacy Modernisation Brief
    Archive — Superseded Pages/

  AI Advisory & Readiness/
    Service Line Summary
    Top Competitors — AI Advisory
    Target Clients — AI Advisory
    Pricing Benchmarks — AI Advisory

  AI Engineering, Automation & Platforms/
    Service Line Summary
    Top Competitors — AI Engineering
    Target Clients — AI Engineering
    Pricing Benchmarks — AI Engineering

  AI Powered Solutions & New Revenue Models/
    Service Line Summary
    Top Competitors — AI Powered Solutions
    Target Clients — AI Powered Solutions
    Pricing Benchmarks — AI Powered Solutions

Usage:
  python confluence_publish.py                    # full publish
  python confluence_publish.py --mode research    # Research folder only
  python confluence_publish.py --mode strategy    # Strategy folders only
  python confluence_publish.py --dry-run

Env vars (in .env):
  CONFLUENCE_BASE_URL, CONFLUENCE_EMAIL, CONFLUENCE_API_TOKEN,
  CONFLUENCE_SPACE_KEY, CONFLUENCE_RESEARCH_PAGE_ID
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("confluence")

SCRIPT_DIR = Path(__file__).parent.resolve()

# ── KNOWN FOLDER IDs (from space inspection) ──────────────────────────────────
_STRATEGY_FOLDER_IDS = {
    "ai_advisory":    "2474541066",
    "ai_engineering": "2476244993",
    "ai_solutions":   "2476179464",
}


def _read_env(key: str) -> str:
    val = os.getenv(key, "")
    if val:
        return val
    env_file = SCRIPT_DIR / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return ""


BASE_URL    = _read_env("CONFLUENCE_BASE_URL").rstrip("/")
EMAIL       = _read_env("CONFLUENCE_EMAIL")
TOKEN       = _read_env("CONFLUENCE_API_TOKEN")
SPACE_KEY   = _read_env("CONFLUENCE_SPACE_KEY")
RESEARCH_ID = _read_env("CONFLUENCE_RESEARCH_PAGE_ID")

AUTH    = (EMAIL, TOKEN)
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


# ── API HELPERS ───────────────────────────────────────────────────────────────
def _find_child(parent_id: str, title: str) -> str | None:
    if not parent_id or not parent_id.isdigit():
        return None
    try:
        data = requests.get(f"{BASE_URL}/rest/api/content/{parent_id}/child/page?limit=100",
                            auth=AUTH, headers=HEADERS, timeout=15).json()
        for p in data.get("results", []):
            if p["title"] == title:
                return p["id"]
        return None  # only return pages that are direct children of the correct parent
    except Exception:
        pass
    return None


def _create_page(parent_id: str, title: str, body: str) -> str:
    payload = {
        "type": "page", "title": title,
        "space": {"key": SPACE_KEY},
        "ancestors": [{"id": parent_id}],
        "body": {"storage": {"value": body, "representation": "storage"}},
    }
    resp = requests.post(f"{BASE_URL}/rest/api/content", auth=AUTH,
                         headers=HEADERS, json=payload, timeout=15)
    if not resp.ok:
        log.error(f"  Create failed '{title}': {resp.status_code} {resp.text[:200]}")
    resp.raise_for_status()
    pid = resp.json()["id"]
    log.info(f"  Created: '{title}' (id={pid})")
    return pid


def _update_page(page_id: str, title: str, body: str) -> None:
    current = requests.get(f"{BASE_URL}/rest/api/content/{page_id}?expand=version",
                           auth=AUTH, headers=HEADERS, timeout=15).json()
    v = current["version"]["number"] + 1
    payload = {"type": "page", "title": title, "version": {"number": v},
               "body": {"storage": {"value": body, "representation": "storage"}}}
    resp = requests.put(f"{BASE_URL}/rest/api/content/{page_id}", auth=AUTH,
                        headers=HEADERS, json=payload, timeout=15)
    if not resp.ok:
        log.error(f"  Update failed '{title}': {resp.status_code} {resp.text[:200]}")
    resp.raise_for_status()
    log.info(f"  Updated: '{title}' (v{v})")


def upsert(parent_id: str, title: str, body: str, dry_run: bool = False) -> str:
    existing = _find_child(parent_id, title)
    if dry_run:
        log.info(f"  [DRY RUN] {'Update' if existing else 'Create'}: '{title}'")
        return existing or "dry-run"
    if existing:
        _update_page(existing, title, body)
        return existing
    return _create_page(parent_id, title, body)


def ensure_folder_page(parent_id: str, title: str, dry_run: bool = False) -> str:
    existing = _find_child(parent_id, title)
    if existing:
        return existing
    body = f"<p><em>{title} — updated {_ts()}.</em></p>"
    return upsert(parent_id, title, body, dry_run)


def move_page(page_id: str, new_parent_id: str, title: str) -> bool:
    """Move a page to a new parent."""
    r = requests.get(f"{BASE_URL}/rest/api/content/{page_id}?expand=version,body.storage",
                     auth=AUTH, headers=HEADERS, timeout=15)
    if not r.ok:
        return False
    data = r.json()
    v = data["version"]["number"] + 1
    body_val = data.get("body", {}).get("storage", {}).get("value", "")
    payload = {"type": "page", "title": title, "version": {"number": v},
               "ancestors": [{"id": new_parent_id}],
               "body": {"storage": {"value": body_val, "representation": "storage"}}}
    r2 = requests.put(f"{BASE_URL}/rest/api/content/{page_id}", auth=AUTH,
                      headers=HEADERS, json=payload, timeout=15)
    return r2.ok


# ── CONTENT BUILDERS ──────────────────────────────────────────────────────────
def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M UTC")


def _info(text: str) -> str:
    return (f'<ac:structured-macro ac:name="info">'
            f'<ac:rich-text-body><p>{text}</p></ac:rich-text-body>'
            f'</ac:structured-macro>')


def _h2(text: str) -> str: return f"<h2>{text}</h2>"
def _h3(text: str) -> str: return f"<h3>{text}</h3>"


def _priority_span(val: str) -> str:
    v = str(val).lower()
    if v in ("high", "yes"):   return f'<span style="color:#276221;font-weight:bold">{val}</span>'
    if v == "medium":          return f'<span style="color:#9C6500;font-weight:bold">{val}</span>'
    if v in ("low", "no"):     return f'<span style="color:#9C0006">{val}</span>'
    return str(val)


def _table(df: pd.DataFrame, max_rows: int = 150) -> str:
    if df.empty:
        return "<p><em>No data.</em></p>"
    df = df.head(max_rows)
    rows = ["<table><tbody>"]
    rows.append("<tr>" + "".join(f"<th><strong>{c}</strong></th>" for c in df.columns) + "</tr>")
    for _, row in df.iterrows():
        cells = []
        for col, val in zip(df.columns, row):
            val = str(val) if val is not None else ""
            col_l = col.lower()
            if any(k in col_l for k in ("priority", "alert", "innovation")):
                cells.append(f"<td>{_priority_span(val)}</td>")
            elif col_l in ("url", "source_url") and val.startswith("http"):
                short = val[:60] + "…" if len(val) > 60 else val
                cells.append(f'<td><a href="{val}">{short}</a></td>')
            else:
                cells.append(f"<td>{val}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _md_to_html(md: str) -> str:
    lines, out = md.splitlines(), []
    for line in lines:
        if line.startswith("# "):     out.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):  out.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "): out.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("> "):   out.append(f"<blockquote><p>{line[2:]}</p></blockquote>")
        elif line.startswith("---"):  out.append("<hr/>")
        elif line.strip() == "":     out.append("<br/>")
        elif line.startswith("| ") and "|" in line: out.append(line)
        else:
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            line = re.sub(r"\*(.+?)\*", r"<em>\1</em>", line)
            out.append(f"<p>{line}</p>")
    result = "\n".join(out)
    def _conv(m):
        rows = [r for r in m.group(0).splitlines() if not re.match(r"^\|[-| ]+\|$", r)]
        html = ["<table><tbody>"]
        for i, row in enumerate(rows):
            cells = [c.strip() for c in row.strip("|").split("|")]
            tag = "th" if i == 0 else "td"
            html.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
        html.append("</tbody></table>")
        return "\n".join(html)
    return re.sub(r"(\|.+\|\n)+", _conv, result)


def _latest(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def _clean_name(raw: str) -> str:
    """Strip 'Www ' prefix and title-case."""
    name = re.sub(r"^Www\s+", "", raw, flags=re.IGNORECASE).strip()
    # Known mappings for common abbreviations
    known = {
        "Db": "Deutsche Bank", "Bmwgroup": "BMW Group",
        "Sc": "Standard Chartered", "Nfl": "NFL",
        "Nhs Uk": "NHS", "Gov Uk": "MHRA / UK Gov",
        "Mtn": "MTN Group", "Ing": "ING Group",
        "Pg": "P&G",
    }
    return known.get(name, name)


def _load_client_meta(sites_dir: Path) -> dict[str, dict]:
    """Load client_meta.json from each client_sites folder → {source_name: meta}."""
    meta = {}
    if not sites_dir.exists():
        return meta
    for folder in sites_dir.iterdir():
        mf = folder / "client_meta.json"
        if mf.exists():
            try:
                data = json.loads(mf.read_text(encoding="utf-8"))
                # source_name as used in the CSV
                host = folder.name.split("_")[0]
                source = re.sub(r"-(com|ai|io|net|org|co|gov|edu|uk|ca|tw)$", "", host, flags=re.IGNORECASE)
                source = source.replace("-", " ").title()
                meta[source] = data
            except Exception:
                pass
    return meta


# ══════════════════════════════════════════════════════════════════════════════
# RESEARCH FOLDER PUBLISHERS
# ══════════════════════════════════════════════════════════════════════════════

def publish_competitors(parent_id: str, dry_run: bool) -> None:
    """Research/Competitors/ — Index, Hypothesis Tracker, Market Summary, Profiles/"""
    out = SCRIPT_DIR / "output"
    csv = _latest(out, "competitor_all_priority_*.csv")
    if not csv:
        log.warning("Competitor CSV not found — skipping."); return

    df = pd.read_csv(csv, dtype=str).fillna("")
    ts = _ts()
    by_comp = {c: g for c, g in df.groupby("competitor")}

    # ── Competitor Index ──
    idx_rows = []
    for comp, rows in sorted(by_comp.items()):
        scores = pd.to_numeric(rows["priority_score"], errors="coerce").fillna(0)
        idx_rows.append({
            "Competitor": _clean_name(comp),
            "# Services": len(rows),
            "Avg Score": round(scores.mean(), 1),
            "High": (rows["priority_tier"].str.lower() == "high").sum(),
            "Medium": (rows["priority_tier"].str.lower() == "medium").sum(),
            "Top Categories": ", ".join(rows["category"].value_counts().head(3).index.tolist()),
            "Clients": ", ".join(c.strip() for r in rows.get("clients", pd.Series(dtype=str)).tolist()
                                 for c in str(r).split(",") if c.strip())[:120],
        })
    idx_df = pd.DataFrame(idx_rows).sort_values("Avg Score", ascending=False)
    upsert(parent_id, "Competitor Index",
           _info(f"{len(by_comp)} competitors analysed. {len(df)} services scored. Updated {ts}.") +
           _table(idx_df), dry_run)

    # ── Hypothesis Tracker ──
    hyp_file = _latest(out, "hypothesis_tracker_*.json")
    if hyp_file:
        hyp_data = json.loads(hyp_file.read_text(encoding="utf-8"))
        hypotheses = [
            "Competitors charging AI premium of 15–30% over baseline rates",
            "Fastest-growing competitors pivoting to outcome/value-based pricing",
            "AI capability built via hyperscaler partnerships not internal R&D",
            "European buyers prioritising data sovereignty and compliance-safe AI",
            "Competitors concentrating AI investment in 2–3 verticals",
        ]
        rows = []
        for comp, verdicts in hyp_data.items():
            for h_key, h_val in verdicts.items():
                idx = int(h_key[1:]) - 1
                rows.append({"Hypothesis": hypotheses[idx] if idx < len(hypotheses) else h_key,
                             "Competitor": _clean_name(comp),
                             "Verdict": h_val.get("verdict", ""),
                             "Evidence For": h_val.get("evidence_for", "")[:200],
                             "Evidence Against": h_val.get("evidence_against", "")[:200]})
        upsert(parent_id, "Competitor Hypothesis Tracker",
               _info(f"Strategic hypothesis verdicts per competitor. Updated {ts}.") +
               _table(pd.DataFrame(rows)), dry_run)

    # ── Market Summary ──
    md_file = _latest(out, "*competitor_market_summary*.md")
    if md_file:
        upsert(parent_id, "Competitor Market Summary",
               _info(f"Competitor market summary. Updated {ts}.") +
               _md_to_html(md_file.read_text(encoding="utf-8")), dry_run)

    # ── Profiles folder ──
    profiles_id = ensure_folder_page(parent_id, "Profiles (Competitors)", dry_run)
    svc_cols = ["service_name", "category", "customer_maturity", "description",
                "clients", "industries", "pricing_signals", "priority_tier", "priority_score"]
    # Sort by avg priority score descending for numbering
    comp_order = sorted(by_comp.items(),
                        key=lambda x: pd.to_numeric(x[1]["priority_score"], errors="coerce").fillna(0).mean(),
                        reverse=True)
    for n, (comp, rows) in enumerate(comp_order, start=1):
        clean = _clean_name(comp)
        page_title = f"{n:02d}. {clean}"
        rows_sorted = rows.sort_values("priority_score", ascending=False,
                                       key=lambda s: pd.to_numeric(s, errors="coerce").fillna(0))
        body = _info(f"{clean} — {len(rows)} AI services scored. Updated {ts}.")
        high = rows_sorted[rows_sorted["priority_tier"].str.lower() == "high"]
        if not high.empty:
            body += _h2(f"High Priority ({len(high)})")
            body += _table(high[[c for c in svc_cols if c in high.columns]])
        rest = rows_sorted[rows_sorted["priority_tier"].str.lower() != "high"]
        if not rest.empty:
            body += _h2(f"Other Services ({len(rest)})")
            body += _table(rest[[c for c in svc_cols if c in rest.columns]])
        upsert(profiles_id, page_title, body, dry_run)


def publish_clients(parent_id: str, dry_run: bool) -> None:
    """Research/Clients & Prospects/ — Index, Vendor Usage, Potential Matches, Profiles/"""
    out = SCRIPT_DIR / "client_output"
    csv = _latest(out, "client_intel_all_priority*.csv")
    if not csv:
        log.warning("Client intel CSV not found — skipping."); return

    df = pd.read_csv(csv, dtype=str).fillna("")
    ts = _ts()
    client_meta = _load_client_meta(SCRIPT_DIR / "client_sites")
    by_source = {s: g for s, g in df.groupby("source")}

    def _client_name(src: str) -> str:
        # Try client_meta first (has proper names like "BMW Group")
        meta = client_meta.get(src, {})
        if meta.get("client_name"):
            return meta["client_name"]
        return _clean_name(src)

    # ── Client Index ──
    idx_rows = []
    for src, rows in sorted(by_source.items()):
        vendors = Counter(v.strip() for r in rows["vendor_tools"].tolist()
                          for v in str(r).split(",") if v.strip()).most_common(3)
        budget = next((r for r in rows["budget_mention"].tolist() if str(r).strip()), "—")
        maturity = rows["maturity"].value_counts().index[0] if not rows["maturity"].empty else "—"
        meta = client_meta.get(src, {})
        idx_rows.append({
            "Client": _client_name(src),
            "Type": meta.get("client_type", "").title(),
            "# Signals": len(rows),
            "AI Maturity": maturity,
            "Top Vendors": ", ".join(v for v, _ in vendors),
            "Budget Signal": str(budget)[:100],
            "High Priority": (rows["priority_tier"].str.lower() == "high").sum(),
        })
    upsert(parent_id, "Client Index",
           _info(f"{len(by_source)} clients/prospects. {len(df)} AI signals. Updated {ts}.") +
           _table(pd.DataFrame(idx_rows)), dry_run)

    # ── Vendor & Tool Usage ──
    all_vendors = [v.strip() for r in df["vendor_tools"].tolist()
                   for v in str(r).split(",") if v.strip()]
    v_df = pd.DataFrame(Counter(all_vendors).most_common(40), columns=["Vendor / Tool", "# Client Mentions"])
    upsert(parent_id, "Client Vendor & Tool Usage",
           _info(f"AI vendors and tools confirmed in use across clients. Updated {ts}.") +
           _table(v_df), dry_run)

    # ── Potential Client Matches ──
    md_file = _latest(out, "*potential_clients*.md")
    if md_file:
        upsert(parent_id, "Client Potential Matches",
               _info(f"Potential clients matched to existing client profiles. Updated {ts}.") +
               _md_to_html(md_file.read_text(encoding="utf-8")), dry_run)

    # ── Profiles folder ──
    profiles_id = ensure_folder_page(parent_id, "Profiles (Clients)", dry_run)
    sig_cols = ["signal_type", "title", "vendor_tools", "budget_mention",
                "strategic_intent", "maturity", "source_type", "priority_tier"]
    # Sort by # high-priority signals descending for numbering
    client_order = sorted(by_source.items(),
                          key=lambda x: (x[1]["priority_tier"].str.lower() == "high").sum(),
                          reverse=True)
    for n, (src, rows) in enumerate(client_order, start=1):
        clean = _client_name(src)
        page_title = f"{n:02d}. {clean}"
        rows_sorted = rows.sort_values("priority_score", ascending=False,
                                       key=lambda s: pd.to_numeric(s, errors="coerce").fillna(0))
        meta = client_meta.get(src, {})
        body = _info(f"{clean} ({meta.get('client_type','').title()}) — {len(rows)} AI signals. Updated {ts}.")
        high = rows_sorted[rows_sorted["priority_tier"].str.lower() == "high"]
        if not high.empty:
            body += _h2(f"High Priority Signals ({len(high)})")
            body += _table(high[[c for c in sig_cols if c in high.columns]])
        rest = rows_sorted[rows_sorted["priority_tier"].str.lower() != "high"]
        if not rest.empty:
            body += _h2(f"Other Signals ({len(rest)})")
            body += _table(rest[[c for c in sig_cols if c in rest.columns]])
        upsert(profiles_id, page_title, body, dry_run)


def publish_market_trends(parent_id: str, dry_run: bool) -> None:
    """Research/Market & Trends/ — AI Consulting Overview, Pricing Analysis, Legacy Brief"""
    ts = _ts()

    # AI Consulting Market Overview
    ai_out = SCRIPT_DIR / "ai_output"
    csv = _latest(ai_out, "ai_consulting_all_priority_*.csv")
    if csv:
        df = pd.read_csv(csv, dtype=str).fillna("")
        by_source = {s: g for s, g in df.groupby("source")}
        from lib.pipelines.ai_consulting import SERVICE_TYPES
        body = _info(f"{len(by_source)} companies. {len(df)} services. Updated {ts}.")
        for stype in SERVICE_TYPES:
            subset = df[df["service_type"].str.contains(stype, case=False, na=False)]
            if subset.empty:
                continue
            cols = ["source", "service_name", "description", "delivery_format",
                    "clients", "industries", "pricing_explicit", "inferred_price_range", "priority_tier"]
            body += _h2(f"{stype} ({len(subset)})")
            body += _table(subset[[c for c in cols if c in subset.columns]], max_rows=80)
        upsert(parent_id, "AI Consulting Market Overview", body, dry_run)

    # Pricing & Format Analysis
    md_file = _latest(ai_out, "*ai_market_summary*.md")
    if md_file:
        upsert(parent_id, "AI Consulting Pricing & Formats",
               _info(f"Delivery formats, pricing models, and price ranges. Updated {ts}.") +
               _md_to_html(md_file.read_text(encoding="utf-8")), dry_run)

    # Legacy Modernisation Brief
    leg_out = SCRIPT_DIR / "legacy_output"
    md_file = _latest(leg_out, "*legacy*brief*.md")
    if md_file:
        upsert(parent_id, "Legacy Modernisation Brief",
               _info(f"Research brief answering the 4 pillar questions. Updated {ts}.") +
               _md_to_html(md_file.read_text(encoding="utf-8")), dry_run)


def publish_research(dry_run: bool = False) -> None:
    """Publish the full Research/ folder structure."""
    ts = _ts()
    log.info("  Building Research/Competitors/")
    comp_id = ensure_folder_page(RESEARCH_ID, "Competitors", dry_run)
    publish_competitors(comp_id, dry_run)

    log.info("  Building Research/Clients & Prospects/")
    client_id = ensure_folder_page(RESEARCH_ID, "Clients & Prospects", dry_run)
    publish_clients(client_id, dry_run)

    log.info("  Building Research/Market & Trends/")
    market_id = ensure_folder_page(RESEARCH_ID, "Market & Trends", dry_run)
    publish_market_trends(market_id, dry_run)


# ══════════════════════════════════════════════════════════════════════════════
# STRATEGY FOLDER PUBLISHERS
# ══════════════════════════════════════════════════════════════════════════════

# Category → strategy folder mapping
_COMP_CATEGORY_MAP = {
    "ai_advisory":    ["AI Advisory & Readiness"],
    "ai_engineering": ["AI Engineering & Automation", "AI Platforms & Agents"],
    "ai_solutions":   ["AI-powered Solutions & New Revenue Models", "Talent & Staffing"],
}
_LEGACY_STRATEGY = "ai_engineering"   # legacy modernisation → AI Engineering folder

# Client signal filters per strategy
_CLIENT_SIGNAL_MAP = {
    "ai_advisory":    {"maturity": {"exploring", "piloting"}, "signal_type": {"strategic_intent"}},
    "ai_engineering": {"maturity": {"scaling", "embedded"},   "signal_type": {"vendor_tool"}},
    "ai_solutions":   {"signal_type": {"ai_initiative", "budget_signal"}},
}

_STRATEGY_LABELS = {
    "ai_advisory":    "AI Advisory & Readiness",
    "ai_engineering": "AI Engineering, Automation & Platforms",
    "ai_solutions":   "AI Powered Solutions & New Revenue Models",
}


def _filter_competitors(df: pd.DataFrame, strategy: str) -> pd.DataFrame:
    cats = _COMP_CATEGORY_MAP.get(strategy, [])
    mask = df["category"].apply(lambda c: any(cat.lower() in c.lower() for cat in cats))
    return df[mask]


def _filter_clients(df: pd.DataFrame, strategy: str) -> pd.DataFrame:
    rules = _CLIENT_SIGNAL_MAP.get(strategy, {})
    mask = pd.Series([False] * len(df), index=df.index)
    if "maturity" in rules:
        mask |= df["maturity"].str.lower().isin(rules["maturity"])
    if "signal_type" in rules:
        mask |= df["signal_type"].str.lower().isin(rules["signal_type"])
    return df[mask]


def _pricing_signals(df: pd.DataFrame) -> str:
    sigs = [r for r in df.get("pricing_signals", pd.Series(dtype=str)).tolist()
            if str(r).strip() and str(r).strip().lower() not in ("", "nan")]
    if not sigs:
        return "<p><em>No explicit pricing signals found in scraped content for this service line.</em></p>"
    items = "".join(f"<li>{s}</li>" for s in sigs[:20])
    return f"<ul>{items}</ul>"


def publish_strategy_folder(folder_id: str, strategy: str,
                             comp_df: pd.DataFrame, client_df: pd.DataFrame,
                             ai_df: pd.DataFrame, legacy_df: pd.DataFrame,
                             dry_run: bool) -> None:
    label = _STRATEGY_LABELS[strategy]
    ts = _ts()

    # ── Service Line Summary ──
    comp_subset   = _filter_competitors(comp_df, strategy)
    client_subset = _filter_clients(client_df, strategy)
    ai_subset     = ai_df  # AI consulting is relevant to all strategies

    # Count services and top competitors
    top_comps = comp_subset.groupby("competitor").size().sort_values(ascending=False).head(5)
    top_vendors = Counter(v.strip() for r in client_subset.get("vendor_tools", pd.Series(dtype=str)).tolist()
                          for v in str(r).split(",") if v.strip()).most_common(5)

    summary_body = _info(f"{label} — service line summary. Updated {ts}.")
    summary_body += _h2("Overview")
    summary_body += (
        f"<p><strong>{len(comp_subset)}</strong> competitor services mapped to this service line across "
        f"<strong>{comp_subset['competitor'].nunique()}</strong> competitors. "
        f"<strong>{len(client_subset)}</strong> client AI signals relevant to this space. "
        f"<strong>{len(ai_df)}</strong> AI consulting market services benchmarked.</p>"
    )
    if not top_comps.empty:
        summary_body += _h2("Most Active Competitors in This Space")
        tc_df = pd.DataFrame({"Competitor": [_clean_name(c) for c in top_comps.index],
                               "# Services": top_comps.values})
        summary_body += _table(tc_df)
    if top_vendors:
        summary_body += _h2("AI Vendors Clients Are Using")
        tv_df = pd.DataFrame(top_vendors, columns=["Vendor / Tool", "# Client Mentions"])
        summary_body += _table(tv_df)
    if strategy == _LEGACY_STRATEGY and not legacy_df.empty:
        high_leg = legacy_df[legacy_df["priority_tier"].str.lower() == "high"]
        summary_body += _h2(f"Legacy Modernisation — High Priority ({len(high_leg)} services)")
        leg_cols = ["source", "service_name", "category", "maturity_level", "priority_tier"]
        summary_body += _table(high_leg[[c for c in leg_cols if c in high_leg.columns]], max_rows=20)
    upsert(folder_id, f"Service Line Summary — {label}", summary_body, dry_run)

    # ── Top Competitors ──
    if not comp_subset.empty:
        cols = ["competitor", "service_name", "category", "description",
                "clients", "industries", "priority_tier", "priority_score"]
        comp_sorted = comp_subset.sort_values("priority_score", ascending=False,
                                              key=lambda s: pd.to_numeric(s, errors="coerce").fillna(0))
        comp_sorted["competitor"] = comp_sorted["competitor"].apply(_clean_name)
        body = _info(f"Competitor services in {label}. {len(comp_subset)} services. Updated {ts}.")
        body += _table(comp_sorted[[c for c in cols if c in comp_sorted.columns]])
        upsert(folder_id, f"Top Competitors — {label}", body, dry_run)

    # ── Target Clients ──
    if not client_subset.empty:
        sig_cols = ["source", "signal_type", "title", "vendor_tools",
                    "budget_mention", "maturity", "priority_tier"]
        client_sorted = client_subset.sort_values("priority_score", ascending=False,
                                                   key=lambda s: pd.to_numeric(s, errors="coerce").fillna(0))
        client_sorted = client_sorted.copy()
        client_sorted["source"] = client_sorted["source"].apply(_clean_name)
        body = _info(f"Clients with AI signals relevant to {label}. {len(client_subset)} signals. Updated {ts}.")
        body += _table(client_sorted[[c for c in sig_cols if c in client_sorted.columns]])
        upsert(folder_id, f"Target Clients — {label}", body, dry_run)

    # ── Pricing Benchmarks ──
    price_body = _info(f"Pricing signals for {label}. Updated {ts}.")
    price_body += _h2("Competitor Pricing Signals")
    price_body += _pricing_signals(comp_subset)
    if not ai_subset.empty:
        price_body += _h2("AI Consulting Market — Price Ranges")
        price_cols = ["source", "service_name", "service_type", "delivery_format",
                      "pricing_explicit", "inferred_price_range", "pricing_model"]
        price_body += _table(ai_subset[[c for c in price_cols if c in ai_subset.columns]], max_rows=50)
    upsert(folder_id, f"Pricing Benchmarks — {label}", price_body, dry_run)


def publish_strategies(dry_run: bool = False) -> None:
    """Publish all three strategy folders."""
    # Load all data once
    comp_csv   = _latest(SCRIPT_DIR / "output",        "competitor_all_priority_*.csv")
    client_csv = _latest(SCRIPT_DIR / "client_output", "client_intel_all_priority*.csv")
    ai_csv     = _latest(SCRIPT_DIR / "ai_output",     "ai_consulting_all_priority_*.csv")
    legacy_csv = _latest(SCRIPT_DIR / "legacy_output", "legacy_all_priority*.csv")

    comp_df   = pd.read_csv(comp_csv,   dtype=str).fillna("") if comp_csv   else pd.DataFrame()
    client_df = pd.read_csv(client_csv, dtype=str).fillna("") if client_csv else pd.DataFrame()
    ai_df     = pd.read_csv(ai_csv,     dtype=str).fillna("") if ai_csv     else pd.DataFrame()
    legacy_df = pd.read_csv(legacy_csv, dtype=str).fillna("") if legacy_csv else pd.DataFrame()

    for strategy, folder_id in _STRATEGY_FOLDER_IDS.items():
        label = _STRATEGY_LABELS[strategy]
        log.info(f"  Building {label}/")
        publish_strategy_folder(folder_id, strategy,
                                comp_df, client_df, ai_df, legacy_df, dry_run)
        log.info(f"  ✓ {label} done.")


# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

def run(mode: str = "all", dry_run: bool = False) -> None:
    for var, val in [("CONFLUENCE_BASE_URL", BASE_URL), ("CONFLUENCE_EMAIL", EMAIL),
                     ("CONFLUENCE_API_TOKEN", TOKEN), ("CONFLUENCE_SPACE_KEY", SPACE_KEY),
                     ("CONFLUENCE_RESEARCH_PAGE_ID", RESEARCH_ID)]:
        if not val:
            raise EnvironmentError(f"{var} not set in .env")

    if mode in ("all", "research"):
        log.info(f"\n{'─'*55}\n  Research Folder\n{'─'*55}")
        publish_research(dry_run)
        log.info("  ✓ Research folder done.")

    if mode in ("all", "strategy"):
        log.info(f"\n{'─'*55}\n  Strategy Folders\n{'─'*55}")
        publish_strategies(dry_run)

    log.info("\nConfluence publish complete.")


def main():
    parser = argparse.ArgumentParser(description="Publish pipeline outputs to Confluence")
    parser.add_argument("--mode", default="all", choices=["all", "research", "strategy"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(mode=args.mode, dry_run=args.dry_run)


if __name__ == "__main__":
    main()