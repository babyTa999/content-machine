# vault — 选题库

每次 run_oracle 产出两份：
- **`YYYY-MM-DD.md`**（score.py 预筛）：`📥 送判官候选`（去重+去硬垃圾的全量）+ `🗑️ 硬剔除`（带原因）。
- **`YYYY-MM-DD-judged.md`**（判官）：`✅ 判官保留`（按栏目分表、发/接分开、带 link）+ `✂️ 判官砍掉`（带否决原因）。← 你看这份挑。

- `.raw-*.json` / `.judge-*.log` 是中间产物，可删。
- 判官保留的候选 → 跑 `oracle/brief.py` 出 brief → 走 `selene-content-sop` 起草。
