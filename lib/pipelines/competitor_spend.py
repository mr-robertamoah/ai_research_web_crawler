"""lib/pipelines/competitor_spend.py — extraction, scoring, and output for competitor AI spend intelligence."""
from __future__ import annotations

import json
import re
import textwrap
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path

from lib.core import call_ai, parse_json, TIER_CONTENT, AI_BACKEND
import logging
log = logging.getLogger("analyse")

DIMENSIONS: list = []  # no scoring matrix — priority is rule-based

SPEND_TYPES = [
    "vendor_partnership",
    "acquisition",
    "internal_r&d",
    "hiring_signal",
    "pricing_strategy",
    "platform_investment",
    "other",
]

_EXTRACTION_SYSTEM = textwrap.dedent("""
    You are a competitive intelligence analyst for AmaliTech — an AI-first tech services company
    serving European enterprise clients. You are analysing what AI investments and spending
    competitors are making INTERNALLY — not what services they sell, but what they are BUYING,
    BUILDING, or INVESTING IN themselves.

    Extract ALL signals about competitor AI spending, investment, and capability building.

    For each signal return a JSON object with EXACTLY these keys:
    {
      "spend_type": "one of: vendor_partnership | acquisition | internal_r&d | hiring_signal | pricing_strategy | platform_investment | other",
      "title": "short descriptive title (e.g. 'Partnership with Microsoft Azure AI')",
      "vendor_or_target": "the vendor, platform, or acquisition target named — or empty string",
      "description": "2-3 sentences — what the investment/spend is, what it signals about their strategy",
      "investment_signal": "any specific amount, scale, or commitment mentioned (e.g. '$50M investment', '500 AI engineers', '3-year deal') — or empty string",
      "strategic_intent": "what capability or market position this investment is building toward",
      "pricing_implication": "if this spend signal relates to their pricing model or strategy, explain — else empty string",
      "maturity": "one of: exploring | piloting | scaling | embedded",
      "evidence": "direct quote or specific fact from the content",
      "source_url": "URL where found, or empty string"
    }

    Focus on:
    - Vendor partnerships and platform deals (Azure, AWS, Google, Databricks, OpenAI, etc.)
    - Acquisitions or investments in AI companies
    - Internal R&D and proprietary AI platform development
    - Hiring patterns that reveal AI capability build (job postings mentioning specific tools)
    - Pricing model signals (outcome-based, subscription, usage-based shifts)
    - Platform or ecosystem investments

    Return ONLY a valid JSON array. No markdown, no preamble.
    If nothing relevant found return: []
""").strip()

_BRIEF_SYSTEM = textwrap.dedent("""
    You are a strategic analyst writing a brief for AmaliTech's leadership on
    where AI competitors are spending their money and what this means for pricing strategy.
    AmaliTech: AI-first tech services, Ghana + Rwanda delivery, European enterprise clients.
    Be direct and evidence-based. Focus on pricing and service offering implications.
""").strip()

_BRIEF_USER = textwrap.dedent("""
    Based on the competitor AI spend intelligence below, answer each question.
    Return ONLY valid JSON:
    {{
      "Q1": {{"question": "Where are competitors investing most heavily in AI — vendors, platforms, or internal R&D?",
              "answer": "", "amalitech_implication": ""}},
      "Q2": {{"question": "What do competitor spending patterns reveal about their future service offerings?",
              "answer": "", "amalitech_implication": ""}},
      "Q3": {{"question": "How does competitor AI spending influence their pricing models and strategy?",
              "answer": "", "amalitech_implication": ""}},
      "Q4": {{"question": "Where are the gaps — what are competitors NOT investing in that AmaliTech could exploit?",
              "answer": "", "amalitech_implication": ""}}
    }}
    Research content:
    {content}
""").strip()


