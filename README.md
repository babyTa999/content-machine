# content-machine — Apodex Product-led Content Oracle

系统先维护 Apodex 自己能够长期拥有的 design-choice 母题，再从外部世界寻找
timing、真实案例、stakes、冲突、更新和仍开放的决策窗口。外部来源不能因为出现
`research`、`verification`、`AI` 或新论文就自行生成 Apodex 内容。

## 当前内容方向

主动生产两条母线：

| ID | 栏目 | 任务 |
|---|---|---|
| C1 | Problem-aware｜现实中的难题形态 | 用当下事件呈现与官网同构的复杂问题 |
| C2 | Design Choice｜为什么 Apodex 这样设计 | 用外部 timing 解释技术报告中的 owned thesis |
| C3 | Before It Becomes Official｜定稿之前 | 只接仍开放的正式 decision window |

其他栏目：

- C4 Worldview：Selene 供料的长期世界观，不由外部管线自动生成。
- C5 Claim vs Record：当前暂停，避免回到 proof / benchmark。
- C6 Social Proof：只接确认可公开的 demo、用户案例和内部物料。
- C8 Engagement：只是一种唯一归宿，不与原创重复。

Proof / benchmark 当前暂停，包括 SOTA、leaderboard、模型横评、内部 benchmark、
4B vs 30B、coding/math benchmark。

## 母题 × 外部信号

母题与兼容关系见 [`config/pillars.yml`](config/pillars.yml)。活跃问题形态包括：

1. 新证据改变原来的答案；
2. 两个可信来源给出冲突结论；
3. 判断取决于条件、阈值和可观察信号；
4. 证据分散在多个独立系统；
5. 定义、纳入和排除条件改变答案集合；
6. 研究过程中现实仍在变化；
7. 一个问题分出多个独立调查分支；
8. 结论需要可追溯、可修订、可分叉的研究历史；
9. 很多引用最终坍缩到同一个来源。

每条原创候选必须同时具有：

- 一个 `problem_shape_id`；
- 一个 compatible `thesis_id`；
- `website` / `technical_report` / `demo` / `owner_confirmed`
  中的一项 capability backing；
- 一个真实外部 signal role；
- 一个且仅一个 primary destination；
- 一个且仅一个 primary action。

## 外部母库

[`config/sources.yml`](config/sources.yml) 是唯一来源开关。

来源按 evidence role 排序：

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

- X：机构/专家 watchlist、problem-shape query、conversation graph、dynamic watch；
- Reddit：只找 pain language 与 case lead，匿名内容不能直接成为官号案例；
- FDA safety / alert index；
- EU Have Your Say open initiatives；
- Federal Reserve press-release RSS；
- Google News signal queries；
- Nature / Science lead-only RSS；
- official challenges 与 prediction bank。

不再主动搜索：

- next experiment；
- assay / protocol / pipeline troubleshooting；
- 垂直 bioinformatics / chemistry / physics 教学；
- AI4AI 方法论文；
- benchmark / competitor launch。

## 管线

```text
external signals
  → unified candidate schema
  → deterministic exclusion + cross-day state
  → Recall Judge: problem shape × thesis pairing
  → deterministic enrichment
  → Evidence Judge: source, stakes, product backing
  → exactly one destination + exactly one action
  → report
```

Judge 规则在 [`personas/选题judge-X.md`](personas/选题judge-X.md)。
代码会校验：

- 每个 candidate ID 恰好被判断一次；
- 原创必须有合法 problem shape、thesis 和 capability backing；
- thesis、problem shape 与 column 必须兼容；
- C3 必须是真正的 decision window；
- 原创与互动互斥；
- 同一个来源不能跨栏目或跨原创/互动重复；
- X / Reddit 互动动作必须与平台匹配。

## 安全边界

- X / Reddit 只通过 repo 外的只读 `safe-social` wrapper；
- 采集器不会发帖、回复、点赞或关注；
- 内部材料不进入 repo；
- access、API、pricing、CTA 和未公开产品事实不由本系统推断；
- 所有数字、案例和引用发布前回一手或权威来源复核。

## SQLite 状态

默认位置：

```text
~/Library/Application Support/Apodex Content Machine/oracle.sqlite3
```

默认 exact 去重 45 天、story 去重 14 天。数据库只保存外部 candidate 标识、
跨日出现记录、账号统计与 Judge outcome。

## 运行

```bash
./run_oracle.sh
./run_oracle.sh --no-x
./run_oracle.sh --no-reddit
./run_oracle.sh --no-judge
```

可用 `APODEX_CONTENT_PY` 指定 Python，`APODEX_SAFE_SOCIAL` 指定只读 wrapper，
`APODEX_CONTENT_STATE_DB` 指向测试数据库。

关键文件：

- [`config/pillars.yml`](config/pillars.yml)：母题、问题形态、栏目和硬门；
- [`config/sources.yml`](config/sources.yml)：外部信号库与 evidence-role 优先级；
- [`config/watchlist.yml`](config/watchlist.yml)：公开外部账号占位，不存内部名单；
- [`oracle/collect.py`](oracle/collect.py)：采集、统一 schema、enrichment；
- [`oracle/score.py`](oracle/score.py)：去重、校验、唯一归宿和报告渲染；
- [`personas/选题judge-X.md`](personas/选题judge-X.md)：两阶段编辑 contract。
