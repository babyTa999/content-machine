# vault — 运行产物

每次 `run_oracle.sh` 生成：

- `YYYY-MM-DD.md`：deterministic prefilter 的 Recall 输入概览与硬排除。
- `YYYY-MM-DD-judged.md`：Evidence Judge 终审报告；原创栏目、X 互动、Reddit 互动、watch 与淘汰分开。
- `.raw-*`、`.recall-*`、`.enriched-*`、`.evidence-*`、`.judge-*`：可审计中间产物。

整个 `vault/` 除本说明与 `.gitkeep` 外都被 gitignore，不会提交外部采集内容或 Judge 结果。
