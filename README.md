# content-machine — Apodex Product-led Content Oracle

系统先维护 Apodex 自己能够长期拥有的母题，再从外部世界寻找 timing、真实案例、
stakes、冲突、更新和仍开放的决策窗口。外部来源不能因为出现 `research`、
`verification`、`AI` 或新论文就自行生成 Apodex 内容。

## 母问题（第 0 层）

> **Who checked this — other than the person who made it?**
> 除了做出这个判断的人，还有谁检查过它？

大白话版：**我已经得到了一个结果，但我不知道还有哪个解释没有排除。**

一切收口回同一句：**这个判断有没有资格被相信**——不是"对不对"。
天然对手是**自评**。定义在 [`config/pillars.yml`](config/pillars.yml) 的 `root_question`。

八个 thesis 并列等于没有母问题，那是内容"散"的结构性原因。

**赛道边界**：当前只服务**科学研究赛道**（受众＝学术 PI / 实验室负责人 / 深科技与量化
研究者，全部 STEM 六科）。金融赛道母问题是监管型的，语法不同，需另立 `root_question`
与独立排期，不得混入同一份日报。

## 当前内容方向

主动生产三条母线：

| ID | 栏目 | 任务 |
|---|---|---|
| C1 | Problem-aware｜现实中的难题形态 | 用当下事件呈现与官网同构的复杂问题 |
| C2 | Design Choice｜为什么 Apodex 这样设计 | 用外部 timing 解释技术报告中的 owned thesis |
| C3 | Before It Becomes Official｜定稿之前 | 只接仍开放的正式 decision window |

其他栏目：

- C4 Worldview：Selene 供料的长期世界观，不由外部管线自动生成。
- C5 Claim vs Record：当前暂停，避免回到 proof / benchmark。
- C6 Social Proof：只接确认可公开的 demo、用户案例和内部物料。
- C8 Engagement：唯一互动归宿，不与原创重复。

Proof / benchmark 当前暂停，包括 SOTA、leaderboard、模型横评、内部 benchmark、
4B vs 30B、coding/math benchmark。

## 母题 × 外部信号

母题入口见 [`config/editorial_intents.yml`](config/editorial_intents.yml)，兼容关系见
[`config/pillars.yml`](config/pillars.yml)。活跃问题形态：

1. 新证据改变原来的答案；
2. 两个可信来源给出冲突结论；
3. 判断取决于条件、阈值和可观察信号；
4. 证据分散在多个独立系统；
5. 定义、纳入和排除条件改变答案集合；
6. 研究过程中现实仍在变化；
7. 一个问题分出多个独立调查分支；
8. 结论需要可追溯、可修订、可分叉的研究历史；
9. 很多引用最终坍缩到同一个来源；
10. 结论没有排除掉替代解释（curated，见常青引擎）；
11. 两个学科的难题在计算范式上是同一个问题（curated）。

### 常青引擎

EI1–EI5 全部事件驱动，没新闻的一周就断供。以下两条不依赖热点——热点只提供
timing，不提供选题：

- **EI6 Missing Control** → PS10 → T2。唯一选题来源是
  [`config/confound_library.yml`](config/confound_library.yml)：六个 STEM 学科的
  混杂因素母表，每条锚定一份已公开发表的 reporting standard。
  五个硬闸门字段（`discipline` / `reported_metric` / `confound` /
  `control_or_report_item` / `standard_source`）缺一即拒——这使"复现性泛叙事"
  和"学术方法论文导读"在结构上无法进入。
- **EI7 Cross-field Isomorphism** → PS11 → T9。学科两两配对，找计算范式上同构的
  难题。背书是落地页 `cross-domain evidence synthesis`，`capability_backing` 填
  `website`。

科研诚信 / 论文可复现性核查是**已排除赛道**，仅作学术获客渠道的自然延伸；撤稿类
选题每月上限 1 条。详见 [`docs/01-选题哲学.md`](docs/01-选题哲学.md) 排除边界。

## 每条原创候选必须具有

**可准入**（Apodex 凭什么能讲）：

- 一个 `editorial_intent_id` 与可审计的 `event_match_reason`；
- 一个 `problem_shape_id`；
- 一个 compatible `thesis_id`；
- `website` / `technical_report` / `demo` / `owner_confirmed` 中的一项 capability backing；
- 一个真实外部 signal role；
- 一个且仅一个 primary destination；
- 一个且仅一个 primary action。

**可动笔**（这条怎么写）——上面七项让候选合法，但它们全部回答"Apodex 凭什么能讲"，
没有一项回答"落笔第一句是什么"。以下三项补这个断口，缺一即校验失败：

| 字段 | 配置 | 回答的问题 |
|---|---|---|
| `operator_id` | [`config/operators.yml`](config/operators.yml) | 这条怎么写 |
| `form` | [`config/forms.yml`](config/forms.yml) | 写成什么形态 |
| `axis` | [`config/axes.yml`](config/axes.yml) | 从母问题的哪个切面问 |

纪律：

- **算子与形态解耦**。任何算子都能出任何形态；`forms.yml` 刻意不含 operator 字段。
  算子的 `close` 字段是"一句可能的开头"，不是模板。
