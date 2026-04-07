"""lib/pipelines/client_intel.py — extraction, row builder, and output for client intelligence pipeline."""
from __future__ import annotations

import json
import re
import textwrap
from collections import defaultdict, Counter
from datetime import datetime
from pathlib import Path

from lib.core import call_ai, parse_json, TIER_CONTENT, AI_BACKEND
import logging
log = logging.getLogger("analyse")

SIGNAL_TYPES = ["ai_initiative", "budget_signal", "vendor_tool", "strategic_intent", "careers_signal"]

DIMENSIONS: list = []  # no scoring matrix for client intel

_EXTRACTION_SYSTEM = textwrap.dedent("""
    You are a competitive intelligence analyst for AmaliTech — an AI-first tech services company
    targeting European enterprise clients in manufacturing, telecoms, financial services, and retail.

    Extract ALL signals about AI investment, AI initiatives, and AI spending from the content.

    For each signal return a JSON object with EXACTLY these keys:
    {
      "signal_type": "one of: ai_initiative | budget_signal | vendor_tool | strategic_intent | careers_signal",
      "title": "short descriptive title",
      "description": "2-3 sentences — what AI initiative/investment/tool this refers to",
      "vendor_tools": "named AI vendors or tools mentioned (e.g. Microsoft Azure AI, Databricks, OpenAI) — or empty string",
      "budget_mention": "any specific budget, investment amount, or cost figure mentioned — or empty string",
      "strategic_intent": "what business outcome or problem this AI investment is targeting",
      "maturity": "one of: exploring | piloting | scaling | embedded",
      "source_type": "one of: investor_relations | news_press | careers | insights_reports | general | pdf",
      "evidence": "direct quote or specific fact from the content",
      "source_url": "URL where found, or empty string"
    }

    Signal type guide:
    - ai_initiative: a named AI project, programme, or product being built or deployed
    - budget_signal: any mention of AI investment amount, budget allocation, or cost savings
    - vendor_tool: evidence of a specific AI vendor or tool being used or evaluated
    - strategic_intent: board/executive statements about AI direction or priorities
    - careers_signal: job postings or hiring patterns revealing AI tool stack or capability build

    Return ONLY a valid JSON array. No markdown, no preamble.
    If nothing relevant found return: []
""").strip()

_BRIEF_SYSTEM = textwrap.dedent("""
    You are a strategic analyst for AmaliTech — an AI-first tech services company.
    AmaliTech targets European enterprise clients in manufacturing, telecoms, financial services, and retail.
    Write a concise brief for AmaliTech's sales and leadership team.
    Be direct and evidence-based. Focus on actionable insights.
""").strip()

_BRIEF_USER = textwrap.dedent("""
    Based on the client intelligence content below, answer each question.
    Return ONLY valid JSON:
    {{
      "Q1": {{"question": "Which clients are most actively investing in AI and what are they building?",
              "answer": "", "amalitech_implication": ""}},
      "Q2": {{"question": "What AI vendors and tools are clients already using or evaluating?",
              "answer": "", "amalitech_implication": ""}},
      "Q3": {{"question": "What budget signals or investment levels are visible?",
              "answer": "", "amalitech_implication": ""}},
      "Q4": {{"question": "Where are the clearest opportunities for AmaliTech to offer services?",
              "answer": "", "amalitech_implication": ""}}
    }}
    Research content:
    {content}
""").strip()


def extract_services(name: str, content: str) -> list[dict]:
    log.info("  Extracting AI signals...")
    raw    = call_ai(_EXTRACTION_SYSTEM, f"Client: {name}\n\nContent:\n{content[:TIER_CONTENT]}")
    result = parse_json(raw, context=name)
    if not isinstance(result, list):
        log.warning(f"  Unexpected extraction response for {name}.")
        return []
    log.info(f"  {len(result)} signal(s) found.")
    return result


