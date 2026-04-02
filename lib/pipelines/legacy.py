"""lib/pipelines/legacy.py — prompts, scoring, row builder, brief for legacy modernisation pipeline."""
from __future__ import annotations

import re
import textwrap
from collections import defaultdict
from datetime import datetime
from pathlib import Path

from lib.core import call_ai, parse_json, TIER_CONTENT, AI_BACKEND
import logging
log = logging.getLogger("analyse")

DIMENSIONS = ["market_impact","effort","scalability","revenue_potential","market_credibility","talent_availability","strategic_fit"]
DIM_LABELS = {"market_impact":"Market Impact","effort":"Effort (inverse: 5=low effort)","scalability":"Scalability",
              "revenue_potential":"Revenue Potential","market_credibility":"Market Credibility",
              "talent_availability":"Talent Availability","strategic_fit":"Strategic Fit"}

_DIM_GUIDE = "\n".join(f"- {DIM_LABELS[d]}" for d in DIMENSIONS)

_EXTRACTION_SYSTEM = textwrap.dedent("""
    You are a competitive intelligence analyst for AmaliTech.
    Extract ALL services and products related to legacy system modernisation using AI:
    1. COBOL / mainframe modernisation
    2. Java version migration (8/11 → 17/21)
    3. AI-assisted refactoring tools and platforms
    4. General legacy application modernisation where AI is a key component
    5. Academic research or case studies about effectiveness

    For each return:
    {"name":"","source":"","type":"service|product|tool|research",
     "category":"","description":"2-4 sentences","maturity_level":"experimental|emerging|established|proven",
     "evidence":"","source_url":"","academic_research":""}

    Return ONLY a valid JSON array. If nothing relevant: []
""").strip()

_SCORING_SYSTEM = textwrap.dedent("""
    Score legacy modernisation services against AmaliTech's priority matrix (1-5 per dimension).
    AmaliTech: AI-first tech services, Ghana+Rwanda delivery, European enterprise clients,
    AWS Advanced Partner, TISAX certified, strong Java/Python/cloud capability.
    Effort is INVERSE: 5=low effort (fast to launch), 1=very high effort.
    Return ONLY valid JSON:
    {"market_impact":{"score":0,"justification":""},"effort":{"score":0,"justification":""},
     "scalability":{"score":0,"justification":""},"revenue_potential":{"score":0,"justification":""},
     "market_credibility":{"score":0,"justification":""},"talent_availability":{"score":0,"justification":""},
     "strategic_fit":{"score":0,"justification":""}}
""").strip()

_BRIEF_SYSTEM = "You are a research analyst writing a concise brief for AmaliTech's leadership on AI-assisted legacy system modernisation. Be concrete and evidence-based."

_BRIEF_USER = textwrap.dedent("""
    Answer each question based on the research content. Return ONLY valid JSON:
    {{"Q1":{{"question":"Who is doing AI-assisted legacy/mainframe/COBOL modernisation?","answer":"","amalitech_implication":""}},
      "Q2":{{"question":"Does it work — what is the maturity level?","answer":"","amalitech_implication":""}},
      "Q3":{{"question":"What state-of-the-art tools and approaches exist (including academic)?","answer":"","amalitech_implication":""}},
      "Q4":{{"question":"What exists for Java 8/11 → 17/21 migration using AI?","answer":"","amalitech_implication":""}}}}
    Research content:
    {content}
""").strip()


def extract_services(name: str, content: str) -> list[dict]:
    log.info("  Extracting legacy modernisation services...")
    raw    = call_ai(_EXTRACTION_SYSTEM, f"Source: {name}\n\nContent:\n{content[:TIER_CONTENT]}")
    result = parse_json(raw, context=name)
    if not isinstance(result, list):
        log.warning(f"  Unexpected extraction response for {name}.")
        return []
    log.info(f"  {len(result)} service(s) found.")
    return result


