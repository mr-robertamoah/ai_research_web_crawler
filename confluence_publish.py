"""
confluence_publish.py — publishes pipeline outputs to Confluence.

Creates/updates the page structure under the Research folder:
  Research/
    Competitor Intelligence/
      Competitor Services — All
      Competitor Services — High Priority
      Hypothesis Tracker
      Market Summary
    AI Consulting Market/
      Services — All
      Services — By Service Type
      Pricing & Format Analysis
      Named Clients & Industries
    Legacy Modernisation/
      Services & Tools — All
      Research Brief
    Client Intelligence/
      AI Signals — All
      Budget Signals
      Vendor & Tool Usage
      Potential Client Matches

Usage:
  python confluence_publish.py                    # publish all pipelines
  python confluence_publish.py --mode competitor  # publish one pipeline
  python confluence_publish.py --dry-run          # show what would be published

Environment variables (all required, set in .env):
  CONFLUENCE_BASE_URL          e.g. https://amali-tech.atlassian.net/wiki
  CONFLUENCE_EMAIL             your Atlassian account email
  CONFLUENCE_API_TOKEN         API token from id.atlassian.com
  CONFLUENCE_SPACE_KEY         e.g. AH
  CONFLUENCE_RESEARCH_PAGE_ID  numeric ID of the Research parent page
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-8s  %(message)s", datefmt="%H:%M:%S")
log = logging.getLogger("confluence")

# ── CONFIG ────────────────────────────────────────────────────────────────────
# Read directly from .env to avoid shell truncation of tokens containing '='
def _read_env(key: str) -> str:
    val = os.getenv(key, "")
    if val:
        return val
    env_file = Path(__file__).parent / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if line.startswith(f"{key}="):
                return line.split("=", 1)[1].strip()
    return ""

# ── CONFIG ────────────────────────────────────────────────────────────────────
BASE_URL   = _read_env("CONFLUENCE_BASE_URL").rstrip("/")
EMAIL      = _read_env("CONFLUENCE_EMAIL")
TOKEN      = _read_env("CONFLUENCE_API_TOKEN")
SPACE_KEY  = _read_env("CONFLUENCE_SPACE_KEY")
RESEARCH_ID= _read_env("CONFLUENCE_RESEARCH_PAGE_ID")
SCRIPT_DIR = Path(__file__).parent.resolve()

AUTH    = (EMAIL, TOKEN)
HEADERS = {"Accept": "application/json", "Content-Type": "application/json"}


# ── CONFLUENCE API HELPERS ────────────────────────────────────────────────────
def _get(path: str) -> dict:
    resp = requests.get(f"{BASE_URL}/rest/api{path}", auth=AUTH, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json()


def _find_child(parent_id: str, title: str) -> str | None:
    """Return page ID if a child with this title exists under parent_id, else None."""
    if not parent_id or not parent_id.isdigit():
        return None
    try:
        data = _get(f"/content/{parent_id}/child/page?limit=50")
        for page in data.get("results", []):
            if page["title"] == title:
                return page["id"]
        return None
    except requests.exceptions.HTTPError:
        pass
    data = _get(f'/content/search?cql=title="{title}" AND space="{SPACE_KEY}" AND ancestor={parent_id}&limit=5')
    for page in data.get("results", []):
        if page["title"] == title:
            return page["id"]
    return None


def _create_page(parent_id: str, title: str, body: str) -> str:
    payload = {
        "type": "page",
        "title": title,
        "space": {"key": SPACE_KEY},
        "ancestors": [{"id": parent_id}],
        "body": {"storage": {"value": body, "representation": "storage"}},
    }
    resp = requests.post(f"{BASE_URL}/rest/api/content", auth=AUTH,
                         headers=HEADERS, json=payload, timeout=15)
    resp.raise_for_status()
    page_id = resp.json()["id"]
    log.info(f"  Created: '{title}' (id={page_id})")
    return page_id


def _update_page(page_id: str, title: str, body: str) -> None:
    current = _get(f"/content/{page_id}?expand=version")
    version = current["version"]["number"] + 1
    payload = {
        "type": "page",
        "title": title,
        "version": {"number": version},
        "body": {"storage": {"value": body, "representation": "storage"}},
    }
    resp = requests.put(f"{BASE_URL}/rest/api/content/{page_id}", auth=AUTH,
                        headers=HEADERS, json=payload, timeout=15)
    resp.raise_for_status()
    log.info(f"  Updated: '{title}' (v{version})")


def upsert_page(parent_id: str, title: str, body: str, dry_run: bool = False) -> str:
    """Create or update a page. Returns the page ID."""
    existing = _find_child(parent_id, title)
    if dry_run:
        log.info(f"  [DRY RUN] {'Update' if existing else 'Create'}: '{title}'")
        return existing or "dry-run"
    if existing:
        _update_page(existing, title, body)
        return existing
    return _create_page(parent_id, title, body)


def ensure_folder(parent_id: str, title: str, dry_run: bool = False) -> str:
    """Ensure a folder-page exists and return its ID."""
    existing = _find_child(parent_id, title)
    if existing:
        return existing
    ts = datetime.now().strftime("%B %Y")
    body = f"<p><em>Research folder — {title}. Last updated {ts}.</em></p>"
    return upsert_page(parent_id, title, body, dry_run)


# ── CONTENT BUILDERS ──────────────────────────────────────────────────────────
def _ts() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M UTC")


def _info_panel(text: str) -> str:
    return (f'<ac:structured-macro ac:name="info">'
            f'<ac:parameter ac:name="title">About this page</ac:parameter>'
            f'<ac:rich-text-body><p>{text}</p></ac:rich-text-body>'
            f'</ac:structured-macro>')


def df_to_confluence_table(df: pd.DataFrame, max_rows: int = 300) -> str:
    """Convert a DataFrame to Confluence storage-format HTML table."""
    if df.empty:
        return "<p><em>No data available.</em></p>"
    df = df.head(max_rows)
    rows = ["<table><tbody>"]
    # Header
    rows.append("<tr>" + "".join(f"<th><strong>{c}</strong></th>" for c in df.columns) + "</tr>")
    # Data rows
    for _, row in df.iterrows():
        cells = []
        for val in row:
            val = str(val) if val is not None else ""
            # Colour priority cells
            if val.lower() in ("high", "yes"):
                cells.append(f'<td><span style="color:#276221;font-weight:bold">{val}</span></td>')
            elif val.lower() == "medium":
                cells.append(f'<td><span style="color:#9C6500;font-weight:bold">{val}</span></td>')
            elif val.lower() in ("low", "no"):
                cells.append(f'<td><span style="color:#9C0006">{val}</span></td>')
            else:
                cells.append(f"<td>{val}</td>")
        rows.append("<tr>" + "".join(cells) + "</tr>")
    rows.append("</tbody></table>")
    return "\n".join(rows)


def md_to_confluence(md: str) -> str:
    """Minimal markdown → Confluence storage format conversion."""
    lines, out = md.splitlines(), []
    for line in lines:
        if line.startswith("# "):
            out.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "):
            out.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):
            out.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("> "):
            out.append(f"<blockquote><p>{line[2:]}</p></blockquote>")
        elif line.startswith("| ") and "|" in line:
            # table row — handled below
            out.append(line)
        elif line.startswith("---"):
            out.append("<hr/>")
        elif line.strip() == "":
            out.append("<br/>")
        else:
            # Bold **text**
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            # Italic *text*
            line = re.sub(r"\*(.+?)\*", r"<em>\1</em>", line)
            out.append(f"<p>{line}</p>")

    # Convert markdown tables
    result = "\n".join(out)
    def convert_table(m):
        rows = [r for r in m.group(0).splitlines() if not re.match(r"^\|[-| ]+\|$", r)]
        html = ["<table><tbody>"]
        for i, row in enumerate(rows):
            cells = [c.strip() for c in row.strip("|").split("|")]
            tag = "th" if i == 0 else "td"
            html.append("<tr>" + "".join(f"<{tag}>{c}</{tag}>" for c in cells) + "</tr>")
        html.append("</tbody></table>")
        return "\n".join(html)
    result = re.sub(r"(\|.+\|\n)+", convert_table, result)
    return result


def _latest_file(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern))
    return matches[-1] if matches else None


# ── PIPELINE PUBLISHERS ───────────────────────────────────────────────────────
def publish_competitor(folder_id: str, dry_run: bool) -> None:
    out = SCRIPT_DIR / "output"
    csv = _latest_file(out, "competitor_all_priority_*.csv")
    if not csv:
        log.warning("Competitor CSV not found — skipping.")
        return

    df = pd.read_csv(csv, dtype=str).fillna("")
    ts = _ts()

    # All services
    cols = ["competitor","service_name","category","customer_maturity","clients","industries",
            "pricing_signals","priority_tier","priority_score"]
    upsert_page(folder_id, "Competitor Services — All",
        _info_panel(f"All competitor AI services extracted and scored. {len(df)} rows. Updated {ts}.") +
        df_to_confluence_table(df[[c for c in cols if c in df.columns]]), dry_run)

    # High priority only
    high = df[df["priority_tier"].str.lower() == "high"]
    upsert_page(folder_id, "Competitor Services — High Priority",
        _info_panel(f"High-priority competitor services only. {len(high)} rows. Updated {ts}.") +
        df_to_confluence_table(high[[c for c in cols if c in high.columns]]), dry_run)

    # Hypothesis tracker
    hyp_file = _latest_file(out, "hypothesis_tracker_*.json")
    if hyp_file:
        hyp_data = json.loads(hyp_file.read_text(encoding="utf-8"))
        rows = []
        hypotheses = [
            "Competitors are charging an AI premium of 15–30% over baseline managed services rates.",
            "The fastest-growing competitors are pivoting from time-and-materials to outcome/value-based pricing.",
            "AI capability is being built primarily through hyperscaler partnerships rather than internal R&D.",
            "European enterprise buyers are prioritising data sovereignty and compliance-safe AI.",
            "Competitors are concentrating AI investment in 2–3 verticals rather than spreading across all sectors.",
        ]
        for comp, verdicts in hyp_data.items():
            for h_key, h_val in verdicts.items():
                idx = int(h_key[1:]) - 1
                rows.append({
                    "Hypothesis": hypotheses[idx] if idx < len(hypotheses) else h_key,
                    "Competitor": comp,
                    "Verdict": h_val.get("verdict",""),
                    "Evidence For": h_val.get("evidence_for","")[:200],
                    "Evidence Against": h_val.get("evidence_against","")[:200],
                })
        hyp_df = pd.DataFrame(rows)
        upsert_page(folder_id, "Hypothesis Tracker",
            _info_panel(f"Strategic hypothesis verdicts per competitor. Updated {ts}.") +
            df_to_confluence_table(hyp_df), dry_run)

    # Market summary
    md_file = _latest_file(out, "*competitor_market_summary*.md")
    if md_file:
        upsert_page(folder_id, "Market Summary",
            _info_panel(f"Competitor market summary. Updated {ts}.") +
            md_to_confluence(md_file.read_text(encoding="utf-8")), dry_run)


def publish_ai_consulting(folder_id: str, dry_run: bool) -> None:
    out = SCRIPT_DIR / "ai_output"
    csv = _latest_file(out, "ai_consulting_all_priority_*.csv")
    if not csv:
        log.warning("AI consulting CSV not found — skipping.")
        return

    df = pd.read_csv(csv, dtype=str).fillna("")
    ts = _ts()

    # All services
    cols = ["source","service_name","service_type","description","delivery_format",
            "duration","target_audience","clients","industries",
            "pricing_explicit","inferred_price_range","pricing_model","priority_tier"]
    upsert_page(folder_id, "Services — All",
        _info_panel(f"All AI consulting services extracted from competitor websites. {len(df)} rows. Updated {ts}.") +
        df_to_confluence_table(df[[c for c in cols if c in df.columns]]), dry_run)

    # By service type
    from lib.pipelines.ai_consulting import SERVICE_TYPES
    body = _info_panel(f"AI consulting services grouped by service type. Updated {ts}.")
    for stype in SERVICE_TYPES:
        subset = df[df["service_type"].str.contains(stype, case=False, na=False)]
        if subset.empty:
            continue
        body += f"<h2>{stype} ({len(subset)})</h2>"
        body += df_to_confluence_table(subset[[c for c in cols if c in subset.columns]], max_rows=100)
    upsert_page(folder_id, "Services — By Service Type", body, dry_run)

    # Pricing & format analysis
    md_file = _latest_file(out, "*ai_market_summary*.md")
    if md_file:
        upsert_page(folder_id, "Pricing & Format Analysis",
            _info_panel(f"Delivery formats, pricing models, and price ranges. Updated {ts}.") +
            md_to_confluence(md_file.read_text(encoding="utf-8")), dry_run)

    # Named clients & industries
    from collections import Counter
    all_clients    = [c.strip() for r in df.get("clients", df.get("client_wins", pd.Series(dtype=str))).tolist() for c in str(r).split(",") if c.strip()]
    all_industries = [i.strip() for r in df.get("industries", pd.Series(dtype=str)).tolist() for i in str(r).split(",") if i.strip()]
    c_df = pd.DataFrame(Counter(all_clients).most_common(50), columns=["Client","# Mentions"])
    i_df = pd.DataFrame(Counter(all_industries).most_common(30), columns=["Industry","# Mentions"])
    upsert_page(folder_id, "Named Clients & Industries",
        _info_panel(f"Named clients and industries found across competitor AI consulting pages. Updated {ts}.") +
        "<h2>Top Clients</h2>" + df_to_confluence_table(c_df) +
        "<h2>Top Industries</h2>" + df_to_confluence_table(i_df), dry_run)


def publish_legacy(folder_id: str, dry_run: bool) -> None:
    out = SCRIPT_DIR / "legacy_output"
    csv = _latest_file(out, "legacy_all_priority*.csv")
    if not csv:
        log.warning("Legacy CSV not found — skipping.")
        return

    df = pd.read_csv(csv, dtype=str).fillna("")
    ts = _ts()

    cols = ["source","service_name","type","category","maturity_level",
            "description","evidence","priority_tier","priority_score"]
    upsert_page(folder_id, "Services & Tools — All",
        _info_panel(f"All legacy modernisation services and tools scored. {len(df)} rows. Updated {ts}.") +
        df_to_confluence_table(df[[c for c in cols if c in df.columns]]), dry_run)

    md_file = _latest_file(out, "*legacy*brief*.md")
    if md_file:
        upsert_page(folder_id, "Research Brief",
            _info_panel(f"Research brief answering the 4 pillar questions on AI-assisted legacy modernisation. Updated {ts}.") +
            md_to_confluence(md_file.read_text(encoding="utf-8")), dry_run)


def publish_client_intel(folder_id: str, dry_run: bool) -> None:
    out = SCRIPT_DIR / "client_output"
    csv = _latest_file(out, "client_intel_all_priority*.csv")
    if not csv:
        log.warning("Client intel CSV not found — skipping.")
        return

    df = pd.read_csv(csv, dtype=str).fillna("")
    ts = _ts()

    cols = ["source","signal_type","title","vendor_tools","budget_mention",
            "strategic_intent","maturity","source_type","priority_tier"]

    upsert_page(folder_id, "AI Signals — All",
        _info_panel(f"All AI investment signals extracted from client and prospect websites. {len(df)} rows. Updated {ts}.") +
        df_to_confluence_table(df[[c for c in cols if c in df.columns]]), dry_run)

    budget = df[df["signal_type"] == "budget_signal"]
    upsert_page(folder_id, "Budget Signals",
        _info_panel(f"Explicit AI budget and investment signals. {len(budget)} rows. Updated {ts}.") +
        df_to_confluence_table(budget[[c for c in cols if c in budget.columns]]), dry_run)

    from collections import Counter
    all_vendors = [v.strip() for r in df["vendor_tools"].tolist() for v in r.split(",") if v.strip()]
    v_df = pd.DataFrame(Counter(all_vendors).most_common(40), columns=["Vendor / Tool","# Client Mentions"])
    upsert_page(folder_id, "Vendor & Tool Usage",
        _info_panel(f"AI vendors and tools confirmed in use across clients. Updated {ts}.") +
        df_to_confluence_table(v_df), dry_run)

    md_file = _latest_file(out, "*potential_clients*.md")
    if md_file:
        upsert_page(folder_id, "Potential Client Matches",
            _info_panel(f"Potential clients matched to existing client profiles by AI maturity and vendor overlap. Updated {ts}.") +
            md_to_confluence(md_file.read_text(encoding="utf-8")), dry_run)


# ── MAIN ──────────────────────────────────────────────────────────────────────
_PIPELINES = {
    "competitor":    ("Competitor Intelligence",  publish_competitor),
    "ai_consulting": ("AI Consulting Market",     publish_ai_consulting),
    "legacy":        ("Legacy Modernisation",     publish_legacy),
    "client_intel":  ("Client Intelligence",      publish_client_intel),
}


def run(mode: str = "all", dry_run: bool = False) -> None:
    for var, name in [("CONFLUENCE_BASE_URL", BASE_URL), ("CONFLUENCE_EMAIL", EMAIL),
                      ("CONFLUENCE_API_TOKEN", TOKEN), ("CONFLUENCE_SPACE_KEY", SPACE_KEY),
                      ("CONFLUENCE_RESEARCH_PAGE_ID", RESEARCH_ID)]:
        if not name:
            raise EnvironmentError(f"{var} not set in .env")

    pipelines = _PIPELINES if mode == "all" else {mode: _PIPELINES[mode]}

    for key, (folder_title, publisher) in pipelines.items():
        log.info(f"\n{'─'*55}\n  {folder_title}\n{'─'*55}")
        folder_id = ensure_folder(RESEARCH_ID, folder_title, dry_run)
        publisher(folder_id, dry_run)
        log.info(f"  ✓ {folder_title} done.")

    log.info("\nAll pipelines published to Confluence.")


def main():
    parser = argparse.ArgumentParser(description="Publish pipeline outputs to Confluence")
    parser.add_argument("--mode", default="all",
                        choices=["all","competitor","ai_consulting","legacy","client_intel"])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    run(mode=args.mode, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
