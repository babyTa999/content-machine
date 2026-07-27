# 03 · Recall → Enrichment → Evidence

这套管线不再让单层模型读短摘要后直接拍板。

## 1. collect：统一 schema

所有来源进入同一 candidate schema：`candidate_id`、platform、source path、discovery paths、url、text、author、authority、metrics、context、domain hints、research-task hints、column hints 与 action hints。

X 四路、Reddit Top/New、RSS/News 与其他 web source 先在当次运行内按 candidate ID 合并。source 只记录 provenance，不预先决定 column。

## 2. deterministic prefilter + SQLite

[`oracle/score.py`](../oracle/score.py) 只做可验证的机械工作：

- 应用 `pillars.yml` 硬排除。
- exact 跨日去重 45 天，story 跨日去重 14 天。
- 计算 enrichment 排序提示；X 权重最高，但不设平台配额。
- 记录账号多日出现、Recall/Evidence outcome，维护动态 X 候选池。

动态账号晋级需要至少两天出现且至少两次通过 Evidence Judge；竞品账号永不自动晋级。SQLite 位于仓库外。

## 3. Recall Judge：保召回

Recall Judge 按 60 条分批全量判断哪些候选值得进一步读取，随后合并校验。它允许同时给原创与互动 action，但不做最终事实结论。每个 candidate 必须且只能得到一次：

- `keep_for_enrichment`
- `interaction_only`
- `watch_only`
- `reject`

验证器会检查漏判、重复 ID、未知 ID 与跨平台 action。最多 32 条按机械 priority 进入 enrichment。

## 4. deterministic enrichment

- X：用 safe-social 读取原 tweet 结构与上下文。
- Reddit：用 safe-social 读取原帖与讨论结构。
- RSS / News / web：读取链接所指向页面。

enrichment 失败会显式记录 `error`，不会被伪装成已验证。

## 5. Evidence Judge：终审

Evidence Judge 必须逐条输出：source says、why now、why Apodex、possible angle、inference boundary、needs verification，以及合法 column/action。

每个 enriched candidate 必须且只能得到一次 `keep`、`interaction`、`watch` 或 `reject`。终审输出统一是 `Idea`，不会越级成成稿。

最终报告把原创栏目、X 互动、Reddit 互动、watch 与淘汰清单分开。同一候选若同时具有原创和互动价值，会出现在两个对应区域。
