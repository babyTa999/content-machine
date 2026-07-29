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

### 常青引擎（第二条产线）

EI1–EI5 全部事件驱动，没新闻的一周就断供。以下两条不依赖热点——热点只提供
timing，不提供选题。它们走独立的第二条产线：跳过 Recall Judge（母题匹配由母表
确定性给出），仍过 Evidence Judge，在预算切分前入座保底。

- **EI6 Missing Control** → PS10 → T2。唯一选题来源是
  [`config/confound_library.yml`](config/confound_library.yml)：六个 STEM 学科的
  混杂因素母表，每条锚定一份已公开发表的 reporting standard。
  五个硬闸门字段（`discipline` / `reported_metric` / `confound` /
  `control_or_report_item` / `standard_source`）缺一即拒——这使"复现性泛叙事"
  和"学术方法论文导读"在结构上无法进入。
- **EI8 Verification Role Gap** → PS8 / PS9 / PS10 → T2 / T5 / T6。轴 AX4（谁在检查）
  与 AX6（错了谁付钱）。选题来源是
  [`config/checker_library.yml`](config/checker_library.yml)：每条锚一份已发表研究，
  且必须量化了某类检查环节的遗漏率或某笔代价。六个硬闸门字段
  （`checker` / `systematic_gap` / `documented_finding` / `evidence_anchor` /
  `problem_shape_id` / `thesis_id`）缺一即拒——`documented_finding` 必须含锚里的
  具体数字，这使"同行评议不可靠"这类复现性泛叙事在结构上无法进入。
  该表刻意**不收**以研究不当行为为主题的锚：那是已排除赛道，且极易滑向指控个人。
- **EI7 Cross-field Isomorphism** → PS11 → T9。学科两两配对，找计算范式上同构的
  难题。背书是落地页 `cross-domain evidence synthesis`，`capability_backing` 填
  `website`。**当前未接入常青产线**：它需要一张自己的母表（条目由 Selene 供料），
  该母表尚未建立，因此 EI7 目前只能靠 `x_queries` 碰运气。EI6 / EI8 不受影响。

**产能是算出来的，不是设出来的**：可持续产量 = 有可用锚的条目数 ÷
`entry_cooldown_days`。没有锚的条目（`source_incomplete` 或缺 locator）不进池，
因为写它就得编来源。当前可用 21 条 ÷ 30 天 ≈ 每天 0.7 条；要稳定每天 2 条，
可用池需要约 60 条。`per_run` 只是阀门，池子不够时 collect 自动少给，不会硬凑。

**引证防造假**：常青条目的 enrichment 不抓全文（出版商多数 403），而是拿 DOI 去
Crossref 核对母表声明的年份、期刊与第一作者。不一致即写 `error`，判官能看到。
这道闸门上线当天就抓出母表里一条 locator 混装了两份不同 checklist 的错配。
无 DOI 的锚（checklist、期刊政策页）不阻塞，但会显式标注"引证未经机器校验"。

**锚有保鲜期**：每条母表条目必须声明 `anchor_type`，三类纪律不同——

| `anchor_type` | 例子 | 纪律 |
|---|---|---|
| `standard` | ARRIVE、CONSORT、STROBE、机制原理 | 年份不构成风险，照常写 |
| `measurement` | 合规率、检出率、成本估算、成功率 | **有保鲜期**，必须同时写 `measured_window` |
| `case` | 一次特定的复现项目、一个具体引用网络 | 案例不过期，但不得推广成"现在普遍如此" |

`measurement` 类由 `collect.py` → `_anchor_age_note` 计算锚龄：距今 3 年以上警告
"可能已有更新"，8 年以上警告"几乎肯定已有更新，并考虑『当年如此、现在如何』本身
是不是更好的选题"（那属于事件驱动那条线）。警告写进 `enrichment.content` 的
`freshness_warning`，判官必须把"查有无更新"列为待核第一条，且散文里必须写清测量时点。

这条规则的来历：一条 2018–2019 年测得的合规率曾被写成当下状态，而监管方后来的执法
动作已经改变了图景——判官当时没有被告知锚的年龄。`checker_library` 里 8 条有 7 条是
`measurement`，因为"量化某个检查缺口的研究"天然是某时点的快照，这是该表最大的翻车风险。

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

