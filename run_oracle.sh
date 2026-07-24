#!/bin/zsh
# run_oracle.sh — 一键跑当日选题：采集 → 预筛(score) → 选题判官(LLM语义culls) → 落 vault
# 用法：./run_oracle.sh            （完整：含 X watchlist）
#      ./run_oracle.sh --no-x     （safe-social X 不可用时跳过）
#      ./run_oracle.sh --no-reddit
#      ./run_oracle.sh --no-judge （只到打分，跳过 LLM 判官）
set -eu
HERE="$(cd "$(dirname "$0")" && pwd)"
PY="/Users/admin/.agent-reach-venv/bin/python"
DATE="$(date +%F)"
RAW="$HERE/vault/.raw-$DATE.json"
OUT="$HERE/vault/$DATE.md"
JUDGED="$HERE/vault/$DATE-judged.md"
PERSONA="$HERE/personas/选题judge-X.md"

# 拆出 collect 的 flags（--no-judge 不传给 collect）
NO_JUDGE=0; COLLECT_FLAGS=()
for a in "$@"; do
  if [ "$a" = "--no-judge" ]; then NO_JUDGE=1; else COLLECT_FLAGS+=("$a"); fi
done

echo "[1/3] collect ..."
"$PY" "$HERE/oracle/collect.py" "${COLLECT_FLAGS[@]}" --out "$RAW"

echo "[2/3] score ..."
"$PY" "$HERE/oracle/score.py" "$RAW" --out "$OUT"

if [ "$NO_JUDGE" = "1" ]; then
  echo "跳过判官 (--no-judge)。产出 -> $OUT"
  exit 0
fi

echo "[3/3] 选题判官 (claude -p 语义 culls) ..."
if ! command -v claude >/dev/null 2>&1; then
  echo "⚠️ 未找到 claude CLI，跳过判官。粗筛结果仍在 -> $OUT"
  exit 0
fi
# 取"送判官候选"全量段（📥 ~ 🗑️硬剔除 之间）喂判官
PASSED="$(sed -n '/📥 送判官候选/,/🗑️ 硬剔除/p' "$OUT")"
PROMPT="$(cat "$PERSONA")

---
以下是预筛（score.py 去重+去硬垃圾后）送来的**全量候选**。严格按上面「选题 Judge」口径：
先语义判栏目（软提示仅参考、on-主线漏杀的救回），再 culls，**严格照输出格式**
（第一行就是 # 月日、H2=栏目、每栏表格、来源列可点击 link、发/接分开）。
只输出 markdown，不要调用任何工具、不要写文件、不要开场白。

$PASSED"

ERRLOG="$HERE/vault/.judge-err-$DATE.log"
# ⚠️ claude -p 出错时可能把错误写进 stdout(→$JUDGED)而非 stderr、且可能仍 exit 0——
#    所以不靠退出码，改校验 $JUDGED 是有效判官输出(非空 + 含 '#' 开头行 = # 月日/## 栏目)。
#    `|| true` 防 set -e 在判官非0退出时直接杀脚本(粗筛结果得保住)。
print -r -- "$PROMPT" | claude -p > "$JUDGED" 2>"$ERRLOG" || true
if [ -s "$JUDGED" ] && grep -qm1 '^#' "$JUDGED"; then
  echo "done -> 粗筛 $OUT ｜ 判官 $JUDGED"
  echo "下一步：看 $JUDGED 的保留清单 → 挑 → 角度生成器 → 起草 → 狠编辑。"
else
  # 判官没产出有效结果：真错误可能在 stdout(已进 $JUDGED)也可能在 stderr($ERRLOG)——
  # 把 $JUDGED 并进日志，避免"日志空、真错误在别处"的误导(codex 2026-07-24 指出)。
  { echo "--- stdout（可能是 claude 把错误写这儿）---"; cat "$JUDGED" 2>/dev/null; } >> "$ERRLOG"
  echo "⚠️ 判官失败/输出非判官格式。真错误见 $ERRLOG（已并入 stdout+stderr）。粗筛结果仍在 -> $OUT"
fi
