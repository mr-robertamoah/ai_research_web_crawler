"""
generate_executive_summary.py — generates a cross-pipeline executive summary
with per-hypothesis verdicts as the centrepiece.

Outputs:
  output/YYYYMMDD_executive_summary.md   — local markdown file
  Confluence: "Executive Summary" page under the Research folder

Usage:
  python generate_executive_summary.py
  python generate_executive_summary.py --dry-run   # skip Confluence publish
  python generate_executive_summary.py --no-publish

Environment variables (in .env):
  ANTHROPIC_API_KEY          required
  CLAUDE_MODEL               default: claude-sonnet-4-5  (or override)
  CONFLUENCE_*               same as confluence_publish.py
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

import pandas as pd
import requests

_SCRIPT_FILE_DIR = Path(__file__).parent.resolve()  # always the script's own directory
SCRIPT_DIR = Path(os.getenv("APP_DIR", _SCRIPT_FILE_DIR))


def _read_env(key: str) -> str:
    val = os.getenv(key, "")
    if val:
        return val
    # Always look for .env next to the script file, regardless of APP_DIR
    for env_file in [_SCRIPT_FILE_DIR / ".env", SCRIPT_DIR / ".env"]:
        if env_file.exists():
            for line in env_file.read_text().splitlines():
                if line.startswith(f"{key}="):
                    return line.split("=", 1)[1].strip()
    return ""


CLAUDE_KEY   = _read_env("ANTHROPIC_API_KEY")
CLAUDE_MODEL = _read_env("CLAUDE_MODEL") or "claude-sonnet-4-5"
BASE_URL     = _read_env("CONFLUENCE_BASE_URL").rstrip("/")
CF_EMAIL     = _read_env("CONFLUENCE_EMAIL")
CF_TOKEN     = _read_env("CONFLUENCE_API_TOKEN")
CF_SPACE     = _read_env("CONFLUENCE_SPACE_KEY")
CF_PARENT    = _read_env("CONFLUENCE_RESEARCH_PAGE_ID")

HYPOTHESES = [
    "Competitors are charging an AI premium of 15–30% over baseline managed services rates.",
    "The fastest-growing competitors are pivoting from time-and-materials to outcome/value-based pricing.",
    "AI capability is being built primarily through hyperscaler partnerships rather than internal R&D.",
    "European enterprise buyers are prioritising data sovereignty and compliance-safe AI.",
    "Competitors are concentrating AI investment in 2–3 verticals rather than spreading across all sectors.",
]


# ── DATA LOADING ──────────────────────────────────────────────────────────────
def _latest(directory: Path, pattern: str) -> Path | None:
    matches = sorted(directory.glob(pattern), key=lambda p: p.stat().st_mtime)
    return matches[-1] if matches else None


def load_hypothesis_data() -> dict:
    f = _latest(SCRIPT_DIR / "output", "hypothesis_tracker_*.json")
    if f:
        return json.loads(f.read_text(encoding="utf-8"))
    return {}


def load_csv(directory: Path, pattern: str) -> pd.DataFrame:
    f = _latest(directory, pattern)
    if f:
        return pd.read_csv(f, dtype=str).fillna("")
    return pd.DataFrame()


def _extract_hpage_verdicts() -> dict[int, str]:
    """
    Read the most recent hypothesis MD files and extract the Overall Verdict
    from each. Returns {1: "Insufficient data", 2: "Mixed", ...}
    Falls back to empty dict if files not found.
    """
    verdicts = {}
    out_dir = SCRIPT_DIR / "output"
    short_slugs = [
        "ai_premium_pricing",
        "shift_to_outcome",
        "hyperscaler_partnerships",
        "european_data_sovereignty",
        "vertical_concentration",
    ]
    for i in range(1, 6):
        # Match any hypothesis file for this number
        matches = sorted(out_dir.glob(f"*hypothesis_{i}_*.md"), key=lambda p: p.stat().st_mtime)
        if not matches:
            continue
        content = matches[-1].read_text(encoding="utf-8")
        # Extract "## Overall Verdict: <verdict>" line
        m = re.search(r"##\s+Overall Verdict:\s*(.+)", content, re.IGNORECASE)
        if m:
            verdicts[i] = m.group(1).strip().rstrip("*").strip()
    return verdicts


def build_data_context() -> dict:
    """Assemble a compact structured summary of all pipeline data for the LLM."""
    hyp_data    = load_hypothesis_data()
    comp_df     = load_csv(SCRIPT_DIR / "output",        "competitor_all_priority_*.csv")
    ai_df       = load_csv(SCRIPT_DIR / "ai_output",     "ai_consulting_all_priority_*.csv")
    legacy_df   = load_csv(SCRIPT_DIR / "legacy_output", "legacy_all_priority*.csv")
    client_df   = load_csv(SCRIPT_DIR / "client_output", "client_intel_all_priority*.csv")

    # ── Load narrative MD files for richer context ──
    def _read_md(directory: Path, pattern: str) -> str:
        f = _latest(directory, pattern)
        return f.read_text(encoding="utf-8")[:8_000] if f else ""

    md_competitor  = _read_md(SCRIPT_DIR / "output",        "*competitor_market_summary*.md")
    md_ai          = _read_md(SCRIPT_DIR / "ai_output",     "*ai_market_summary*.md")
    md_legacy      = _read_md(SCRIPT_DIR / "legacy_output", "*legacy*brief*.md")
    md_client      = _read_md(SCRIPT_DIR / "client_output", "*client_market_summary*.md")

    # ── Hypothesis verdicts: read from H-page MD files (ground truth) ──
    hpage_verdicts = _extract_hpage_verdicts()

    hyp_summary = []
    for i, hyp_text in enumerate(HYPOTHESES, 1):
        h_key = f"h{i}"
        verdicts_list = [v[h_key]["verdict"] for v in hyp_data.values() if h_key in v]
        counts        = Counter(verdicts_list)
        total         = len(verdicts_list) or 1
        # Use H-page verdict as ground truth; fall back to computed if not available
        overall = hpage_verdicts.get(i) or (
            "Confirmed" if counts.get("Confirmed",0) > total*0.5
            else "Refuted" if counts.get("Refuted",0) > total*0.5
            else "Mixed / Insufficient data"
        )
        evidence_for     = [v[h_key].get("evidence_for","")     for v in hyp_data.values() if h_key in v and v[h_key].get("evidence_for","").strip()][:5]
        evidence_against = [v[h_key].get("evidence_against","") for v in hyp_data.values() if h_key in v and v[h_key].get("evidence_against","").strip()][:5]
        hyp_summary.append({
            "hypothesis": hyp_text,
            "verdict": overall,
            "confirmed": counts.get("Confirmed",0),
            "refuted": counts.get("Refuted",0),
            "insufficient": counts.get("Insufficient data",0),
            "evidence_for": evidence_for,
            "evidence_against": evidence_against,
        })

    # ── AI consulting companies as additional hypothesis evidence ──
    ai_hyp_evidence = {}
    if not ai_df.empty:
        # H1: pricing signals
        pricing_signals = [r for r in ai_df.get("pricing_explicit", pd.Series(dtype=str)).tolist() if str(r).strip() and str(r).lower() not in ("nan","")]
        pricing_models  = ai_df["pricing_model"].value_counts().to_dict() if "pricing_model" in ai_df.columns else {}
        inferred        = ai_df["inferred_price_range"].value_counts().head(5).to_dict() if "inferred_price_range" in ai_df.columns else {}
        ai_hyp_evidence["H1_pricing"] = {"explicit_prices": pricing_signals[:10], "pricing_models": {k: int(v) for k,v in pricing_models.items()}, "inferred_ranges": {k: int(v) for k,v in inferred.items()}}
        # H5: vertical concentration
        industries = Counter(i.strip() for r in ai_df.get("industries", pd.Series(dtype=str)).tolist() for i in str(r).split(",") if i.strip())
        ai_hyp_evidence["H5_verticals"] = dict(industries.most_common(10))
        # H3: tech stack / partnerships
        tech_stacks = [r for r in ai_df.get("delivery_format", pd.Series(dtype=str)).tolist() if str(r).strip()][:20]
        ai_hyp_evidence["H3_formats"] = tech_stacks

    # ── Competitor snapshot ──
    comp_snap = {}
    if not comp_df.empty:
        comp_snap = {
            "total_services": int(len(comp_df)),
            "competitors": int(comp_df["competitor"].nunique() if "competitor" in comp_df.columns else 0),
            "high_priority": int((comp_df["priority_tier"].str.lower() == "high").sum()),
            "top_categories": {k: int(v) for k, v in comp_df["category"].value_counts().head(3).to_dict().items()} if "category" in comp_df.columns else {},
            "top_clients": [(c, int(n)) for c, n in Counter(c.strip() for r in comp_df.get("clients", pd.Series(dtype=str)).tolist()
                                   for c in str(r).split(",") if c.strip()).most_common(5)],
            "top_industries": [(i, int(n)) for i, n in Counter(i.strip() for r in comp_df.get("industries", pd.Series(dtype=str)).tolist()
                                      for i in str(r).split(",") if i.strip()).most_common(5)],
        }

    # ── AI consulting snapshot ──
    ai_snap = {}
    if not ai_df.empty:
        ai_snap = {
            "total_services": int(len(ai_df)),
            "companies": int(ai_df["source"].nunique() if "source" in ai_df.columns else 0),
            "top_service_types": {k: int(v) for k, v in ai_df["service_type"].value_counts().head(4).to_dict().items()} if "service_type" in ai_df.columns else {},
            "pricing_models": {k: int(v) for k, v in ai_df["pricing_model"].value_counts().to_dict().items()} if "pricing_model" in ai_df.columns else {},
            "top_clients": [(c, int(n)) for c, n in Counter(c.strip() for r in ai_df.get("clients", pd.Series(dtype=str)).tolist()
                                   for c in str(r).split(",") if c.strip()).most_common(5)],
        }

    # ── Legacy snapshot ──
    legacy_snap = {}
    if not legacy_df.empty:
        legacy_snap = {
            "total_services": int(len(legacy_df)),
            "high_priority": int((legacy_df["priority_tier"].str.lower() == "high").sum()),
            "top_categories": {k: int(v) for k, v in legacy_df["category"].value_counts().head(3).to_dict().items()} if "category" in legacy_df.columns else {},
            "maturity_levels": {k: int(v) for k, v in legacy_df["maturity_level"].value_counts().to_dict().items()} if "maturity_level" in legacy_df.columns else {},
        }

    client_snap = {}
    if not client_df.empty:
        client_snap = {
            "total_signals": int(len(client_df)),
            "clients": int(client_df["source"].nunique() if "source" in client_df.columns else 0),
            "high_priority": int((client_df["priority_tier"].str.lower() == "high").sum()),
            "signal_types": {k: int(v) for k, v in client_df["signal_type"].value_counts().to_dict().items()} if "signal_type" in client_df.columns else {},
            "top_vendors": [(v, int(n)) for v, n in Counter(v.strip() for r in client_df.get("vendor_tools", pd.Series(dtype=str)).tolist()
                                   for v in str(r).split(",") if v.strip()).most_common(5)],
            "budget_signals": client_df[client_df.get("signal_type","") == "budget_signal"]["budget_mention"].tolist()[:5] if "signal_type" in client_df.columns else [],
        }

    return {
        "hypotheses": hyp_summary,
        "ai_consulting_hypothesis_evidence": ai_hyp_evidence,
        "competitor": comp_snap,
        "ai_consulting": ai_snap,
        "legacy": legacy_snap,
        "client_intel": client_snap,
        "narratives": {
            "competitor_market_summary": md_competitor,
            "ai_consulting_market_summary": md_ai,
            "legacy_research_brief": md_legacy,
            "client_market_summary": md_client,
        },
        "generated_at": datetime.now().strftime("%B %Y"),
    }


def _build_prompt_data(ctx: dict) -> str:
    """Build a rich, structured prompt context from all data sources."""
    parts = []

    # Structured hypothesis data
    parts.append("=== HYPOTHESIS DATA (from competitor pipeline) ===")
    parts.append(json.dumps(ctx["hypotheses"], indent=2))

    # AI consulting evidence for hypotheses
    if ctx.get("ai_consulting_hypothesis_evidence"):
        parts.append("\n=== AI CONSULTING MARKET — HYPOTHESIS EVIDENCE (46 companies) ===")
        parts.append(json.dumps(ctx["ai_consulting_hypothesis_evidence"], indent=2))

    # Structured snapshots
    parts.append("\n=== PIPELINE SNAPSHOTS ===")
    for key in ("competitor", "ai_consulting", "legacy", "client_intel"):
        if ctx.get(key):
            parts.append(f"\n-- {key.upper()} --")
            parts.append(json.dumps(ctx[key], indent=2))

    # Narrative MD summaries (richest context)
    narratives = ctx.get("narratives", {})
    if narratives.get("competitor_market_summary"):
        parts.append("\n=== COMPETITOR MARKET SUMMARY (full narrative) ===")
        parts.append(narratives["competitor_market_summary"])
    if narratives.get("ai_consulting_market_summary"):
        parts.append("\n=== AI CONSULTING MARKET SUMMARY (full narrative) ===")
        parts.append(narratives["ai_consulting_market_summary"])
    if narratives.get("legacy_research_brief"):
        parts.append("\n=== LEGACY MODERNISATION RESEARCH BRIEF ===")
        parts.append(narratives["legacy_research_brief"])
    if narratives.get("client_market_summary"):
        parts.append("\n=== CLIENT INTELLIGENCE MARKET SUMMARY ===")
        parts.append(narratives["client_market_summary"])

    return "\n".join(parts)


# ── LLM CALL ──────────────────────────────────────────────────────────────────
_SYSTEM = """You are a senior strategic analyst writing an executive summary for AmaliTech's leadership team.

