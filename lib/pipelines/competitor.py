"""
lib/pipelines/competitor.py — prompts, hypothesis tracking, scoring, row builder
for the competitor intelligence pipeline. Preserves all unique analyse_groq.py logic.
"""
from __future__ import annotations

import re
import textwrap

from lib.core import call_ai, parse_json, TIER_CONTENT
import logging
log = logging.getLogger("analyse")

SERVICE_CATEGORIES = [
    "AI Advisory & Readiness", "AI Engineering & Automation", "AI Platforms & Agents",
    "AI-powered Solutions & New Revenue Models", "Talent & Staffing", "Other",
]
MATURITY_LEVELS = [
    "AI Explorer", "AI Practitioner", "AI Champion",
    "AI Explorer → AI Practitioner", "AI Practitioner → AI Champion",
]
DIMENSIONS = ["market_impact","effort","scalability","revenue_potential",
              "market_credibility","talent_availability","strategic_fit"]

DIM_LABELS = {
    "market_impact":       "Market Impact",
    "effort":              "Effort (inverse)",
    "scalability":         "Scalability",
    "revenue_potential":   "Revenue Potential",
    "market_credibility":  "Market Credibility",
    "talent_availability": "Talent Availability",
    "strategic_fit":       "Strategic Fit",
}

HYPOTHESES = [
    "Competitors are charging an AI premium of 15–30% over baseline managed services rates.",
    "The fastest-growing competitors are pivoting from time-and-materials to outcome/value-based pricing.",
    "AI capability is being built primarily through hyperscaler partnerships rather than internal R&D.",
    "European enterprise buyers are prioritising data sovereignty and compliance-safe AI.",
    "Competitors are concentrating AI investment in 2–3 verticals rather than spreading across all sectors.",
]

_EXTRACTION_SYSTEM = textwrap.dedent(f"""
    You are a competitive intelligence analyst for AmaliTech — an AI-first
    technology services company from Ghana and Rwanda serving European enterprise clients.

    Extract ALL AI-related and AI-adjacent services from the competitor content. Be thorough.
    Return ONLY a valid JSON array. No markdown, no preamble.
    Each element must have EXACTLY these keys:
    {{
      "name": "", "category": "one of: {' | '.join(SERVICE_CATEGORIES)}",
      "customer_maturity": "one of: {' | '.join(MATURITY_LEVELS)}",
      "description": "1-3 sentences",
      "plain_english_summary": "1 sentence, jargon-free",
      "ai_classification": "core_ai or ai_adjacent",
      "pricing_signals": "any pricing model hints or AI premium mentions — or empty string",
      "clients": "named actual clients mentioned (e.g. BMW, Deutsche Telekom, Unilever) — or empty string",
      "industries": "industries this service targets or has been used in (e.g. Manufacturing, Telecommunications, Financial Services) — infer from context if not explicit",
      "tech_stack": "", "evidence": "", "source_url": ""
    }}
    If nothing relevant: []
""").strip()

_HYPOTHESIS_SYSTEM = (
    "You are a competitive intelligence analyst for AmaliTech. "
    "Given competitor content, assess evidence for/against each hypothesis. "
    "Return ONLY valid JSON:\n"
    '{"h1":{"evidence_for":"","evidence_against":"","verdict":"Confirmed|Refuted|Insufficient data"},'
    '"h2":{"evidence_for":"","evidence_against":"","verdict":"Confirmed|Refuted|Insufficient data"},'
    '"h3":{"evidence_for":"","evidence_against":"","verdict":"Confirmed|Refuted|Insufficient data"},'
    '"h4":{"evidence_for":"","evidence_against":"","verdict":"Confirmed|Refuted|Insufficient data"},'
    '"h5":{"evidence_for":"","evidence_against":"","verdict":"Confirmed|Refuted|Insufficient data"}}'
)

_SCORING_SYSTEM = (
    "You are scoring competitor AI services for AmaliTech's strategic priority matrix.\n"
    "AmaliTech: ~400 staff, Ghana+Rwanda delivery, ISO 27001/TISAX certified, AWS Advanced Partner.\n"
    "Effort is INVERSE (5=deliverable now, 1=2+ year roadmap).\n"
    "Return ONLY valid JSON:\n"
    '{"market_impact":{"score":0,"justification":""},"effort":{"score":0,"justification":""},'
    '"scalability":{"score":0,"justification":""},"revenue_potential":{"score":0,"justification":""},'
    '"market_credibility":{"score":0,"justification":""},"talent_availability":{"score":0,"justification":""},'
    '"strategic_fit":{"score":0,"justification":""}}'
)