def _priority_score(signal: dict) -> tuple[float, str]:
    weights = {
        "acquisition": 90,
        "vendor_partnership": 80,
        "internal_r&d": 75,
        "platform_investment": 70,
        "pricing_strategy": 85,
        "hiring_signal": 60,
        "other": 40,
    }
    base = weights.get(signal.get("spend_type", "other"), 40)
    if signal.get("investment_signal", "").strip():
        base = min(100, base + 10)
    tier = "High" if base >= 75 else ("Medium" if base >= 60 else "Low")
    return float(base), tier


def extract_services(name: str, content: str) -> list[dict]:
    log.info("  Extracting competitor spend signals...")
    raw    = call_ai(_EXTRACTION_SYSTEM, f"Competitor: {name}\n\nContent:\n{content[:TIER_CONTENT]}")
    result = parse_json(raw, context=name)
    if not isinstance(result, list):
        log.warning(f"  Unexpected extraction response for {name}.")
        return []
    log.info(f"  {len(result)} spend signal(s) found.")
    return result


def build_rows(source: str, services: list[dict]) -> list[dict]:
    rows = []
    for sig in services:
        score, tier = _priority_score(sig)
        rows.append({
            "source":             source,
            "spend_type":         sig.get("spend_type", ""),
            "title":              sig.get("title", ""),
            "vendor_or_target":   sig.get("vendor_or_target", ""),
            "description":        sig.get("description", ""),
            "investment_signal":  sig.get("investment_signal", ""),
            "strategic_intent":   sig.get("strategic_intent", ""),
            "pricing_implication":sig.get("pricing_implication", ""),
            "maturity":           sig.get("maturity", ""),
            "evidence":           sig.get("evidence", ""),
            "source_url":         sig.get("source_url", ""),
            "priority_score":     score,
            "priority_tier":      tier,
        })
    rows.sort(key=lambda r: -r["priority_score"])
    return rows


def generate_brief(all_content: str) -> dict:
    log.info("  Generating competitor spend brief...")
    raw    = call_ai(_BRIEF_SYSTEM, _BRIEF_USER.format(content=all_content[:TIER_CONTENT]))
    result = parse_json(raw, context="competitor_spend_brief")
    return result if isinstance(result, dict) else {}


def write_brief_md(brief: dict, all_rows: list[dict], path: Path) -> None:
    ts = datetime.now().strftime("%B %Y")
    lines = [
        "# Competitor AI Spend Intelligence — Research Brief",
        "", f"**Date:** {ts}  ", f"**AI backend:** {AI_BACKEND.upper()}  ", "", "---", "",
    ]
    q_labels = {
        "Q1": "Where competitors are investing most heavily",
        "Q2": "What spending patterns reveal about future offerings",
        "Q3": "How spending influences pricing strategy",
        "Q4": "Gaps AmaliTech can exploit",
    }
    for q_id in ["Q1", "Q2", "Q3", "Q4"]:
        q = brief.get(q_id, {})
        lines += [
            f"## {q_id}: {q_labels[q_id]}", "",
            f"**Question:** {q.get('question', '')}", "",
            q.get("answer", "_No answer generated._"), "",
            f"> **AmaliTech implication:** {q.get('amalitech_implication', '_Not available._')}",
            "", "---", "",
        ]
    # Signal table by competitor
    by_source = defaultdict(list)
    for row in all_rows:
        by_source[row["source"]].append(row)
    lines += ["## Full Signal List by Competitor", ""]
    n = 1
    for src in sorted(by_source):
        rows = sorted(by_source[src], key=lambda r: -float(r.get("priority_score", 0) or 0))
        lines += [f"### {src}", "",
                  "| # | Spend Type | Title | Vendor/Target | Investment Signal | Pricing Implication | Priority |",
                  "|---|---|---|---|---|---|---|"]
        for row in rows:
            lines.append(
                f"| {n} | {row['spend_type']} | {row['title']} "
                f"| {row['vendor_or_target']} | {row['investment_signal']} "
                f"| {row['pricing_implication'][:80] if row['pricing_implication'] else '—'} "
                f"| {row['priority_tier']} |"
            )
            n += 1
        lines.append("")
    lines += ["---", "", f"*Competitor spend intelligence — {ts}. AI backend: {AI_BACKEND.upper()}.*", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"  Research brief: {path.name}")