AmaliTech context:
- AI-first technology services company, ~400 staff, delivery from Ghana + Rwanda
- Serving European enterprise clients: manufacturing (Schaeffler, Continental), telecoms (Deutsche Telekom), financial services
- ISO 27001/TISAX certified, AWS Advanced Partner
- Considering launching AI consulting services and legacy modernisation services
- Competing against Accenture, Deloitte, BCG, Wipro, Thoughtworks and boutique AI firms

CRITICAL INSTRUCTION: The hypothesis verdicts provided in the data are the AUTHORITATIVE verdicts from the individual hypothesis analysis pages (H1–H5). You MUST use these exact verdict labels in the Executive Summary. Do NOT reinterpret or override them. The narrative under each hypothesis must be consistent with and supportive of its verdict label.

Write with authority and precision. Be direct. No filler phrases. Every sentence must carry information.
"""

_PROMPT = """Based on the research data below, write a comprehensive executive summary.

Structure EXACTLY as follows (use these exact markdown headers):

# Executive Summary — AI Market Intelligence
*{date}*

## State of the Market
[3–4 sentences: what is happening in the AI services market right now, what is the dominant trend, what does this mean for a company like AmaliTech]

## Hypothesis Verdicts
*These verdicts reflect the AmaliTech-strategy interpretation from the individual hypothesis pages (H1–H5), which build on the aggregate evidence in the Competitor Hypothesis Tracker and Research folders.*

