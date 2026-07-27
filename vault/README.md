# vault — 运行产物

每次 `run_oracle.sh` 生成（文件名对应 `run_oracle.sh` 里的变量）：

- `YYYY-MM-DD.md`：**Evidence Judge 终审报告**（`$FINAL`）。原创栏目、X 互动、
  Reddit 互动、观察、来源覆盖、轮换自查与终审淘汰分开成节。这是给人看的那一份。
- `.recall-YYYY-MM-DD.md`：deterministic prefilter 的 Recall 输入概览与硬排除。
- `.raw-*`、`.recall-*`、`.enriched-*`、`.evidence-*`、`.judge-err-*`：可审计中间产物。

整个 `vault/` 除本说明与 `.gitkeep` 外都被 gitignore，不会提交外部采集内容或 Judge 结果。

历史遗存：`.legacy-*-judged.md` 是旧命名（终审报告曾叫 `-judged.md`）留下的文件，
当前管线不再产出该后缀。