def write_market_summary_md(all_rows: list[dict], path: Path) -> None:
    ts = datetime.now().strftime("%B %Y")
    by_source = defaultdict(list)
    for row in all_rows:
        by_source[row["source"]].append(row)

    spend_type_counts = Counter(r["spend_type"] for r in all_rows)
    vendor_counts     = Counter(r["vendor_or_target"].strip() for r in all_rows if r.get("vendor_or_target","").strip())
    investment_rows   = [r for r in all_rows if r.get("investment_signal","").strip()]
    pricing_rows      = [r for r in all_rows if r.get("pricing_implication","").strip()]

    lines = [
        "# Competitor AI Spend Intelligence — Market Summary",
        "", f"**Based on:** {len(all_rows)} spend signals across {len(by_source)} competitors  ",
        f"**Date:** {ts}", "", "---", "",
        "## 1. Spend Type Breakdown",
        "", "| Spend Type | # Signals | What It Means |",
        "|---|---|---|",
        f"| vendor_partnership | {spend_type_counts.get('vendor_partnership',0)} | Buying AI capability from external platforms |",
        f"| acquisition | {spend_type_counts.get('acquisition',0)} | Acquiring AI companies or IP |",
        f"| internal_r&d | {spend_type_counts.get('internal_r&d',0)} | Building proprietary AI capability |",
        f"| hiring_signal | {spend_type_counts.get('hiring_signal',0)} | Scaling AI talent — reveals tool stack |",
        f"| pricing_strategy | {spend_type_counts.get('pricing_strategy',0)} | Signals in pricing model evolution |",
        f"| platform_investment | {spend_type_counts.get('platform_investment',0)} | Investing in AI platform/ecosystem |",
        "",
        "---", "", "## 2. Most Referenced Vendors & Platforms",
        "", "| Vendor / Platform | # Competitor Mentions |", "|---|---|",
    ]
    for vendor, cnt in vendor_counts.most_common(20):
        lines.append(f"| {vendor} | {cnt} |")

    lines += [
        "", "> Vendors appearing here are being actively used or partnered with by competitors.",
        "> AmaliTech should position services that complement or extend these platforms.",
        "",
        "---", "", "## 3. Investment Signals (Specific Amounts or Scale)",
        "", "| Competitor | Signal | Spend Type | Strategic Intent |",
        "|---|---|---|---|",
    ]
    for row in sorted(investment_rows, key=lambda r: -float(r.get("priority_score", 0) or 0)):
        lines.append(f"| {row['source']} | {row['investment_signal']} | {row['spend_type']} | {row['strategic_intent'][:100]} |")

    lines += [
        "", "---", "", "## 4. Pricing Strategy Signals",
        "", "| Competitor | Pricing Signal | Implication |",
        "|---|---|---|",
    ]
    for row in pricing_rows:
        lines.append(f"| {row['source']} | {row['title']} | {row['pricing_implication'][:120]} |")

    lines += [
        "", "---", "", "## 5. Per-Competitor Spend Profile",
        "",
    ]
    for src in sorted(by_source):
        rows = by_source[src]
        top_vendors = Counter(r["vendor_or_target"].strip() for r in rows if r.get("vendor_or_target","").strip()).most_common(3)
        top_types   = Counter(r["spend_type"] for r in rows).most_common(3)
        maturity    = Counter(r["maturity"] for r in rows if r.get("maturity")).most_common(1)
        investment  = next((r["investment_signal"] for r in rows if r.get("investment_signal","").strip()), "none found")
        lines += [
            f"### {src}",
            "",
            f"**Signals found:** {len(rows)}  ",
            f"**AI maturity:** {maturity[0][0] if maturity else 'unknown'}  ",
            f"**Top vendors/platforms:** {', '.join(v for v,_ in top_vendors) or 'none identified'}  ",
            f"**Dominant spend type:** {', '.join(t for t,_ in top_types)}  ",
            f"**Investment signal:** {investment}",
            "",
            "| Signal | Spend Type | Pricing Implication |",
            "|---|---|---|",
        ]
        for row in sorted(rows, key=lambda r: -float(r.get("priority_score", 0) or 0))[:5]:
            lines.append(f"| {row['title']} | {row['spend_type']} | {row['pricing_implication'][:80] if row['pricing_implication'] else '—'} |")
        lines.append("")

    lines += ["---", "", f"*Competitor spend intelligence — {ts}. AI backend: {AI_BACKEND.upper()}.*", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"  Market summary: {path.name}")


