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
# The persona tells the judge to "read config/*.yml", but `claude -p` only sees stdin.
# Every table the judge must satisfy is piped in, or it guesses and the gate fails closed.
PILLARS="$HERE/config/pillars.yml"
OPERATORS="$HERE/config/operators.yml"
FORMS="$HERE/config/forms.yml"
AXES="$HERE/config/axes.yml"
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

# Send one Stage-1 chunk to the judge. The compatibility table is piped in because
# `claude -p` only sees stdin; without it the judge guesses shape x thesis pairings.
judge_recall_chunk() {
  local chunk_in="$1" chunk_out="$2"
  {
    cat "$PERSONA"
    echo ""
    echo "Authoritative active editorial intents:"
    cat "$INTENTS"
    echo ""
    echo "Human-labelled retrieval examples and current policy:"
    cat "$GOLDEN"
    echo ""
    echo "Authoritative problem shape / thesis / column compatibility table."
    echo "A thesis only backs the problem_shapes listed under it, and only routes to its allowed_columns."
    cat "$PILLARS"
    echo ""
    echo "Execute Stage 1 only. Judge every candidate in this chunk. Return JSON only."
    cat "$chunk_in"
  } | claude -p > "$chunk_out" 2>> "$ERRLOG" || true
}

echo "[3/7] Recall Judge"
"$PY" "$HERE/oracle/score.py" recall-chunks --candidates "$RECALL_INPUT" --out-dir "$RECALL_CHUNKS" --size 60
RECALL_OUTPUTS=()
for CHUNK in "$RECALL_CHUNKS"/input-*.json(N); do
  NAME="${CHUNK##*/}"
  CHUNK_OUTPUT="$RECALL_CHUNKS/output-${NAME#input-}"
  judge_recall_chunk "$CHUNK" "$CHUNK_OUTPUT"
  RECALL_OUTPUTS+=("$CHUNK_OUTPUT")
done
"$PY" "$HERE/oracle/score.py" merge-recall "${RECALL_OUTPUTS[@]}" --out "$RECALL_RAW"

echo "[4/7] validate Recall accounting + build enrichment queue"
# Auto-repair loop: the judge mis-pairs shape x thesis roughly once per run even
# with the table in front of it. Rather than fail-close the whole run for one row,
# re-judge exactly the offending rows a bounded number of times, then let
# validate-recall be the final fail-closed authority.
SCAN="$HERE/vault/.recall-scan-$DATE.json"
REPAIR_MAX=3
for attempt in $(seq 1 $REPAIR_MAX); do
  "$PY" "$HERE/oracle/score.py" scan-recall "$RECALL_RAW" --candidates "$RECALL_INPUT" --out "$SCAN" >/dev/null
  REPAIR_IN="$RECALL_CHUNKS/repair-input-$attempt.json"
  REPAIR_OUT="$RECALL_CHUNKS/repair-output-$attempt.json"
  # 候选 id 只在 Python 里流动，绝不经过 shell 变量。这个脚本是 zsh，而 zsh 默认
  # 不对未加引号的变量做分词 —— 之前把 id 列表当 $BAD_IDS 展开，六个 id 变成一个
  # 参数，keep 集合里装的是一个巨型字符串，没有候选匹配得上，修复输入永远是空的。
  # 结果：这个循环从写下那天起从未真正重判过任何一行（2026-07-30 查出）。
  if ! "$PY" "$HERE/oracle/score.py" build-repair-input \
       --scan "$SCAN" --candidates "$RECALL_INPUT" --out "$REPAIR_IN" --stage recall; then
    echo "build-repair-input failed; scan: $SCAN"
    exit 1
  fi
  if [ ! -s "$REPAIR_IN" ]; then
    break
  fi
  judge_recall_chunk "$REPAIR_IN" "$REPAIR_OUT"
  "$PY" "$HERE/oracle/score.py" splice-recall --base "$RECALL_RAW" --patch "$REPAIR_OUT" --out "$RECALL_RAW"
done

if ! "$PY" "$HERE/oracle/score.py" validate-recall "$RECALL_RAW" --candidates "$RECALL_INPUT" --out "$RECALL_CLEAN" --queue-out "$QUEUE"; then
  echo "Recall Judge failed validation after $REPAIR_MAX repair attempts. Raw output: $RECALL_RAW; errors: $ERRLOG"
  exit 1
fi

