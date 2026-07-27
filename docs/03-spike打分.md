# 03 · Product Thesis → External Signal → Evidence

管线先确定产品母题，再寻找能证明“为什么现在讲”的外部信号；不再从大量专业素材反推品牌内容。

## 1. Product Thesis 母库

[`config/pillars.yml`](../config/pillars.yml) 保存：

- Design Choice 母题及报告依据
- 官网同构的 Problem Shape
- capability backing 与公开边界
- 适合的外部信号角色
- 硬排除和暂停叙事

没有 `problem_shape_id` 或 `product_thesis_id` 的候选不能进入 product-led 原创。

## 2. collect：证据角色 schema

所有来源统一记录 `evidence_role`、`signal_group`、`lead_only`、provenance、authority、metrics 与 context。RSS 可配置成字典形式；官方 index、监管咨询、权威 RSS、X 与 Reddit 分别承担不同证据角色。

Reddit、新闻聚合与期刊列表默认是 `lead_only`：可以发现问题，但不能单独支撑产品 claim。

## 3. deterministic prefilter + SQLite

[`oracle/score.py`](../oracle/score.py) 负责：

- 应用 capability-backed 硬门槛与主题排除。
- exact 跨日去重 45 天，story 跨日去重 14 天。
- 推断 problem shape / thesis hint 和 evidence role。
- 保留动态 X 来源治理与 fail-closed 限流纪律。

## 4. Recall Judge：只决定是否值得补证

Recall 可以给出 `keep_for_enrichment`、`interaction_only`、`watch_only` 或 `reject`。它必须说明候选可能支撑哪个母题，以及还缺哪类证据；不能因为出现 verification、research 或 AI 关键词自动放行。

## 5. deterministic enrichment

enrichment 回到原始 tweet、原帖、官方文件、公开记录或权威页面。失败显式记录 `error`，`lead_only` 来源在未找到原始证据前不能升级。

## 6. Evidence Judge：唯一编辑决策

每个 candidate 最终必须且只能得到：

- 一个 `primary_destination`
- 一个 `primary_action`
- 一个 problem shape 或 product thesis
- source says / why now / why Apodex / inference boundary
- capability backing 和 evidence gap

同一来源整份日报只出现一次。原创与互动冲突时必须二选一；其他用途只允许短 `secondary_note`，不得复制到第二个栏目。