def write_executive_brief_md(brief: dict, all_rows: list[dict], path: Path) -> None:
    ts = datetime.now().strftime("%B %Y")
    by_source = defaultdict(list)
    for row in all_rows:
        by_source[row["source"]].append(row)

    vendor_counts   = Counter(r["vendor_or_target"].strip() for r in all_rows if r.get("vendor_or_target","").strip())
    pricing_rows    = [r for r in all_rows if r.get("pricing_implication","").strip()]
    investment_rows = [r for r in all_rows if r.get("investment_signal","").strip()]

    lines = [
        "# Competitor AI Spend Intelligence — Executive Brief",
        "", f"**Prepared for:** AmaliTech Leadership  ",
        f"**Date:** {ts}  ", f"**Classification:** Internal", "", "---", "",
        "## What We Did",
        "",
        f"We scraped and analysed **{len(by_source)} competitor websites** to extract signals about "
        f"where competitors are spending money on AI — not what they sell, but what they are buying, "
        f"building, and investing in. This reveals their capability roadmap and pricing strategy direction.",
        "",
        f"**Total signals:** {len(all_rows)} across {len(by_source)} competitors",
        "",
        "---", "", "## Top AI Vendors Competitors Are Using",
        "", "| Vendor / Platform | # Competitors Using |", "|---|---|",
    ]
    for vendor, cnt in vendor_counts.most_common(8):
        lines.append(f"| {vendor} | {cnt} |")
    lines += [
        "", "> These are the platforms competitors are building on.",
        "> AmaliTech should position as the expert integrator for these platforms in European enterprise.",
        "",
        "---", "", "## Investment Signals",
        "", "| Competitor | Investment | Strategic Intent |", "|---|---|---|",
    ]
    for row in sorted(investment_rows, key=lambda r: -float(r.get("priority_score", 0) or 0))[:10]:
        lines.append(f"| {row['source']} | {row['investment_signal']} | {row['strategic_intent'][:100]} |")

    lines += ["", "---", "", "## Pricing Strategy Signals",
              "", "| Competitor | Signal | Pricing Implication |", "|---|---|---|"]
    for row in pricing_rows[:10]:
        lines.append(f"| {row['source']} | {row['title']} | {row['pricing_implication'][:120]} |")

    if brief:
        lines += ["", "---", "", "## Research Questions", ""]
        q_labels = {
            "Q1": "Where competitors invest most",
            "Q2": "Future service offering signals",
            "Q3": "Pricing strategy implications",
            "Q4": "Gaps for AmaliTech",
        }
        for q_id in ["Q1", "Q2", "Q3", "Q4"]:
            q = brief.get(q_id, {})
            lines += [
                f"**{q_id}: {q_labels[q_id]}**", "",
                q.get("answer", "_Not available._"), "",
                f"> **AmaliTech implication:** {q.get('amalitech_implication', '_Not available._')}",
                "",
            ]

    lines += ["---", "", f"*Built by the AmaliTech Benchmarking Team — {ts}.*", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"  Executive brief: {path.name}")