def score_service(source: str, service: dict) -> dict:
    user = (f"Source: {source}\nService: {service.get('name','')}\nType: {service.get('type','')}\n"
            f"Description: {service.get('description','')}\nMaturity: {service.get('maturity_level','')}")
    raw    = call_ai(_SCORING_SYSTEM, user, max_tokens=1024)
    result = parse_json(raw, context=service.get("name",""))
    return result if isinstance(result, dict) else {}


def compute_score(scores: dict, weights: dict) -> float:
    total_w  = sum(weights.values()) or 1
    weighted = sum((scores.get(d,{}).get("score",0) if isinstance(scores.get(d),dict) else 0) * weights[d]
                   for d in DIMENSIONS)
    return round((weighted / (5 * total_w)) * 100, 1)


def priority_tier(score: float) -> str:
    return "High" if score >= 70 else ("Medium" if score >= 45 else "Low")


def build_rows(source: str, services: list[dict], all_scores: list[dict], weights: dict) -> list[dict]:
    rows = []
    for svc, scores in zip(services, all_scores):
        score = compute_score(scores, weights)
        row   = {
            "source": source, "service_name": svc.get("name",""), "type": svc.get("type",""),
            "category": svc.get("category",""), "maturity_level": svc.get("maturity_level",""),
            "description": svc.get("description",""), "evidence": svc.get("evidence",""),
            "academic_research": svc.get("academic_research",""), "source_url": svc.get("source_url",""),
            "priority_score": score, "priority_tier": priority_tier(score),
        }
        for dim in DIMENSIONS:
            d = scores.get(dim, {})
            row[f"{dim}_score"]         = d.get("score","") if isinstance(d,dict) else ""
            row[f"{dim}_justification"] = d.get("justification","") if isinstance(d,dict) else ""
        rows.append(row)
    rows.sort(key=lambda r: -r["priority_score"])
    return rows


def generate_brief(all_content: str) -> dict:
    log.info("  Generating research brief...")
    raw    = call_ai(_BRIEF_SYSTEM, _BRIEF_USER.format(content=all_content[:TIER_CONTENT]))
    result = parse_json(raw, context="research_brief")
    return result if isinstance(result, dict) else {}


def write_brief_md(brief: dict, all_rows: list[dict], path: Path) -> None:
    ts = datetime.now().strftime("%B %Y")
    q_labels = {"Q1":"Who is doing this?","Q2":"Does it work?","Q3":"State-of-the-art tools","Q4":"Java 8/11 → 17/21 migration"}
    lines = ["# Legacy Modernisation Research Brief", "",
             f"**Date:** {ts}  ", f"**AI backend:** {AI_BACKEND.upper()}  ", "", "---", ""]
    for q_id in ["Q1","Q2","Q3","Q4"]:
        q = brief.get(q_id, {})
        lines += [f"## {q_id}: {q_labels[q_id]}", "", f"**Question:** {q.get('question','')}",
                  "", q.get("answer","_No answer generated._"), "",
                  f"> **AmaliTech implication:** {q.get('amalitech_implication','_Not available._')}",
                  "", "---", ""]
    lines += ["## Full Scored Service List", ""]
    grouped: dict[str, list] = defaultdict(list)
    for row in all_rows:
        grouped[row["source"]].append(row)
    n = 1
    for src in sorted(grouped):
        lines += [f"### {src}", "", "| # | Service | Focus | Maturity | Priority |", "|---|---|---|---|---|"]
        for row in sorted(grouped[src], key=lambda r: -float(r.get("priority_score",0))):
            lines.append(f"| {n} | {row.get('service_name','')} | {row.get('category','')} | {row.get('maturity_level','')} | {row.get('priority_tier','')} ({row.get('priority_score','')}) |")
            n += 1
        lines.append("")
    lines += ["---", "", f"*Research based on scraped website data — {ts}.*", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"  Research brief: {path.name}")