_AI_KEYWORDS = re.compile(
    r"\b(ai|ml|machine learning|deep learning|llm|generative|automation|analytics|"
    r"data science|nlp|computer vision|predictive|intelligent|chatbot|copilot|"
    r"neural|model|inference|embedding|vector|rag|fine.tun)\b", re.IGNORECASE,
)


def data_confidence(content: str) -> str:
    length = len(content)
    if length >= 30_000: return "High"
    if length >= 8_000:  return "Medium"
    return "Low"


def _smart_excerpt(content: str, limit: int = TIER_CONTENT) -> str:
    blocks = content.split("\n\n---\n\n")
    scored = sorted(blocks, key=lambda b: len(_AI_KEYWORDS.findall(b)), reverse=True)
    result, used = [], 0
    for block in scored:
        take = block[:limit - used]
        result.append(take)
        used += len(take)
        if used >= limit:
            break
    return "\n\n---\n\n".join(result)


def _sanitise_services(services: list[dict]) -> list[dict]:
    for svc in services:
        if svc.get("customer_maturity","") not in MATURITY_LEVELS:
            raw = svc.get("customer_maturity","")
            svc["customer_maturity"] = next((m for m in MATURITY_LEVELS if m.lower() in raw.lower()), MATURITY_LEVELS[0])
        if svc.get("category","") not in SERVICE_CATEGORIES:
            svc["category"] = "Other"
    return services


def compute_score(scores: dict, weights: dict) -> float:
    total_w  = sum(weights.values()) or 1
    weighted = sum((scores.get(d,{}).get("score",0) if isinstance(scores.get(d),dict) else 0) * weights[d]
                   for d in DIMENSIONS)
    return round((weighted / (5 * total_w)) * 100, 1)


def priority_tier(score: float) -> str:
    return "High" if score >= 70 else ("Medium" if score >= 45 else "Low")


def priority_display(score: float) -> str:
    return f"{priority_tier(score)} ({score})"


def extract_services(name: str, content: str) -> list[dict]:
    log.info("  Extracting services...")
    raw    = call_ai(_EXTRACTION_SYSTEM, f"Competitor: {name}\n\nContent:\n{_smart_excerpt(content)}")
    result = parse_json(raw, context=name)
    if not isinstance(result, list):
        log.warning(f"  Unexpected extraction response for {name}.")
        return []
    result = _sanitise_services(result)
    log.info(f"  {len(result)} service(s) found.")
    return result


def assess_hypotheses(name: str, content: str) -> dict:
    log.info("  Assessing hypotheses...")
    hyp_context = "\n".join(f"H{i+1}: {h}" for i, h in enumerate(HYPOTHESES))
    raw    = call_ai(_HYPOTHESIS_SYSTEM, f"Competitor: {name}\nHypotheses:\n{hyp_context}\n\nContent:\n{_smart_excerpt(content)}")
    result = parse_json(raw, context=f"{name} hypotheses")
    return result if isinstance(result, dict) else {}


def score_service(competitor: str, service: dict) -> dict:
    user   = (f"Competitor: {competitor}\nService: {service.get('name','')}\n"
              f"Category: {service.get('category','')}\nCustomer Maturity: {service.get('customer_maturity','')}\n"
              f"Description: {service.get('description','')}\nAI Classification: {service.get('ai_classification','')}\n"
              "Score this service against AmaliTech's priority matrix.")
    raw    = call_ai(_SCORING_SYSTEM, user)
    result = parse_json(raw, context=service.get("name",""))
    return result if isinstance(result, dict) else {}


def build_rows(competitor: str, services: list[dict],
               all_scores: list[dict], weights: dict, confidence: str = "Medium") -> list[dict]:
    rows = []
    for svc, scores in zip(services, all_scores):
        score = compute_score(scores, weights)
        row   = {
            "competitor": competitor, "service_name": svc.get("name",""),
            "category": svc.get("category",""), "customer_maturity": svc.get("customer_maturity",""),
            "ai_classification": svc.get("ai_classification",""), "description": svc.get("description",""),
            "plain_english_summary": svc.get("plain_english_summary",""),
            "pricing_signals": svc.get("pricing_signals",""),
            "clients": svc.get("clients", svc.get("client_wins","")),  # support old key too
            "industries": svc.get("industries",""),
            "tech_stack": svc.get("tech_stack",""), "data_confidence": confidence,
            "evidence": svc.get("evidence",""), "source_url": svc.get("source_url",""),
            "priority_score": score, "priority_tier": priority_tier(score), "priority_display": priority_display(score),
        }
        for dim in DIMENSIONS:
            d = scores.get(dim, {})
            row[f"{dim}_score"]         = d.get("score","")         if isinstance(d, dict) else ""
            row[f"{dim}_justification"] = d.get("justification","") if isinstance(d, dict) else ""
        rows.append(row)
    rows.sort(key=lambda r: -r["priority_score"])
    return rows