- **`long_post` 每期上限 1、每周上限 2**，近 7 天由 `report_forms` 状态表计数。
  防止 draft 退化成同一个骨架反复填空。
- **`dropped` / `paused` / `needs_rework` 的算子选了报错**；problem shape 声明了
  `operator_fit` 时只能从那张表选。
- **代号不许进散文**。`source_says` / `why_now` / `why_apodex` / `possible_angle` /
  `inference_boundary` / `secondary_note` 出现 `E1` `OP8` `PS10` `EI6` `AX3` `T9`
  `RQ_*` `CF_*` 一律报错。日报只打印中文名。

## 外部母库

[`config/sources.yml`](config/sources.yml) 是唯一来源开关。来源按 evidence role 排序：

```text
official_record
  > institutional_update
  > primary_report
  > expert_primary_link
  > reputable_news_lead
  > community_case_lead
  > paper_abstract_only
  > anonymous_opinion
```

当前自动来源：

- X：机构/专家 watchlist、editorial-intent event query、dynamic watch；
- FDA safety / alert index；
- EU Have Your Say open initiatives；
- Federal Reserve press-release RSS；
- Nature / Science lead-only RSS；
- official challenges 与 prediction bank。

conversation graph、Reddit discovery 与 Google News signal 当前关闭。

不再主动搜索：

- next experiment；
- assay / protocol / pipeline troubleshooting；
- 垂直 bioinformatics / chemistry / physics 教学；
- AI4AI 方法论文；
- benchmark / competitor launch。

## 管线

```text
external source mentions
  → deterministic canonical events
  → deterministic exclusion + cross-day state
  → Recall Judge: event → editorial intent → product qualification
  → deterministic enrichment
  → Evidence Judge: verified event match, source, stakes, product backing
  → exactly one destination + one action + one operator + one form + one axis
  → report（含轮换自查：轴分布 + 形态用量）
```

Judge 规则在 [`personas/选题judge-X.md`](personas/选题judge-X.md)。代码会校验：

- 每个 canonical event ID 恰好被判断一次；
- 每个原创事件必须先匹配 active editorial intent；
- 原创必须有合法 problem shape、thesis 和 capability backing；
- thesis、problem shape 与 column 必须兼容；
- C3 必须是真正的 decision window；
- 原创必须有可写算子，且算子对该 problem shape 合规；
- 原创必须有合法形态，且不超出单期与周配额；
- 原创必须有合法轴；
- 散文字段不含内部代号；
- 原创与互动互斥；
- 同一个 canonical event 不能跨栏目或跨原创/互动重复；
- X / Reddit 互动动作必须与平台匹配。

## 安全边界

- X / Reddit 只通过 repo 外的只读 `safe-social` wrapper；
- 采集器不会发帖、回复、点赞或关注；
- 内部材料不进入 repo（含 TracesA/TracesB 与商业化战略文件里的一切案例与数字）；
- access、API、pricing、CTA 和未公开产品事实不由本系统推断；
- 所有数字、案例和引用发布前回一手或权威来源复核。

## SQLite 状态

默认位置：

```text
~/Library/Application Support/Apodex Content Machine/oracle.sqlite3
```

默认 exact 去重 45 天、story 去重 14 天。数据库保存外部 event/mention 标识、
跨日出现记录、账号统计、Judge outcome 与每日形态用量（`report_forms`）。

## 运行

```bash
./run_oracle.sh
```

```bash
./run_oracle.sh --no-x
```

```bash
./run_oracle.sh --no-judge
```

可用 `APODEX_CONTENT_PY` 指定 Python，`APODEX_SAFE_SOCIAL` 指定只读 wrapper，
`APODEX_CONTENT_STATE_DB` 指向测试数据库。

契约测试：

```bash
python -m unittest tests.test_pipeline_contract
```

## 关键文件

| 文件 | 作用 |
|---|---|
| [`config/pillars.yml`](config/pillars.yml) | 母问题、赛道边界、栏目、问题形态、thesis、硬门 |
| [`config/editorial_intents.yml`](config/editorial_intents.yml) | 事件触发词、信源类型、正反例、产品 backing |
| [`config/operators.yml`](config/operators.yml) | 13 个算子：这条怎么写，含弃选口径 |
| [`config/confound_library.yml`](config/confound_library.yml) | 六学科混杂因素母表，EI6 唯一选题来源 |
| [`config/forms.yml`](config/forms.yml) | 形态与配额，刻意与算子解耦 |
| [`config/axes.yml`](config/axes.yml) | 8 根分类轴与轮换纪律 |
| [`config/editorial_golden_set.yml`](config/editorial_golden_set.yml) | Selene 历史 review 正反例与当前发布政策 |
| [`config/sources.yml`](config/sources.yml) | 外部信号库与 evidence-role 优先级 |
| [`config/watchlist.yml`](config/watchlist.yml) | 公开外部账号占位，不存内部名单 |
| [`oracle/collect.py`](oracle/collect.py) | 采集、mention 聚合、canonical event、enrichment |
| [`oracle/score.py`](oracle/score.py) | 去重、校验、唯一归宿、配额、报告渲染 |
| [`personas/选题judge-X.md`](personas/选题judge-X.md) | 两阶段编辑 contract |
