#!/usr/bin/env bash
# Run the full VSPP-RAG pipeline end-to-end.
#
# Order:
#   discover -> ingest -> normalize -> sync_corpus -> chunk -> embed -> eval
#
# Examples:
#   ./run_pipeline.sh
#   ./run_pipeline.sh --source ado_wiki --limit 1 --summarize
#   ./run_pipeline.sh --skip-discover --skip-ingest --skip-eval

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

if [[ -x ".venv/bin/python" ]]; then
  PY=".venv/bin/python"
else
  PY="python3"
fi

SOURCE="all"
LIMIT=""

DO_DISCOVER=1
DO_INGEST=1
DO_NORMALIZE=1
DO_SYNC=1
DO_CHUNK=1
DO_EMBED=1
DO_EVAL=1

DO_SUMMARIZE=0
RE_SUMMARIZE=0

usage() {
  cat <<'EOF'
Usage:
  ./run_pipeline.sh [options]

Options:
  --source SOURCE       Restrict ingest/normalize to a source, e.g. ado_wiki, confluence, 3gpp.
  --limit N            Limit ingest/normalize to N rows.
  --summarize          Run local-LLM doc summarization during normalize.py.
  --re-summarize       Force summary refresh even when input_sha256 matches.

  --skip-discover      Skip discover.py.
  --skip-ingest        Skip ingest.py.
  --skip-normalize     Skip normalize.py.
  --skip-sync          Skip sync_corpus.py.
  --skip-chunk         Skip chunk.py.
  --skip-embed         Skip embed.py.
  --skip-eval          Skip eval_retrieval.py.

Examples:
  ./run_pipeline.sh
  ./run_pipeline.sh --source ado_wiki --limit 1 --summarize
  ./run_pipeline.sh --skip-discover --skip-ingest --source ado_wiki --limit 1 --summarize
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --source)
      SOURCE="${2:?--source requires a value}"
      shift 2
      ;;
    --limit)
      LIMIT="${2:?--limit requires a value}"
      shift 2
      ;;
    --summarize)
      DO_SUMMARIZE=1
      shift
      ;;
    --re-summarize)
      DO_SUMMARIZE=1
      RE_SUMMARIZE=1
      shift
      ;;
    --skip-discover)
      DO_DISCOVER=0
      shift
      ;;
    --skip-ingest)
      DO_INGEST=0
      shift
      ;;
    --skip-normalize)
      DO_NORMALIZE=0
      shift
      ;;
    --skip-sync)
      DO_SYNC=0
      shift
      ;;
    --skip-chunk)
      DO_CHUNK=0
      shift
      ;;
    --skip-embed)
      DO_EMBED=0
      shift
      ;;
    --skip-eval)
      DO_EVAL=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      echo "Unknown option: $1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

step() {
  echo
  echo "============================================================"
  echo " $*"
  echo "============================================================"
}

run() {
  echo "+ $*"
  "$@"
}

if [[ $DO_DISCOVER -eq 1 ]]; then
  step "1/7 discover.py - build discovery_manifest.json and PM_Catalog.md"
  run "$PY" discover.py
fi

if [[ $DO_INGEST -eq 1 ]]; then
  step "2/7 ingest.py - download selected artifacts"
  ingest_args=()
  if [[ "$SOURCE" == "all" ]]; then
    ingest_args+=(--all)
  else
    ingest_args+=(--source "$SOURCE")
  fi
  if [[ -n "$LIMIT" ]]; then
    ingest_args+=(--limit "$LIMIT")
  fi
  run "$PY" ingest.py "${ingest_args[@]}"
fi

if [[ $DO_NORMALIZE -eq 1 ]]; then
  step "3/7 normalize.py - extract clean text and optional summaries"
  normalize_args=()
  if [[ "$SOURCE" != "all" ]]; then
    normalize_args+=(--source "$SOURCE")
  fi
  if [[ -n "$LIMIT" ]]; then
    normalize_args+=(--limit "$LIMIT")
  fi
  if [[ $DO_SUMMARIZE -eq 1 ]]; then
    normalize_args+=(--summarize)
  fi
  if [[ $RE_SUMMARIZE -eq 1 ]]; then
    normalize_args+=(--re-summarize)
  fi
  run "$PY" normalize.py "${normalize_args[@]}"
fi

if [[ $DO_SYNC -eq 1 ]]; then
  step "4/7 sync_corpus.py - prune stale normalized records"
  run "$PY" sync_corpus.py --prune --delete-orphan-txt
fi

if [[ $DO_CHUNK -eq 1 ]]; then
  step "5/7 chunk.py - build paragraph-aware chunks"
  run "$PY" chunk.py
fi

if [[ $DO_EMBED -eq 1 ]]; then
  step "6/7 embed.py - build vector matrix and chunk index"
  run "$PY" embed.py
fi

if [[ $DO_EVAL -eq 1 ]]; then
  step "7/7 eval_retrieval.py - run retrieval eval queries"
  if ! run "$PY" eval_retrieval.py; then
    echo "eval_retrieval.py reported misses; continuing because pipeline artifacts were built." >&2
  fi
fi

echo
echo "Pipeline complete."
