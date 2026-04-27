#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
#  Court Case Management — launch helper
#
#  Usage:
#    ./run.sh            → start the full app (home + all pages)
#    ./run.sh seed       → populate the database (run once)
#    ./run.sh api        → start FastAPI backend only
#    ./run.sh eval       → run evaluation suite (rules-only)
#    ./run.sh eval --llm → run evaluation suite with LLM calls
# ─────────────────────────────────────────────────────────────
set -e
export PYTHONPATH="$(pwd)"
source venv/bin/activate

case "${1:-app}" in
  app|"")
    echo "Starting Court Case Management..."
    echo "Open: http://localhost:8501"
    streamlit run Home.py \
      --server.port 8501 \
      --server.headless true \
      --theme.primaryColor "#1f77b4" \
      --theme.backgroundColor "#ffffff" \
      --theme.secondaryBackgroundColor "#f0f2f6" \
      --theme.font "sans serif"
    ;;
  seed)
    python3 db/seed.py
    ;;
  api)
    echo "Starting FastAPI on http://localhost:8000"
    uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
    ;;
  eval)
    shift
    python3 evaluation/run_evaluation.py "$@"
    ;;
  *)
    echo "Usage: ./run.sh [app|seed|api|eval [--llm]]"
    exit 1
    ;;
esac