def build_rows(source: str, services: list[dict]) -> list[dict]:
    """Build rows — also reads client_meta.json if present to add client_type/client_name."""
    # Try to load client metadata from the site folder (written by scraper)
    # source is the folder name-derived name; metadata is richer
    rows = []
    for sig in services:
        rows.append({
            "source":           source,
            "signal_type":      sig.get("signal_type", ""),
            "title":            sig.get("title", ""),
            "description":      sig.get("description", ""),
            "vendor_tools":     sig.get("vendor_tools", ""),
            "budget_mention":   sig.get("budget_mention", ""),
            "strategic_intent": sig.get("strategic_intent", ""),
            "maturity":         sig.get("maturity", ""),
            "source_type":      sig.get("source_type", ""),
            "evidence":         sig.get("evidence", ""),
            "source_url":       sig.get("source_url", ""),
            "priority_score":   _signal_priority(sig),
            "priority_tier":    _signal_tier(sig),
        })
    rows.sort(key=lambda r: -float(r.get("priority_score", 0) or 0))
    return rows


def _signal_priority(sig: dict) -> float:
    weights = {"budget_signal": 90, "ai_initiative": 80, "strategic_intent": 75,
               "vendor_tool": 65, "careers_signal": 55}
    base = weights.get(sig.get("signal_type", ""), 50)
    # Boost if budget is mentioned
    if sig.get("budget_mention", "").strip():
        base = min(100, base + 10)
    return float(base)


def _signal_tier(sig: dict) -> str:
    s = _signal_priority(sig)
    return "High" if s >= 75 else ("Medium" if s >= 60 else "Low")


def generate_brief(all_content: str) -> dict:
    log.info("  Generating client intelligence brief...")
    raw    = call_ai(_BRIEF_SYSTEM, _BRIEF_USER.format(content=all_content[:TIER_CONTENT]))
    result = parse_json(raw, context="client_brief")
    return result if isinstance(result, dict) else {}


def write_brief_md(brief: dict, all_rows: list[dict], path: Path) -> None:
    ts = datetime.now().strftime("%B %Y")
    lines = [
        "# Client AI Intelligence — Research Brief",
        "", f"**Date:** {ts}  ", f"**AI backend:** {AI_BACKEND.upper()}  ", "", "---", "",
    ]
    q_labels = {
        "Q1": "Most active AI investors",
        "Q2": "AI vendors and tools in use",
        "Q3": "Budget signals",
        "Q4": "Opportunities for AmaliTech",
    }
    for q_id in ["Q1","Q2","Q3","Q4"]:
        q = brief.get(q_id, {})
        lines += [
            f"## {q_id}: {q_labels[q_id]}", "",
            f"**Question:** {q.get('question','')}", "",
            q.get("answer","_No answer generated._"), "",
            f"> **AmaliTech implication:** {q.get('amalitech_implication','_Not available._')}",
            "", "---", "",
        ]
    # Signal table by client
    by_source = defaultdict(list)
    for row in all_rows:
        by_source[row["source"]].append(row)
    lines += ["## Full Signal List by Client", ""]
    n = 1
    for src in sorted(by_source):
        rows = sorted(by_source[src], key=lambda r: -float(r.get("priority_score", 0) or 0))
        lines += [f"### {src}", "",
                  "| # | Signal Type | Title | Vendors | Budget | Maturity | Source |",
                  "|---|---|---|---|---|---|---|"]
        for row in rows:
            lines.append(
                f"| {n} | {row['signal_type']} | {row['title']} "
                f"| {row['vendor_tools']} | {row['budget_mention']} "
                f"| {row['maturity']} | {row['source_type']} |"
            )
            n += 1
        lines.append("")
    lines += ["---", "", f"*Client intelligence — {ts}. AI backend: {AI_BACKEND.upper()}.*", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"  Research brief: {path.name}")


