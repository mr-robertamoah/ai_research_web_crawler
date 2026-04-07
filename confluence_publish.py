"""
confluence_publish.py — publishes pipeline outputs to Confluence Research folder.

Structure (per-entity pages for easy Rovo querying):
  Research/
    Competitor Intelligence/
      Index                         ← summary table + avg scores per competitor
      <Competitor Name>             ← one page per competitor (services + scores)
      Hypothesis Tracker            ← verdicts per hypothesis per competitor
      Market Summary                ← narrative MD
    AI Consulting Market/
      Index                         ← summary: # services per company, top formats
      AI Readiness                  ← one page per service type
      Use Case Discovery
      Workshops
      Implementation
      Governance
      Other
      Named Clients & Industries
    Legacy Modernisation/
      Index                         ← summary table
      Research Brief                ← narrative MD
    Client Intelligence/
      Index                         ← per-client summary (maturity, top vendors, budget)
      <Client Name>                 ← one page per client with their signals
      Vendor & Tool Usage
      Potential Client Matches

Usage:
  python confluence_publish.py                    # publish all pipelines
  python confluence_publish.py --mode competitor  # one pipeline
  python confluence_publish.py --dry-run          # preview only

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
SPACE_ID: str = ""   # filled on first use

AUTH    = (EMAIL, TOKEN)
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


# ── API HELPERS ───────────────────────────────────────────────────────────────
def _get_space_id() -> str:
    global SPACE_ID
    if SPACE_ID:
        return SPACE_ID
    resp = requests.get(f"{BASE_URL}/api/v2/spaces?keys={SPACE_KEY}",
                        auth=AUTH, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    SPACE_ID = resp.json()["results"][0]["id"]
    return SPACE_ID


def _find_child(parent_id: str, title: str) -> str | None:
    if not parent_id or not parent_id.isdigit():
        return None
    try:
        data = requests.get(f"{BASE_URL}/rest/api/content/{parent_id}/child/page?limit=100",
                            auth=AUTH, headers=HEADERS, timeout=15).json()
        for p in data.get("results", []):
            if p["title"] == title:
                return p["id"]
    except Exception:
        pass
    resp = requests.get(f"{BASE_URL}/rest/api/content/search",
                        params={"cql": f'title="{title}" AND space="{SPACE_KEY}" AND ancestor={parent_id}', "limit": 5},
                        auth=AUTH, headers=HEADERS, timeout=15)
    if resp.ok:
        for p in resp.json().get("results", []):
            if p["title"] == title:
                return p["id"]
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
        log.error(f"  Create failed for '{title}': {resp.status_code} {resp.text[:300]}")
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
    requests.put(f"{BASE_URL}/rest/api/content/{page_id}", auth=AUTH,
                 headers=HEADERS, json=payload, timeout=15).raise_for_status()
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
    body = f"<p><em>{title} — research folder. Last updated {_ts()}.</em></p>"
    return upsert(parent_id, title, body, dry_run)


# ── HTML BUILDERS ─────────────────────────────────────────────────────────────
def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M UTC")


def _info(text: str) -> str:
    return (f'<ac:structured-macro ac:name="info">'
            f'<ac:rich-text-body><p>{text}</p></ac:rich-text-body>'
            f'</ac:structured-macro>')


def _h2(text: str) -> str:
    return f"<h2>{text}</h2>"


def _priority_span(val: str) -> str:
    v = str(val).lower()
    if v == "high":   return f'<span style="color:#276221;font-weight:bold">{val}</span>'
    if v == "medium": return f'<span style="color:#9C6500;font-weight:bold">{val}</span>'
    if v == "low":    return f'<span style="color:#9C0006">{val}</span>'
    if v == "yes":    return f'<span style="color:#276221;font-weight:bold">{val}</span>'
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
            if "priority" in col_l or "alert" in col_l or "innovation" in col_l:
                cells.append(f"<td>{_priority_span(val)}</td>")
            elif col_l == "url" or col_l == "source_url":
                cells.append(f'<td><a href="{val}">{val[:60]}…</a></td>' if len(val) > 60 else f'<td><a href="{val}">{val}</a></td>')
            else:
                cells.append(f"<td>{val}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    rows.append("</tbody></table>")
    return "\n".join(rows)


def _md_to_html(md: str) -> str:
    lines, out = md.splitlines(), []
    for line in lines:
        if line.startswith("# "):    out.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "): out.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):out.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("> "): out.append(f"<blockquote><p>{line[2:]}</p></blockquote>")
        elif line.startswith("---"): out.append("<hr/>")
        elif line.strip() == "":    out.append("<br/>")
        elif line.startswith("| ") and "|" in line: out.append(line)  # handle below
        else:
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            line = re.sub(r"\*(.+?)\*", r"<em>\1</em>", line)
            out.append(f"<p>{line}</p>")
    result = "\n".join(out)
    def _conv_table(m):
        rows = [r for r in m.group(0).splitlines() if not re.match(r"^\|[-| ]+\|$", r)]
        html = ["<table><tbody>"]
        for i, row in enumerate(rows):
            cells = [c.strip() for c in row.strip("|").split("|")]
            tag = "th" if i == 0 else "td"
            html.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
        html.append("</tbody></table>")
        return "\n".join(html)
    return re.sub(r"(\|.+\|\n)+", _conv_table, result)


def _latest(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def _safe_title(name: str) -> str:
    """Sanitise a name for use as a Confluence page title."""
    return re.sub(r"[^\w\s\-&()]", "", name).strip()[:100]


# ── COMPETITOR ────────────────────────────────────────────────────────────────
def publish_competitor(folder_id: str, dry_run: bool) -> None:
    out = SCRIPT_DIR / "output"
    csv = _latest(out, "competitor_all_priority_*.csv")
    if not csv:
        log.warning("Competitor CSV not found — skipping."); return

    df = pd.read_csv(csv, dtype=str).fillna("")
    ts = _ts()
    by_comp: dict[str, pd.DataFrame] = {c: g for c, g in df.groupby("competitor")}

    # Index page — one row per competitor
    idx_rows = []
    for comp, rows in sorted(by_comp.items()):
        scores = pd.to_numeric(rows["priority_score"], errors="coerce").fillna(0)
        idx_rows.append({
            "Competitor": comp,
            "# Services": len(rows),
            "Avg Priority Score": round(scores.mean(), 1),
            "High": (rows["priority_tier"].str.lower() == "high").sum(),
            "Medium": (rows["priority_tier"].str.lower() == "medium").sum(),
            "Low": (rows["priority_tier"].str.lower() == "low").sum(),
            "Top Categories": ", ".join(rows["category"].value_counts().head(3).index.tolist()),
        })
    idx_df = pd.DataFrame(idx_rows).sort_values("Avg Priority Score", ascending=False)
    upsert(folder_id, "Competitor Intelligence — Index",
           _table(idx_df), dry_run)

    # One page per competitor
    svc_cols = ["service_name", "category", "customer_maturity", "description",
                "clients", "industries", "pricing_signals", "priority_tier", "priority_score"]
    for comp, rows in sorted(by_comp.items()):
        rows_sorted = rows.sort_values("priority_score", ascending=False,
                                       key=lambda s: pd.to_numeric(s, errors="coerce").fillna(0))
        body = _info(f"{comp} — {len(rows)} AI services scored. Updated {ts}.")
        # High priority first
        high = rows_sorted[rows_sorted["priority_tier"].str.lower() == "high"]
        if not high.empty:
            body += _h2(f"🔴 High Priority ({len(high)})")
            body += _table(high[[c for c in svc_cols if c in high.columns]])
        rest = rows_sorted[rows_sorted["priority_tier"].str.lower() != "high"]
        if not rest.empty:
            body += _h2(f"Other Services ({len(rest)})")
            body += _table(rest[[c for c in svc_cols if c in rest.columns]])
        upsert(folder_id, _safe_title(comp), body, dry_run)

    # Hypothesis tracker
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
                             "Competitor": comp, "Verdict": h_val.get("verdict",""),
                             "Evidence For": h_val.get("evidence_for","")[:200],
                             "Evidence Against": h_val.get("evidence_against","")[:200]})
        upsert(folder_id, "Hypothesis Tracker",
               _info(f"Strategic hypothesis verdicts per competitor. Updated {ts}.") +
               _table(pd.DataFrame(rows)), dry_run)

    # Market summary
    md_file = _latest(out, "*competitor_market_summary*.md")
    if md_file:
        upsert(folder_id, "Market Summary",
               _info(f"Competitor market summary. Updated {ts}.") +
               _md_to_html(md_file.read_text(encoding="utf-8")), dry_run)


# ── AI CONSULTING ─────────────────────────────────────────────────────────────
def publish_ai_consulting(folder_id: str, dry_run: bool) -> None:
    out = SCRIPT_DIR / "ai_output"
    csv = _latest(out, "ai_consulting_all_priority_*.csv")
    if not csv:
        log.warning("AI consulting CSV not found — skipping."); return

    df = pd.read_csv(csv, dtype=str).fillna("")
    ts = _ts()
    svc_cols = ["source", "service_name", "description", "delivery_format", "duration",
                "target_audience", "clients", "industries",
                "pricing_explicit", "inferred_price_range", "pricing_model", "priority_tier"]

    # Index — one row per company
    by_source = {s: g for s, g in df.groupby("source")}
    idx_rows = []
    for src, rows in sorted(by_source.items()):
        idx_rows.append({
            "Company": src,
            "# Services": len(rows),
            "Service Types": ", ".join(rows["service_type"].value_counts().head(3).index.tolist()),
            "Top Format": rows["delivery_format"].value_counts().index[0] if not rows["delivery_format"].empty else "",
            "Clients Mentioned": ", ".join(c.strip() for r in rows.get("clients", rows.get("client_wins", pd.Series(dtype=str))).tolist()
                                           for c in str(r).split(",") if c.strip())[:100],
        })
    upsert(folder_id, "AI Consulting Market — Index",
           _info(f"{len(by_source)} companies. {len(df)} services. Updated {ts}.") +
           _table(pd.DataFrame(idx_rows)), dry_run)

    # One page per service type
    from lib.pipelines.ai_consulting import SERVICE_TYPES
    for stype in SERVICE_TYPES:
        subset = df[df["service_type"].str.contains(stype, case=False, na=False)]
        if subset.empty:
            continue
        body = _info(f"{stype} — {len(subset)} services across {subset['source'].nunique()} companies. Updated {ts}.")
        body += _table(subset[[c for c in svc_cols if c in subset.columns]])
        upsert(folder_id, f"AI Consulting — {stype}", body, dry_run)

    # Named clients & industries
    all_clients    = [c.strip() for r in df.get("clients", pd.Series(dtype=str)).tolist()
                      for c in str(r).split(",") if c.strip()]
    all_industries = [i.strip() for r in df.get("industries", pd.Series(dtype=str)).tolist()
                      for i in str(r).split(",") if i.strip()]
    c_df = pd.DataFrame(Counter(all_clients).most_common(50), columns=["Client", "# Mentions"])
    i_df = pd.DataFrame(Counter(all_industries).most_common(30), columns=["Industry", "# Mentions"])
    upsert(folder_id, "Named Clients & Industries",
           _info(f"Named clients and industries found across AI consulting pages. Updated {ts}.") +
           _h2("Top Clients") + _table(c_df) +
           _h2("Top Industries") + _table(i_df), dry_run)

    # Pricing & format analysis
    md_file = _latest(out, "*ai_market_summary*.md")
    if md_file:
        upsert(folder_id, "Pricing & Format Analysis",
               _info(f"Delivery formats, pricing models, and price ranges. Updated {ts}.") +
               _md_to_html(md_file.read_text(encoding="utf-8")), dry_run)


# ── LEGACY ────────────────────────────────────────────────────────────────────
def publish_legacy(folder_id: str, dry_run: bool) -> None:
    out = SCRIPT_DIR / "legacy_output"
    csv = _latest(out, "legacy_all_priority*.csv")
    if not csv:
        log.warning("Legacy CSV not found — skipping."); return

    df = pd.read_csv(csv, dtype=str).fillna("")
    ts = _ts()
    svc_cols = ["source", "service_name", "type", "category", "maturity_level",
                "description", "evidence", "priority_tier", "priority_score"]

    # Index — summary per source
    by_source = {s: g for s, g in df.groupby("source")}
    idx_rows = [{"Source": src, "# Services": len(rows),
                 "High Priority": (rows["priority_tier"].str.lower() == "high").sum(),
                 "Top Category": rows["category"].value_counts().index[0] if not rows["category"].empty else ""}
                for src, rows in sorted(by_source.items())]
    upsert(folder_id, "Legacy Modernisation — Index",
           _info(f"{len(by_source)} sources. {len(df)} services/tools scored. Updated {ts}.") +
           _table(pd.DataFrame(idx_rows)), dry_run)

    # High priority services
    high = df[df["priority_tier"].str.lower() == "high"].sort_values(
        "priority_score", ascending=False, key=lambda s: pd.to_numeric(s, errors="coerce").fillna(0))
    upsert(folder_id, "High Priority Services",
           _info(f"{len(high)} high-priority legacy modernisation services. Updated {ts}.") +
           _table(high[[c for c in svc_cols if c in high.columns]]), dry_run)

    # All services
    upsert(folder_id, "All Services & Tools",
           _info(f"All {len(df)} legacy modernisation services and tools. Updated {ts}.") +
           _table(df[[c for c in svc_cols if c in df.columns]]), dry_run)

    # Research brief
    md_file = _latest(out, "*legacy*brief*.md")
    if md_file:
        upsert(folder_id, "Research Brief",
               _info(f"Research brief answering the 4 pillar questions. Updated {ts}.") +
               _md_to_html(md_file.read_text(encoding="utf-8")), dry_run)


# ── CLIENT INTEL ──────────────────────────────────────────────────────────────
def publish_client_intel(folder_id: str, dry_run: bool) -> None:
    out = SCRIPT_DIR / "client_output"
    csv = _latest(out, "client_intel_all_priority*.csv")
    if not csv:
        log.warning("Client intel CSV not found — skipping."); return

    df = pd.read_csv(csv, dtype=str).fillna("")
    ts = _ts()
    sig_cols = ["signal_type", "title", "vendor_tools", "budget_mention",
                "strategic_intent", "maturity", "source_type", "priority_tier"]
    by_source = {s: g for s, g in df.groupby("source")}

    # Index — one row per client
    idx_rows = []
    for src, rows in sorted(by_source.items()):
        vendors = Counter(v.strip() for r in rows["vendor_tools"].tolist()
                          for v in str(r).split(",") if v.strip()).most_common(3)
        budget  = next((r for r in rows["budget_mention"].tolist() if str(r).strip()), "—")
        maturity = rows["maturity"].value_counts().index[0] if not rows["maturity"].empty else "—"
        idx_rows.append({
            "Client": src,
            "# Signals": len(rows),
            "AI Maturity": maturity,
            "Top Vendors": ", ".join(v for v, _ in vendors),
            "Budget Signal": str(budget)[:100],
            "High Priority": (rows["priority_tier"].str.lower() == "high").sum(),
        })
    upsert(folder_id, "Client Intelligence — Index",
           _info(f"{len(by_source)} clients/prospects analysed. {len(df)} AI signals. Updated {ts}.") +
           _table(pd.DataFrame(idx_rows)), dry_run)

    # One page per client
    for src, rows in sorted(by_source.items()):
        rows_sorted = rows.sort_values("priority_score", ascending=False,
                                       key=lambda s: pd.to_numeric(s, errors="coerce").fillna(0))
        body = _info(f"{src} — {len(rows)} AI signals. Updated {ts}.")
        high = rows_sorted[rows_sorted["priority_tier"].str.lower() == "high"]
        if not high.empty:
            body += _h2(f"🔴 High Priority Signals ({len(high)})")
            body += _table(high[[c for c in sig_cols if c in high.columns]])
        rest = rows_sorted[rows_sorted["priority_tier"].str.lower() != "high"]
        if not rest.empty:
            body += _h2(f"Other Signals ({len(rest)})")
            body += _table(rest[[c for c in sig_cols if c in rest.columns]])
        upsert(folder_id, _safe_title(src), body, dry_run)

    # Vendor & tool usage
    all_vendors = [v.strip() for r in df["vendor_tools"].tolist()
                   for v in str(r).split(",") if v.strip()]
    v_df = pd.DataFrame(Counter(all_vendors).most_common(40), columns=["Vendor / Tool", "# Client Mentions"])
    upsert(folder_id, "Vendor & Tool Usage",
           _info(f"AI vendors and tools confirmed in use across clients. Updated {ts}.") +
           _table(v_df), dry_run)

    # Potential client matches
    md_file = _latest(out, "*potential_clients*.md")
    if md_file:
        upsert(folder_id, "Potential Client Matches",
               _info(f"Potential clients matched to existing client profiles. Updated {ts}.") +
               _md_to_html(md_file.read_text(encoding="utf-8")), dry_run)


# ── MAIN ──────────────────────────────────────────────────────────────────────
_PIPELINES = {
    "competitor":    ("Competitor Intelligence",  publish_competitor),
    "ai_consulting": ("AI Consulting Market",     publish_ai_consulting),
    "legacy":        ("Legacy Modernisation",     publish_legacy),
    "client_intel":  ("Client Intelligence",      publish_client_intel),
}


def run(mode: str = "all", dry_run: bool = False) -> None:
    for var, val in [("CONFLUENCE_BASE_URL", BASE_URL), ("CONFLUENCE_EMAIL", EMAIL),
                     ("CONFLUENCE_API_TOKEN", TOKEN), ("CONFLUENCE_SPACE_KEY", SPACE_KEY),
                     ("CONFLUENCE_RESEARCH_PAGE_ID", RESEARCH_ID)]:
        if not val:
            raise EnvironmentError(f"{var} not set in .env")

    pipelines = _PIPELINES if mode == "all" else {mode: _PIPELINES[mode]}
    for key, (folder_title, publisher) in pipelines.items():
        log.info(f"\n{'─'*55}\n  {folder_title}\n{'─'*55}")
        folder_id = ensure_folder_page(RESEARCH_ID, folder_title, dry_run)
        publisher(folder_id, dry_run)
        log.info(f"  ✓ {folder_title} done.")
    log.info("\nAll pipelines published to Confluence.")


def main():
    parser = argparse.ArgumentParser(description="Publish pipeline outputs to Confluence")
    parser.add_argument("--mode", default="all",
                        choices=["all", "competitor", "ai_consulting", "legacy", "client_intel"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(mode=args.mode, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
