#!/bin/zsh
# collect → deterministic prefilter/state → Recall Judge → enrichment → Evidence Judge → report
# Usage: ./run_oracle.sh [--no-x] [--no-reddit] [--no-judge]
set -eu

HERE="$(cd "$(dirname "$0")" && pwd)"
CANONICAL_REPO="/Users/admin/Apodex/内容/content-machine"
if [ "$HERE" != "$CANONICAL_REPO" ]; then
  echo "REFUSED: content-machine may only run from $CANONICAL_REPO" >&2
  echo "Current path: $HERE" >&2
  exit 78
fi
PY="${APODEX_CONTENT_PY:-$HOME/.agent-reach-venv/bin/python}"
DATE="$(date +%F)"
PERSONA="$HERE/personas/选题judge-X.md"
INTENTS="$HERE/config/editorial_intents.yml"
GOLDEN="$HERE/config/editorial_golden_set.yml"
RAW="$HERE/vault/.raw-$DATE.json"
RECALL_MD="$HERE/vault/.recall-$DATE.md"
RECALL_INPUT="$HERE/vault/.recall-input-$DATE.json"
RECALL_RAW="$HERE/vault/.recall-raw-$DATE.json"
RECALL_CHUNKS="$HERE/vault/.recall-chunks-$DATE"
RECALL_CLEAN="$HERE/vault/.recall-clean-$DATE.json"
QUEUE="$HERE/vault/.enrichment-queue-$DATE.json"
ENRICHED="$HERE/vault/.enriched-$DATE.json"
EVIDENCE_RAW="$HERE/vault/.evidence-raw-$DATE.json"
EVIDENCE_CLEAN="$HERE/vault/.evidence-clean-$DATE.json"
FINAL="$HERE/vault/$DATE.md"
ERRLOG="$HERE/vault/.judge-err-$DATE.log"

NO_JUDGE=0
COLLECT_FLAGS=()
for argument in "$@"; do
  if [ "$argument" = "--no-judge" ]; then
    NO_JUDGE=1
  else
    COLLECT_FLAGS+=("$argument")
  fi
done

echo "[1/7] collect: X four paths + Reddit Top/New + external sources"
"$PY" "$HERE/oracle/collect.py" "${COLLECT_FLAGS[@]}" --out "$RAW"

echo "[2/7] deterministic prefilter + SQLite state"
"$PY" "$HERE/oracle/score.py" prefilter "$RAW" --out "$RECALL_MD" --json-out "$RECALL_INPUT"

if [ "$NO_JUDGE" = "1" ]; then
  echo "Stopped before Judges (--no-judge) -> $RECALL_MD"
  exit 0
fi
if ! command -v claude >/dev/null 2>&1; then
  echo "No claude CLI; prefilter is available -> $RECALL_MD"
  exit 0
fi

echo "[3/7] Recall Judge"
"$PY" "$HERE/oracle/score.py" recall-chunks --candidates "$RECALL_INPUT" --out-dir "$RECALL_CHUNKS" --size 60
RECALL_OUTPUTS=()
for CHUNK in "$RECALL_CHUNKS"/input-*.json(N); do
  NAME="${CHUNK##*/}"
  CHUNK_OUTPUT="$RECALL_CHUNKS/output-${NAME#input-}"
  {
    cat "$PERSONA"
    echo ""
    echo "Authoritative active editorial intents:"
    cat "$INTENTS"
    echo ""
    echo "Human-labelled retrieval examples and current policy:"
    cat "$GOLDEN"
    echo ""
    echo "Execute Stage 1 only. Judge every candidate in this chunk. Return JSON only."
    cat "$CHUNK"
  } | claude -p > "$CHUNK_OUTPUT" 2>> "$ERRLOG" || true
  RECALL_OUTPUTS+=("$CHUNK_OUTPUT")
done
"$PY" "$HERE/oracle/score.py" merge-recall "${RECALL_OUTPUTS[@]}" --out "$RECALL_RAW"

echo "[4/7] validate Recall accounting + build enrichment queue"
if ! "$PY" "$HERE/oracle/score.py" validate-recall "$RECALL_RAW" --candidates "$RECALL_INPUT" --out "$RECALL_CLEAN" --queue-out "$QUEUE"; then
  echo "Recall Judge failed validation. Raw output: $RECALL_RAW; errors: $ERRLOG"
  exit 1
fi

echo "[5/7] deterministic enrichment"
"$PY" "$HERE/oracle/collect.py" --enrich "$QUEUE" --candidates "$RECALL_INPUT" --out "$ENRICHED"

echo "[6/7] Evidence Judge"
{
  cat "$PERSONA"
  echo ""
  echo "Authoritative active editorial intents:"
  cat "$INTENTS"
  echo ""
  echo "Human-labelled retrieval examples and current policy:"
  cat "$GOLDEN"
  echo ""
  echo "Execute Stage 2 only. Judge every enriched candidate in this JSON. Return JSON only."
  cat "$ENRICHED"
} | claude -p > "$EVIDENCE_RAW" 2>> "$ERRLOG" || true

echo "[7/7] validate Evidence accounting + render"
if ! "$PY" "$HERE/oracle/score.py" render "$EVIDENCE_RAW" --candidates "$ENRICHED" --out "$FINAL" --clean-out "$EVIDENCE_CLEAN"; then
  echo "Evidence Judge failed validation. Raw output: $EVIDENCE_RAW; errors: $ERRLOG"
  exit 1
fi

echo "done -> $FINAL"