[For EACH of the 5 hypotheses, write a subsection:]

### H1: [short hypothesis title]
**Verdict: [Confirmed / Refuted / Mixed]** ({confirmed} confirmed, {refuted} refuted across {total} competitors)
[2–3 sentences: what the evidence shows, what this means for AmaliTech's strategy]
**Key evidence:** [1–2 specific quotes or findings from the data]
**AmaliTech implication:** [1 sentence — concrete action or positioning]

[repeat for H2–H5]

## Competitor Landscape
[3–4 sentences: who the key players are, what they are doing, where they are strong, where they are weak]

## AI Consulting Market
[2–3 sentences: what services dominate, how pricing works, what the entry point looks like for AmaliTech]

## Legacy Modernisation Opportunity
[2–3 sentences: maturity of the market, key tools/approaches, AmaliTech's angle]

## Client AI Spend Signals
[2–3 sentences: which clients are actively investing, what vendors they are using, what this means for AmaliTech's account strategy]

## Strategic Recommendations
[Exactly 5 numbered recommendations, each 2 sentences: what to do and why, grounded in the data above]

---
*Research based on {competitor_count} competitors, {ai_companies} AI consulting firms, {legacy_sources} legacy sources, and {client_count} client/prospect websites.*

---

Research data:
{data}
"""


def call_claude(data_context: dict) -> str:
    try:
        import anthropic
    except ImportError:
        raise ImportError("Run: pip install anthropic")
    if not CLAUDE_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY not set.")

    hyp_data = data_context["hypotheses"]
    prompt = _PROMPT.format(
        date=data_context["generated_at"],
        confirmed=sum(h["confirmed"] for h in hyp_data),
        refuted=sum(h["refuted"] for h in hyp_data),
        total=sum(h["confirmed"] + h["refuted"] + h["insufficient"] for h in hyp_data),
        competitor_count=data_context["competitor"].get("competitors", 0),
        ai_companies=data_context["ai_consulting"].get("companies", 0),
        legacy_sources=data_context["legacy"].get("total_services", 0),
        client_count=data_context["client_intel"].get("clients", 0),
        data=_build_prompt_data(data_context),
    )

    client = anthropic.Anthropic(api_key=CLAUDE_KEY)
    resp = client.messages.create(
        model=CLAUDE_MODEL,
        max_tokens=8192,
        system=_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


# ── CONFLUENCE PUBLISH ────────────────────────────────────────────────────────
def _md_to_html(md: str) -> str:
    lines, out = md.splitlines(), []
    for line in lines:
        if line.startswith("# "):     out.append(f"<h1>{line[2:]}</h1>")
        elif line.startswith("## "): out.append(f"<h2>{line[3:]}</h2>")
        elif line.startswith("### "):out.append(f"<h3>{line[4:]}</h3>")
        elif line.startswith("**") and line.endswith("**"): out.append(f"<p><strong>{line[2:-2]}</strong></p>")
        elif line.startswith("> "): out.append(f"<blockquote><p>{line[2:]}</p></blockquote>")
        elif line.startswith("---"): out.append("<hr/>")
        elif line.startswith("*") and line.endswith("*") and not line.startswith("**"):
            out.append(f"<p><em>{line[1:-1]}</em></p>")
        elif re.match(r"^\d+\.", line): out.append(f"<p>{line}</p>")
        elif line.strip() == "":    out.append("<br/>")
        else:
            line = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", line)
            line = re.sub(r"\*(.+?)\*", r"<em>\1</em>", line)
            out.append(f"<p>{line}</p>")
    return "\n".join(out)


_HYP_SYSTEM = """You are a senior strategic analyst writing a focused hypothesis analysis for AmaliTech's leadership.
AmaliTech: AI-first tech services, Ghana + Rwanda delivery, European enterprise clients (manufacturing, telecoms, financial services).
ISO 27001/TISAX certified, AWS Advanced Partner. Considering launching AI consulting and legacy modernisation services.
Be direct, evidence-based, and specific. Every sentence must carry information."""

_HYP_PROMPT = """Write a detailed hypothesis analysis document for the following hypothesis.

Hypothesis: {hypothesis}

Structure EXACTLY as follows:

# Hypothesis {num}: {short_title}

## Hypothesis Statement
{hypothesis}

## Overall Verdict: {verdict}
*{confirmed} Confirmed | {refuted} Refuted | {insufficient} Insufficient data across {total} competitors*

[2–3 sentences explaining what the aggregate evidence shows and why the verdict is what it is]

## Per-Competitor Breakdown
[For each competitor with a verdict, one bullet: **CompetitorName** — Verdict — key evidence quote (1 sentence)]

## Supporting Evidence from AI Consulting Market (46 companies)
[3–4 sentences: what the broader AI consulting market data shows that supports or challenges this hypothesis — use the ai_consulting_evidence data]

## What This Means for AmaliTech
[3–4 sentences: concrete strategic implication — what AmaliTech should do differently, what opportunity or risk this creates, what action to take]

## Confidence Assessment
**Data quality:** [High / Medium / Low] — [1 sentence explaining why]
**Recommended follow-up:** [1 sentence on what additional research would strengthen this verdict]

---

Research data for this hypothesis:
{data}
"""


def generate_hypothesis_summary(hyp_index: int, hyp_data: dict,
                                  ai_evidence: dict, data_context: dict) -> str:
    """Generate a standalone deep-dive for one hypothesis."""
    try:
        import anthropic
    except ImportError:
        raise ImportError("Run: pip install anthropic")

    h = data_context["hypotheses"][hyp_index]
    short_titles = [
        "AI Premium Pricing",
        "Shift to Outcome-Based Pricing",
        "Hyperscaler Partnerships vs Internal R&D",
        "European Data Sovereignty Priority",
        "Vertical Concentration Strategy",
    ]

    # Build per-competitor breakdown
    h_key = f"h{hyp_index + 1}"
    comp_breakdown = []
    for comp, verdicts in hyp_data.items():
        if h_key in verdicts:
            v = verdicts[h_key]
            evidence = v.get("evidence_for", "") or v.get("evidence_against", "")
            comp_breakdown.append(f"- **{comp}** — {v.get('verdict','?')} — {evidence[:150]}")

    data_str = f"""
Per-competitor verdicts:
{chr(10).join(comp_breakdown)}

AI consulting market evidence:
{json.dumps(ai_evidence, indent=2)[:3_000]}

Competitor market narrative:
{data_context['narratives'].get('competitor_market_summary','')[:3_000]}

AI consulting market narrative:
{data_context['narratives'].get('ai_consulting_market_summary','')[:2_000]}
"""

    prompt = _HYP_PROMPT.format(
        num=hyp_index + 1,
        hypothesis=h["hypothesis"],
        short_title=short_titles[hyp_index],
        verdict=h["verdict"],
        confirmed=h["confirmed"],
        refuted=h["refuted"],
        insufficient=h["insufficient"],
        total=h["confirmed"] + h["refuted"] + h["insufficient"],
        data=data_str,
    )

    client = anthropic.Anthropic(api_key=CLAUDE_KEY)
    resp = client.messages.create(
        model=CLAUDE_MODEL, max_tokens=4096,
        system=_HYP_SYSTEM,
        messages=[{"role": "user", "content": prompt}],
    )
    return resp.content[0].text.strip()


# ── CONFLUENCE HELPERS ────────────────────────────────────────────────────────
def _cf_upsert(parent_id: str, title: str, body: str,
               auth: tuple, headers: dict, dry_run: bool) -> str:
    resp = requests.get(f"{BASE_URL}/rest/api/content/search",
                        params={"cql": f'title="{title}" AND space="{CF_SPACE}" AND ancestor={parent_id}', "limit": 5},
                        auth=auth, headers=headers, timeout=15)
    existing_id = None
    if resp.ok:
        for p in resp.json().get("results", []):
            if p["title"] == title:
                existing_id = p["id"]
                break

    if dry_run:
        print(f"  [DRY RUN] {'Update' if existing_id else 'Create'}: '{title}'")
        return existing_id or "dry-run"

    if existing_id:
        current = requests.get(f"{BASE_URL}/rest/api/content/{existing_id}?expand=version",
                               auth=auth, headers=headers, timeout=15).json()
        v = current["version"]["number"] + 1
        payload = {"type": "page", "title": title, "version": {"number": v},
                   "body": {"storage": {"value": body, "representation": "storage"}}}
        requests.put(f"{BASE_URL}/rest/api/content/{existing_id}", auth=auth,
                     headers=headers, json=payload, timeout=15).raise_for_status()
        print(f"  Updated: '{title}' (v{v})")
        return existing_id
    else:
        payload = {"type": "page", "title": title, "space": {"key": CF_SPACE},
                   "ancestors": [{"id": parent_id}],
                   "body": {"storage": {"value": body, "representation": "storage"}}}
        r = requests.post(f"{BASE_URL}/rest/api/content", auth=auth,
                          headers=headers, json=payload, timeout=15)
        if not r.ok:
            print(f"  ERROR creating '{title}': {r.status_code} {r.text[:200]}")
            r.raise_for_status()
        pid = r.json()["id"]
        print(f"  Created: '{title}' (id={pid})")
        return pid


def publish_all_to_confluence(exec_md: str, hyp_mds: list[str],
                               dry_run: bool = False) -> None:
    if not all([BASE_URL, CF_EMAIL, CF_TOKEN, CF_SPACE, CF_PARENT]):
        print("Confluence env vars not set — skipping publish.")
        return

    auth    = (CF_EMAIL, CF_TOKEN)
    headers = {"Accept": "application/json", "Content-Type": "application/json"}

    # Ensure "Strategic Intelligence" folder exists under Research
    folder_id = _cf_upsert(CF_PARENT, "Strategic Intelligence",
                            "<p><em>Executive summary and hypothesis analyses. Auto-generated.</em></p>",
                            auth, headers, dry_run)

    # Executive summary
    _cf_upsert(folder_id, "Executive Summary",
               _md_to_html(exec_md), auth, headers, dry_run)

    # One page per hypothesis
    short_titles = [
        "AI Premium Pricing",
        "Shift to Outcome-Based Pricing",
        "Hyperscaler Partnerships vs Internal R&D",
        "European Data Sovereignty Priority",
        "Vertical Concentration Strategy",
    ]
    for i, (title, md) in enumerate(zip(short_titles, hyp_mds), 1):
        _cf_upsert(folder_id, f"H{i}: {title}",
                   _md_to_html(md), auth, headers, dry_run)


# ── MAIN ──────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Generate executive summary + hypothesis analyses")
    parser.add_argument("--dry-run",    action="store_true", help="Skip Confluence publish")
    parser.add_argument("--no-publish", action="store_true", help="Skip Confluence publish")
    args = parser.parse_args()

    print("Loading research data...")
    data_context = build_data_context()
    hyp_data     = load_hypothesis_data()
    ai_evidence  = data_context.get("ai_consulting_hypothesis_evidence", {})

    ts      = datetime.now().strftime("%Y%m%d_%H%M")
    out_dir = SCRIPT_DIR / "output"
    out_dir.mkdir(exist_ok=True)

    # ── Generate executive summary ──
    print(f"\nGenerating executive summary ({CLAUDE_MODEL})...")
    exec_md = call_claude(data_context)
    exec_path = out_dir / f"{ts}_executive_summary.md"
    exec_path.write_text(exec_md, encoding="utf-8")
    print(f"Saved: {exec_path.name}")

    # ── Generate per-hypothesis summaries ──
    hyp_mds = []
    short_titles = [
        "AI Premium Pricing",
        "Shift to Outcome-Based Pricing",
        "Hyperscaler Partnerships vs Internal R&D",
        "European Data Sovereignty Priority",
        "Vertical Concentration Strategy",
    ]
    for i in range(5):
        print(f"Generating H{i+1}: {short_titles[i]}...")
        md = generate_hypothesis_summary(i, hyp_data, ai_evidence, data_context)
        path = out_dir / f"{ts}_hypothesis_{i+1}_{short_titles[i].lower().replace(' ','_').replace('/','_')}.md"
        path.write_text(md, encoding="utf-8")
        print(f"Saved: {path.name}")
        hyp_mds.append(md)

    # ── Publish to Confluence ──
    if not args.dry_run and not args.no_publish:
        print("\nPublishing to Confluence...")
        publish_all_to_confluence(exec_md, hyp_mds)
    elif args.dry_run:
        publish_all_to_confluence(exec_md, hyp_mds, dry_run=True)

    print("\nDone. All 6 documents generated.")


if __name__ == "__main__":
    main()

    print("\nDone.")


if __name__ == "__main__":
    main()