def write_market_summary_md(all_rows: list[dict], path: Path) -> None:
    ts = datetime.now().strftime("%B %Y")
    by_source = defaultdict(list)
    client_meta: dict[str, dict] = {}  # source → {client_type, client_name}

    for row in all_rows:
        by_source[row["source"]].append(row)

    # Aggregate vendor mentions
    all_vendors = [v.strip() for r in all_rows for v in r.get("vendor_tools","").split(",") if v.strip()]
    vendor_counts = Counter(all_vendors).most_common(20)

    # Budget signals
    budget_rows = [r for r in all_rows if r.get("budget_mention","").strip()]

    # Signal type breakdown
    signal_counts = Counter(r["signal_type"] for r in all_rows)

    # Maturity breakdown
    maturity_counts = Counter(r["maturity"] for r in all_rows if r.get("maturity"))

    lines = [
        "# Client AI Intelligence — Market Summary",
        "",
        f"**Based on:** {len(all_rows)} signals across {len(by_source)} clients  ",
        f"**Date:** {ts}",
        "",
        "---",
        "",
        "## 1. Signal Type Breakdown",
        "",
        "| Signal Type | # Signals | What It Means |",
        "|---|---|---|",
        "| ai_initiative | {} | Named AI projects or programmes being built/deployed |".format(signal_counts.get("ai_initiative",0)),
        "| budget_signal | {} | Explicit investment amounts or cost savings mentioned |".format(signal_counts.get("budget_signal",0)),
        "| vendor_tool | {} | Specific AI vendors or tools confirmed in use |".format(signal_counts.get("vendor_tool",0)),
        "| strategic_intent | {} | Board/executive statements about AI direction |".format(signal_counts.get("strategic_intent",0)),
        "| careers_signal | {} | Job postings revealing AI tool stack or capability build |".format(signal_counts.get("careers_signal",0)),
        "",
        "---",
        "",
        "## 2. AI Maturity Across Clients",
        "",
        "| Maturity Level | # Signals | Description |",
        "|---|---|---|",
        "| exploring | {} | Early research, no committed investment yet |".format(maturity_counts.get("exploring",0)),
        "| piloting | {} | Running PoCs or limited trials |".format(maturity_counts.get("piloting",0)),
        "| scaling | {} | Expanding proven AI use cases across the business |".format(maturity_counts.get("scaling",0)),
        "| embedded | {} | AI is core to operations and products |".format(maturity_counts.get("embedded",0)),
        "",
        "---",
        "",
        "## 3. AI Vendors and Tools in Use",
        "",
        "| Vendor / Tool | # Client Mentions |",
        "|---|---|",
    ]
    for vendor, cnt in vendor_counts:
        lines.append(f"| {vendor} | {cnt} |")
    lines += [
        "",
        "> Vendors appearing here are already embedded in client environments.",
        "> AmaliTech should position services that complement or extend these platforms.",
        "",
        "---",
        "",
        "## 4. Budget Signals",
        "",
        "| Client | Budget Mention | Signal Type | Source |",
        "|---|---|---|---|",
    ]
    for row in sorted(budget_rows, key=lambda r: -float(r.get("priority_score", 0) or 0)):
        lines.append(f"| {row['source']} | {row['budget_mention']} | {row['signal_type']} | {row['source_type']} |")

    lines += [
        "",
        "---",
        "",
        "## 5. Per-Client AI Spend Profile",
        "",
    ]
    for src in sorted(by_source):
        rows = by_source[src]
        top_vendors = Counter(v.strip() for r in rows for v in r.get("vendor_tools","").split(",") if v.strip()).most_common(3)
        top_signals = sorted(rows, key=lambda r: -float(r.get("priority_score", 0) or 0))[:3]
        budgets     = [r["budget_mention"] for r in rows if r.get("budget_mention","").strip()]
        maturity    = Counter(r["maturity"] for r in rows if r.get("maturity")).most_common(1)
        lines += [
            f"### {src}",
            "",
            f"**Signals found:** {len(rows)}  ",
            f"**AI maturity:** {maturity[0][0] if maturity else 'unknown'}  ",
            f"**Top vendors:** {', '.join(v for v,_ in top_vendors) or 'none identified'}  ",
            f"**Budget mentions:** {'; '.join(budgets[:2]) or 'none found'}",
            "",
            "| Signal | Type | Maturity | Source |",
            "|---|---|---|---|",
        ]
        for row in top_signals:
            lines.append(f"| {row['title']} | {row['signal_type']} | {row['maturity']} | {row['source_type']} |")
        lines.append("")

    lines += ["---", "", f"*Client intelligence — {ts}. AI backend: {AI_BACKEND.upper()}.*", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"  Market summary: {path.name}")