# ── BRIEF GENERATION ──────────────────────────────────────────────────────────
_BRIEF_SYSTEM = textwrap.dedent("""
    You are a competitive intelligence analyst writing a brief for AmaliTech's leadership.
    AmaliTech: AI-first tech services, Ghana + Rwanda delivery, European enterprise clients
    (manufacturing, telecoms). ISO 27001/TISAX certified, AWS Advanced Partner.

    Answer each question with a direct, evidence-based answer (3-5 sentences).
    Name specific competitors, clients, and industries where relevant.
    End each answer with an AmaliTech implication.
""").strip()

_BRIEF_USER = textwrap.dedent("""
    Based on the competitor research content below, answer each question.

    Return ONLY valid JSON:
    {{
      "Q1": {{
        "question": "Which competitors are most active in AI services and what are their key offerings?",
        "answer": "",
        "amalitech_implication": ""
      }},
      "Q2": {{
        "question": "What clients and industries are competitors winning AI work in?",
        "answer": "",
        "amalitech_implication": ""
      }},
      "Q3": {{
        "question": "What pricing models and signals are visible across competitors?",
        "answer": "",
        "amalitech_implication": ""
      }},
      "Q4": {{
        "question": "Where are the clearest gaps or opportunities for AmaliTech to compete?",
        "answer": "",
        "amalitech_implication": ""
      }}
    }}

    Research content:
    {content}
""").strip()


def generate_brief(all_content: str) -> dict:
    from lib.core import parse_json
    log.info("  Generating competitor research brief...")
    raw    = call_ai(_BRIEF_SYSTEM, _BRIEF_USER.format(content=all_content[:6_000]))
    result = parse_json(raw, context="competitor_brief")
    return result if isinstance(result, dict) else {}