- X：editorial-intent event query、dynamic watch；
- FDA safety / alert index；
- EU Have Your Say open initiatives（已加主题排除，见 `title_exclude`）；
- US Federal Register API：仍开放的 proposed rule / notice（`comments_close_on` 直接
  给出窗口截止日）与近期 final rule，按科研机构过滤 + 标题排除行政噪音；
- Science news lead-only RSS；
- 常青母表（不算外部来源，见上）。

当前关闭，以及为什么（每条的完整理由写在 `sources.yml` 各源的 `note`）：

| 源 | 停用理由 |
|---|---|
| `x_watchlist` | 07-27 实跑 48 条 0 keep 0 interaction；45 条来自机构官号，供的是论文推广，与母题结构不匹配。按人订阅出不了题 |
| `official_feeds`（美联储） | 金融赛道，本文件与 pillars 明文规定不得混入同一份日报；17 条 0 keep |
| `official_challenges` | 悬赏招标是"征集方案"不是正式 decision window，与 EI3 形似神不似；12 条 0 keep |
| `prediction_banks` | 搜索串偏政策金融，与科研赛道错位；4 条 0 keep，且 metaculus 返回 403 |
| Nature 主刊 RSS | `paper_abstract_only` 结构上进不了原创；12 条 0 keep |
| conversation graph / Reddit discovery / Google News | 此前已关，理由见 `sources.yml` |

不再主动搜索：

- next experiment；
- assay / protocol / pipeline troubleshooting；
- 垂直 bioinformatics / chemistry / physics 教学；
- AI4AI 方法论文；
- benchmark / competitor launch。

## 管线

```text
第一条产线（事件驱动）
external source mentions
  → deterministic canonical events
  → deterministic exclusion + cross-day state
  → Recall Judge: event → editorial intent → product qualification
  → deterministic enrichment
  → Evidence Judge: verified event match, source, stakes, product backing
  → exactly one destination + one action + one operator + one form + one axis
  → report（含轮换自查：轴分布 + 形态用量）

第二条产线（常青，不依赖外部事件）
curated 母表 → 冷却期过滤 + 日序轮换
  → prefilter（跳过主题闸与跨日去重，冷却状态另存 evergreen_usage）
  → 跳过 Recall Judge，在预算切分前入座保底
  → enrichment（回 anchor 页面验证锚可达）
  → 同一个 Evidence Judge → 同一份 report
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
跨日出现记录、账号统计、Judge outcome、每日形态用量（`report_forms`）与常青条目
冷却（`evergreen_usage`，只有真正进了日报的原创才烧冷却）。

注意：`collected_at` 与状态库的 `seen_date` 走 UTC，而 vault 文件名和报告日期走本地
日历。东八区的凌晨跑一次，会与前一天的运行落在同一个 UTC 日内——此时跨日去重按定义
不生效（`last_seen < seen_date` 不成立），全部候选会重新送判。要跨日去重生效，每个
UTC 日跑一次。

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
| [`config/confound_library.yml`](config/confound_library.yml) | 六学科混杂因素母表，EI6 唯一选题来源（轴 AX3） |
| [`config/checker_library.yml`](config/checker_library.yml) | 检查者与代价母表，EI8 唯一选题来源（轴 AX4 / AX6） |
| [`config/forms.yml`](config/forms.yml) | 形态与配额，刻意与算子解耦 |
| [`config/axes.yml`](config/axes.yml) | 8 根分类轴与轮换纪律 |
| [`config/editorial_golden_set.yml`](config/editorial_golden_set.yml) | Selene 历史 review 正反例与当前发布政策 |
| [`config/sources.yml`](config/sources.yml) | 外部信号库与 evidence-role 优先级 |
| [`config/watchlist.yml`](config/watchlist.yml) | 公开外部账号占位，不存内部名单 |
| [`oracle/collect.py`](oracle/collect.py) | 采集、mention 聚合、canonical event、enrichment |
| [`oracle/score.py`](oracle/score.py) | 去重、校验、唯一归宿、配额、报告渲染 |
| [`personas/选题judge-X.md`](personas/选题judge-X.md) | 两阶段编辑 contract |