def write_executive_brief_md(brief: dict, all_rows: list[dict], path: Path) -> None:
    ts = datetime.now().strftime("%B %Y")
    by_source = defaultdict(list)
    for row in all_rows:
        by_source[row["source"]].append(row)

    all_vendors   = [v.strip() for r in all_rows for v in r.get("vendor_tools","").split(",") if v.strip()]
    top_vendors   = Counter(all_vendors).most_common(5)
    budget_rows   = [r for r in all_rows if r.get("budget_mention","").strip()]
    high_priority = [r for r in all_rows if r["priority_tier"] == "High"]

    lines = [
        "# Client AI Intelligence — Executive Brief",
        "",
        f"**Prepared for:** AmaliTech Leadership  ",
        f"**Date:** {ts}  ",
        f"**Classification:** Internal",
        "",
        "---",
        "",
        "## What We Did",
        "",
        f"We automatically scraped and analysed **{len(by_source)} client and prospect websites**, "
        f"extracting **{len(all_rows)} AI signals** from investor relations pages, press releases, "
        f"news, insights reports, and careers postings. "
        f"The goal: understand where clients are spending on AI so AmaliTech can position services accordingly.",
        "",
        "---",
        "",
        "## Key Findings",
        "",
        f"**{len(high_priority)} high-priority signals** identified — budget mentions, named AI initiatives, and confirmed vendor relationships.",
        "",
        "**Top AI vendors already embedded in client environments:**",
        "",
        "| Vendor / Tool | # Clients Using |",
        "|---|---|",
    ]
    for vendor, cnt in top_vendors:
        lines.append(f"| {vendor} | {cnt} |")

    lines += [
        "",
        "> AmaliTech should position as an implementation and integration partner for these platforms,",
        "> not as a replacement — clients have already chosen their AI stack.",
        "",
        "---",
        "",
        "## Budget Signals",
        "",
        "| Client | Investment Signal | Source |",
        "|---|---|---|",
    ]
    for row in sorted(budget_rows, key=lambda r: -float(r.get("priority_score", 0) or 0))[:10]:
        lines.append(f"| {row['source']} | {row['budget_mention']} | {row['source_type']} |")

    if brief:
        lines += ["", "---", "", "## Research Questions", ""]
        q_labels = {
            "Q1": "Most active AI investors",
            "Q2": "Vendors and tools in use",
            "Q3": "Budget signals",
            "Q4": "Opportunities for AmaliTech",
        }
        for q_id in ["Q1","Q2","Q3","Q4"]:
            q = brief.get(q_id, {})
            lines += [
                f"**{q_id}: {q_labels[q_id]}**", "",
                q.get("answer","_Not available._"), "",
                f"> **AmaliTech implication:** {q.get('amalitech_implication','_Not available._')}",
                "",
            ]

    lines += ["---", "", f"*Built by the AmaliTech Benchmarking Team — {ts}.*", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"  Executive brief: {path.name}")


