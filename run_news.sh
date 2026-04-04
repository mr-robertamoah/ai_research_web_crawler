#!/bin/bash
# run_news.sh — wrapper for cron to run the news monitoring pipeline
# Runs scrape + analyse in sequence. Safe to call even if container is stopped.
#
# Cron setup (twice daily at 08:00 and 18:00 UTC):
#   0 8,18 * * * /path/to/ai_research_web_crawler/run_news.sh >> /path/to/ai_research_web_crawler/news_cron.log 2>&1
#
# Environment variables read from .env in the project directory.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# Load .env
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

echo "=== News monitoring run started at $(date -u '+%Y-%m-%d %H:%M UTC') ==="

# Ensure container is running
docker compose up -d --quiet-pull 2>/dev/null || true

# Step 1: Scrape
echo "--- Scraping news sources ---"
docker compose exec -T \
  -e SCRAPE_MODE=news_monitoring \
  -e MAX_DEPTH=1 \
  -e MIN_RELEVANCE=1 \
  -e FRESH=1 \
  -e NEWS_SITES_DIR=/app/news_sites \
  scraper python3 /app/input/scraper_new.py

# Step 2: Analyse
echo "--- Analysing articles ---"
docker compose exec -T \
  -e ANALYSE_MODE=news_monitoring \
  -e AI_BACKEND="${AI_BACKEND:-groq}" \
  -e GROQ_API_KEY="${GROQ_API_KEY:-}" \
  -e GROQ_MODEL="${GROQ_MODEL:-llama-3.1-8b-instant}" \
  -e ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}" \
  -e CLAUDE_MODEL="${CLAUDE_MODEL:-claude-haiku-4-5-20251001}" \
  -e SLACK_WEBHOOK_URL="${SLACK_WEBHOOK_URL:-}" \
  -e APP_DIR=/app \
  -e NEWS_SITES_DIR=/app/news_sites \
  -e NEWS_OUTPUT_DIR=/app/news_output \
  scraper python3 /app/input/analyse_new.py --max-pages 5 --rerun-all

echo "=== News monitoring run completed at $(date -u '+%Y-%m-%d %H:%M UTC') ==="