echo "[5/7] deterministic enrichment"
"$PY" "$HERE/oracle/collect.py" --enrich "$QUEUE" --candidates "$RECALL_INPUT" --out "$ENRICHED"

# Stage-2 judging over an enriched JSON. Every authoritative table is piped in,
# same reason as Stage 1: claude -p only sees stdin.
judge_evidence() {
  local enriched_in="$1" evidence_out="$2"
  {
    cat "$PERSONA"
    echo ""
    echo "Authoritative active editorial intents:"
    cat "$INTENTS"
    echo ""
    echo "Human-labelled retrieval examples and current policy:"
    cat "$GOLDEN"
    echo ""
    echo "Authoritative problem shape / thesis / column compatibility table."
    echo "A thesis only backs the problem_shapes listed under it, and only routes to its allowed_columns."
    cat "$PILLARS"
    echo ""
    echo "Authoritative operator table for operator_id. Only status active/always_on may be chosen;"
    echo "dropped, paused and needs_rework are rejected. Respect problem_shape operator_fit when declared."
    cat "$OPERATORS"
    echo ""
    echo "Authoritative form table for form. Forms are decoupled from operators; long_post is capped."
    cat "$FORMS"
    echo ""
    echo "Authoritative axis table for axis."
    cat "$AXES"
    echo ""
    echo "Account routing. Every interaction candidate already carries context.account_tier,"
    echo "context.account_route and context.account_route_reason, decided by account tier plus"
    echo "content type before you saw it. You may downgrade a route from official to founder"
    echo "when you can see something the marker lists cannot. You may never upgrade founder to"
    echo "official — that gate protects the official account's standing and is not advisory."
    echo "Engineering implementation detail, hiring, personal news and heated rants belong to"
    echo "the founder's own account no matter how good the post is."
    echo ""
    echo "For decision=interaction you must also return, both in plain prose with no internal"
    echo "codenames: account_route (official|founder); stance_read — what position this author is"
    echo "actually taking, not what the post is about; verification_hook — how this connects back"
    echo "to our one narrative (verification). A reply that could be written without reading the"
    echo "post is not an interaction; leave it as watch instead."
    echo ""
    echo "Execute Stage 2 only. Judge every enriched candidate in this JSON. Return JSON only."
    cat "$enriched_in"
  } | claude -p > "$evidence_out" 2>> "$ERRLOG" || true
}

echo "[6/7] Evidence Judge"
judge_evidence "$ENRICHED" "$EVIDENCE_RAW"

# Same auto-repair as recall: Stage 2 mis-pairs intent/shape/thesis too (seen
# 2026-07-29: EI3 x PS8). Re-judge only the offending enriched candidates rather
# than making a human hand-edit evidence-raw. render() stays the fail-closed authority.
ESCAN="$HERE/vault/.evidence-scan-$DATE.json"
for attempt in $(seq 1 $REPAIR_MAX); do
  "$PY" "$HERE/oracle/score.py" scan-evidence "$EVIDENCE_RAW" --candidates "$ENRICHED" --out "$ESCAN" >/dev/null
  EREPAIR_IN="$HERE/vault/.evidence-repair-input-$DATE-$attempt.json"
  EREPAIR_OUT="$HERE/vault/.evidence-repair-output-$DATE-$attempt.json"
  # 同上：id 不过 shell。这条循环有过一模一样的 zsh 分词 bug。
  if ! "$PY" "$HERE/oracle/score.py" build-repair-input \
       --scan "$ESCAN" --candidates "$ENRICHED" --out "$EREPAIR_IN" --stage evidence; then
    echo "build-repair-input failed; scan: $ESCAN"
    exit 1
  fi
  if [ ! -s "$EREPAIR_IN" ]; then
    break
  fi
  judge_evidence "$EREPAIR_IN" "$EREPAIR_OUT"
  "$PY" "$HERE/oracle/score.py" splice-recall --base "$EVIDENCE_RAW" --patch "$EREPAIR_OUT" --out "$EVIDENCE_RAW"
done

echo "[7/7] validate Evidence accounting + render"
if ! "$PY" "$HERE/oracle/score.py" render "$EVIDENCE_RAW" --candidates "$ENRICHED" --out "$FINAL" --clean-out "$EVIDENCE_CLEAN"; then
  echo "Evidence Judge failed validation after $REPAIR_MAX repair attempts. Raw output: $EVIDENCE_RAW; errors: $ERRLOG"
  exit 1
fi

echo "done -> $FINAL"