def write_potential_clients_md(all_rows: list[dict], all_folders: list, path: Path) -> None:
    """
    Sheet showing potential clients that match the profile of existing clients —
    based on industry overlap, AI maturity, and vendor stack similarity.
    """
    ts = datetime.now().strftime("%B %Y")

    # Load client metadata from site folders
    existing: dict[str, dict] = {}
    potential: dict[str, dict] = {}
    for folder in all_folders:
        meta_file = folder / "client_meta.json"
        if meta_file.exists():
            try:
                meta = json.loads(meta_file.read_text(encoding="utf-8"))
                name = meta.get("client_name", folder.name)
                if meta.get("client_type") == "existing":
                    existing[name] = meta
                else:
                    potential[name] = meta
            except Exception:
                pass

    # Build vendor profiles per client from rows
    by_source = defaultdict(list)
    for row in all_rows:
        by_source[row["source"]].append(row)

    def vendor_set(rows):
        return set(v.strip().lower() for r in rows for v in r.get("vendor_tools","").split(",") if v.strip())

    def maturity_score(rows):
        m = {"exploring": 1, "piloting": 2, "scaling": 3, "embedded": 4}
        scores = [m.get(r.get("maturity",""),0) for r in rows]
        return sum(scores) / len(scores) if scores else 0

    # For each potential client, find most similar existing clients
    lines = [
        "# Potential Clients — Profile Match Analysis",
        "",
        f"**Date:** {ts}  ",
        "",
        "This page identifies potential clients whose AI investment profile most closely matches",
        "AmaliTech's existing clients — indicating similar buying patterns and service needs.",
        "",
        "---",
        "",
    ]

    for pot_name in sorted(potential):
        pot_rows = by_source.get(pot_name, [])
        if not pot_rows:
            continue
        pot_vendors  = vendor_set(pot_rows)
        pot_maturity = maturity_score(pot_rows)
        pot_signals  = Counter(r["signal_type"] for r in pot_rows)

        # Score similarity against each existing client
        matches = []
        for ex_name in existing:
            ex_rows = by_source.get(ex_name, [])
            if not ex_rows:
                continue
            ex_vendors  = vendor_set(ex_rows)
            ex_maturity = maturity_score(ex_rows)
            # Jaccard similarity on vendor overlap
            overlap = len(pot_vendors & ex_vendors)
            union   = len(pot_vendors | ex_vendors) or 1
            vendor_sim = overlap / union
            maturity_sim = 1 - abs(pot_maturity - ex_maturity) / 4
            score = round((vendor_sim * 0.6 + maturity_sim * 0.4) * 100, 1)
            if score > 0:
                matches.append((ex_name, score, list(pot_vendors & ex_vendors)))

        matches.sort(key=lambda x: -x[1])
        top_matches = matches[:3]

        top_vendors_list = Counter(v.strip() for r in pot_rows for v in r.get("vendor_tools","").split(",") if v.strip()).most_common(5)
        budget = next((r["budget_mention"] for r in pot_rows if r.get("budget_mention","")), "none found")
        maturity_label = Counter(r["maturity"] for r in pot_rows if r.get("maturity")).most_common(1)

        lines += [
            f"## {pot_name}",
            "",
            f"**AI signals found:** {len(pot_rows)}  ",
            f"**AI maturity:** {maturity_label[0][0] if maturity_label else 'unknown'}  ",
            f"**Top vendors:** {', '.join(v for v,_ in top_vendors_list) or 'none identified'}  ",
            f"**Budget signal:** {budget}",
            "",
            "**Most similar existing clients:**",
            "",
            "| Existing Client | Match Score | Shared Vendors |",
            "|---|---|---|",
        ]
        for ex_name, score, shared in top_matches:
            lines.append(f"| {ex_name} | {score}% | {', '.join(shared[:5]) or 'none'} |")

        lines += [
            "",
            "**Top AI signals:**",
            "",
            "| Signal | Type | Maturity | Budget |",
            "|---|---|---|---|",
        ]
        for row in sorted(pot_rows, key=lambda r: -float(r.get("priority_score", 0) or 0))[:5]:
            lines.append(f"| {row['title']} | {row['signal_type']} | {row['maturity']} | {row.get('budget_mention','') or '—'} |")
        lines.append("")

    lines += ["---", "", f"*Client intelligence — {ts}.*", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"  Potential clients profile match: {path.name}")