def write_brief_md(brief: dict, all_rows: list[dict], path) -> None:
    from collections import defaultdict
    from datetime import datetime
    ts = datetime.now().strftime("%B %Y")
    lines = [
        "# Competitor Intelligence Research Brief",
        "", f"**Date:** {ts}  ", "",  "---", "",
    ]
    q_labels = {
        "Q1": "Most active competitors and key offerings",
        "Q2": "Clients and industries competitors are winning",
        "Q3": "Pricing models and signals",
        "Q4": "Gaps and opportunities for AmaliTech",
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
    # Service table by competitor
    by_comp = defaultdict(list)
    for row in all_rows:
        by_comp[row.get("competitor","")].append(row)
    lines += ["## Full Service List by Competitor", ""]
    n = 1
    for comp in sorted(by_comp):
        rows = sorted(by_comp[comp], key=lambda r: -float(r.get("priority_score",0) or 0))
        lines += [f"### {comp}", "",
                  "| # | Service | Category | Clients | Industries | Priority |",
                  "|---|---|---|---|---|---|"]
        for row in rows:
            lines.append(
                f"| {n} | {row.get('service_name','')} | {row.get('category','')} "
                f"| {row.get('clients','')} | {row.get('industries','')} "
                f"| {row.get('priority_display','')} |"
            )
            n += 1
        lines.append("")
    lines += ["---", "", f"*Competitor research — {ts}.*", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"  Research brief: {path.name}")


def write_market_summary_md(all_rows: list[dict], path) -> None:
    """Detailed market summary: categories, clients, industries, pricing, hypotheses, top services."""
    from collections import defaultdict, Counter
    from datetime import datetime
    import json as _json
    ts = datetime.now().strftime("%B %Y")

    by_comp: dict[str, list] = defaultdict(list)
    for row in all_rows:
        by_comp[row.get("competitor","")].append(row)

    cat_counts    = Counter(r.get("category","") for r in all_rows)
    all_clients   = [c.strip() for r in all_rows for c in r.get("clients","").split(",") if c.strip()]
    all_industries= [i.strip() for r in all_rows for i in r.get("industries","").split(",") if i.strip()]
    client_counts = Counter(all_clients).most_common(20)
    industry_counts = Counter(all_industries).most_common(15)
    top10 = sorted(all_rows, key=lambda r: -float(r.get("priority_score",0) or 0))[:10]

    # Pricing signals aggregated
    pricing_signals = [r.get("pricing_signals","").strip() for r in all_rows if r.get("pricing_signals","").strip()]

    # Hypothesis verdicts from file
    from pathlib import Path as _Path
    import os as _os
    _script_dir = _Path(_os.getenv("APP_DIR", _Path(__file__).parent.parent.parent.resolve()))
    _out_dir    = _Path(_os.getenv("OUTPUT_DIR", _script_dir / "output"))
    _ai_backend = _os.getenv("AI_BACKEND","groq").lower().strip()
    hyp_file    = _out_dir / f"hypothesis_tracker_{_ai_backend}.json"
    hyp_data: dict = {}
    if hyp_file.exists():
        try:
            hyp_data = _json.loads(hyp_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    lines = [
        "# Competitor AI Services — Market Summary",
        "",
        f"**Based on:** {len(all_rows)} services across {len(by_comp)} competitors  ",
        f"**Date:** {ts}",
        "",
        "---",
        "",
        "## 1. Services by Category",
        "",
        "| Category | # Services | % of Total | Top Competitors |",
        "|---|---|---|---|",
    ]
    for cat, cnt in cat_counts.most_common():
        pct = round(cnt / len(all_rows) * 100)
        top_comps = Counter(
            r.get("competitor","") for r in all_rows if r.get("category","") == cat
        ).most_common(3)
        top_str = ", ".join(f"{c} ({n})" for c, n in top_comps)
        lines.append(f"| {cat} | {cnt} | {pct}% | {top_str} |")

    # Per-category breakdown
    lines += ["", "---", "", "## 2. Breakdown by Service Category", ""]
    for cat, cnt in cat_counts.most_common():
        cat_rows = [r for r in all_rows if r.get("category","") == cat]
        cat_rows_sorted = sorted(cat_rows, key=lambda r: -float(r.get("priority_score",0) or 0))
        cat_clients   = [c.strip() for r in cat_rows for c in r.get("clients","").split(",") if c.strip()]
        cat_industries= [i.strip() for r in cat_rows for i in r.get("industries","").split(",") if i.strip()]
        cat_pricing   = [r.get("pricing_signals","").strip() for r in cat_rows if r.get("pricing_signals","").strip()]
        top_cat_clients   = Counter(cat_clients).most_common(5)
        top_cat_industries= Counter(cat_industries).most_common(5)

        lines += [
            f"### {cat}",
            "",
            f"**{cnt} services** across {Counter(r.get('competitor','') for r in cat_rows).total() and len(set(r.get('competitor','') for r in cat_rows))} competitors",
            "",
            "**Top services by priority:**",
            "",
            "| Competitor | Service | Clients | Industries | Priority |",
            "|---|---|---|---|---|",
        ]
        for row in cat_rows_sorted[:8]:
            lines.append(
                f"| {row.get('competitor','')} | {row.get('service_name','')} "
                f"| {row.get('clients','')} | {row.get('industries','')} "
                f"| {row.get('priority_display','')} |"
            )
        if top_cat_clients:
            lines += ["", f"**Named clients:** {', '.join(c for c,_ in top_cat_clients)}"]
        if top_cat_industries:
            lines += [f"**Industries:** {', '.join(i for i,_ in top_cat_industries)}"]
        if cat_pricing:
            lines += [f"**Pricing signals:** {' | '.join(cat_pricing[:3])}"]
        lines += [""]

    lines += ["---", "", "## 3. Clients Mentioned Across Competitors", "",
              "| Client | # Mentions |", "|---|---|"]
    for client, cnt in client_counts:
        lines.append(f"| {client} | {cnt} |")
    lines += [
        "",
        "> Named clients on competitor websites = active AI buyers. Cross-reference against AmaliTech's account list.",
        "",
        "---", "", "## 4. Industries Targeted",
        "", "| Industry | # Mentions |", "|---|---|",
    ]
    for ind, cnt in industry_counts:
        lines.append(f"| {ind} | {cnt} |")
    lines += [
        "", "> AmaliTech focus: Manufacturing, Telecommunications, E-commerce.",
        "",
        "---", "", "## 5. Pricing Signals",
        "",
        "Pricing signals extracted directly from competitor content:",
        "",
    ]
    if pricing_signals:
        for sig in pricing_signals[:20]:
            lines.append(f"- {sig}")
    else:
        lines.append("_No explicit pricing signals found in scraped content._")

    # Hypothesis verdicts summary
    if hyp_data:
        lines += ["", "---", "", "## 6. Hypothesis Tracker — Aggregate Verdicts", ""]
        verdict_totals: dict[str, Counter] = defaultdict(Counter)
        for comp_data in hyp_data.values():
            for h_key, h_val in comp_data.items():
                verdict_totals[h_key][h_val.get("verdict","Insufficient data")] += 1

        for i, hyp_text in enumerate(HYPOTHESES, 1):
            h_key = f"h{i}"
            totals = verdict_totals.get(h_key, Counter())
            confirmed = totals.get("Confirmed", 0)
            refuted   = totals.get("Refuted", 0)
            insuff    = totals.get("Insufficient data", 0)
            total     = confirmed + refuted + insuff or 1
            verdict   = "Confirmed" if confirmed > refuted and confirmed > insuff else \
                        "Refuted" if refuted > confirmed and refuted > insuff else "Mixed / Insufficient"
            lines += [
                f"**H{i}:** {hyp_text}",
                "",
                f"Confirmed: {confirmed} | Refuted: {refuted} | Insufficient: {insuff} — **Overall: {verdict}**",
                "",
            ]

    lines += [
        "---", "", "## 7. Top 10 Services by Priority Score",
        "", "| # | Competitor | Service | Category | Clients | Industries | Score |",
        "|---|---|---|---|---|---|---|",
    ]
    for i, row in enumerate(top10, 1):
        lines.append(
            f"| {i} | {row.get('competitor','')} | {row.get('service_name','')} "
            f"| {row.get('category','')} | {row.get('clients','')} "
            f"| {row.get('industries','')} | {row.get('priority_display','')} |"
        )
    lines += ["", "---", "", f"*Analysis based on scraped competitor data — {ts}.*", ""]
    path.write_text("\n".join(lines), encoding="utf-8")
    log.info(f"  Market summary: {path.name}")


def write_executive_brief_md(brief: dict, all_rows: list[dict], path) -> None:
    """C-suite executive brief for competitor analysis."""
    from collections import defaultdict, Counter
    from datetime import datetime
    ts = datetime.now().strftime("%B %Y")

    by_comp = defaultdict(list)
    for row in all_rows:
        by_comp[row.get("competitor","")].append(row)

    all_clients    = [c.strip() for r in all_rows for c in r.get("clients","").split(",") if c.strip()]
    all_industries = [i.strip() for r in all_rows for i in r.get("industries","").split(",") if i.strip()]
    top_clients    = Counter(all_clients).most_common(5)
    top_industries = Counter(all_industries).most_common(5)

    lines = [
        "# Competitor AI Intelligence — Executive Brief",
        "", f"**Prepared for:** AmaliTech Leadership  ",
        f"**Date:** {ts}  ", f"**Classification:** Internal", "", "---", "",
        "## What We Did",
        "",
        f"We automatically scraped and analysed **{len(by_comp)} competitor websites**, "
        f"extracting **{len(all_rows)} AI services** using LLM-based analysis. "
        "The system identifies services, scores them against AmaliTech's strategic priorities, "
        "tracks named clients and target industries, and assesses five strategic hypotheses.",
        "", "---", "", "## Key Findings", "",
    ]

    # Hypothesis summary
    lines += [
        "### Strategic Hypotheses",
        "",
        "Five hypotheses were tested against competitor content:",
        "",
    ]
    for i, h in enumerate(HYPOTHESES, 1):
        lines.append(f"{i}. {h}")
    lines += ["", "*(See Hypothesis Tracker sheet in the Excel workbook for per-competitor verdicts.)*", ""]

    # Top clients
    lines += [
        "### Clients Competitors Are Winning",
        "",
        "The most frequently named clients across competitor websites:",
        "",
        "| Client | # Competitors Mentioning |",
        "|---|---|",
    ]
    for client, cnt in top_clients:
        lines.append(f"| {client} | {cnt} |")
    lines += [
        "", "> These are live AI spending signals — companies actively buying AI services from competitors.",
        "> AmaliTech should prioritise outreach to these organisations.", "",
    ]

    # Top industries
    lines += [
        "### Industries with Most AI Activity",
        "",
        "| Industry | # Mentions |",
        "|---|---|",
    ]
    for ind, cnt in top_industries:
        lines.append(f"| {ind} | {cnt} |")
    lines += [""]

    # Brief Q&A
    if brief:
        lines += ["---", "", "## Research Questions", ""]
        q_labels = {
            "Q1": "Most active competitors",
            "Q2": "Clients and industries being won",
            "Q3": "Pricing signals",
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
