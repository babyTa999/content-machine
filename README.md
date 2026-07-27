# content-machine — Apodex Science 选题 Oracle

从可靠的外部母库扩大召回，再用两层 Judge 把“及时、真实、能承接”收敛成可用选题与互动机会。当前只覆盖 Science track；所有内部物料由 Selene 提供，仓库不保存内部路径、原文、截图、数字或未公开产品信息。

## 当前边界

- 目标受众：研究实验室、研究者与科研团队。
- 核心价值：研究问题、证据边界、决策与验证。
- X 是最大母库，也是最高召回权重；Reddit 同时服务真实问题发现、原创 seed 与站内互动。
- Nature / Science RSS、Google News RSS、专业预测与挑战页面、Hacker News 用作补充发现。
- 官方数据库与正式记录源已预留接口，待单独确认后再配置。
- 不抓学术诚信、出版争议、作者争议或撤回追踪；不因“新发布”自动加分。

## 管线

```text
external sources
  → unified candidate schema
  → deterministic prefilter + SQLite cross-day state
  → Recall Judge
  → deterministic enrichment
  → Evidence Judge
  → original-post pools + X interaction + Reddit interaction
```

source、column、action 完全解耦。一个 X 或 Reddit 候选可以同时成为原创素材与原平台互动对象；互动动作不会跨平台串池。

## X 四路并行

1. 静态 watchlist：已知研究者、机构与专业账号。
2. 任务型 query：每组同时跑 `Top` 与 `Latest`；Top 找已被圈内验证的强信号，Latest 找新问题与小账号。
3. conversation graph：围绕 seed handle 找 replies 与 quote chain，发现 watchlist 之外的真实专业参与者。
4. dynamic watch：账号多日稳定命中，且至少两次通过 Evidence Judge 后，自动进入外部 SQLite 动态池。

X 的 recall priority 是 6，Reddit 是 4，RSS 是 2，web 是 1。它们只影响 enrichment 排序，不替 Judge 做内容结论，也不是固定配额。

## 内容栏目

| ID | 栏目 | 外部自动发现 |
|---|---|---|
| C1 | Problem-aware｜难题求解 | 是 |
| C2 | Product-aware｜验证拆解 / 反 AI 幻觉 | 是 |
| C3 | Before It Becomes Official｜定稿之前 | 是 |
| C4 | Worldview｜Big Idea 世界观 | 否，Selene 供料 |
| C5 | Claim vs Record｜公开说法 vs 正式记录 | 是；正式记录源待补 |
| C6 | Social Proof｜#ApodexSolvers | 否，Selene 供料 |
| C8 | Engagement｜圈内接话 | 展示层，底层仍按平台动作分开 |

栏目细则见 [`docs/02-内容栏目.md`](docs/02-内容栏目.md)，机器配置见 [`config/pillars.yml`](config/pillars.yml)。

## 来源配置

[`config/sources.yml`](config/sources.yml) 是唯一来源开关：

- `auto`：当前脚本自动采集。
- `staged`：只保留结构，不采集。
- `manual`：由 Selene 供料，不进入自动外部管线。

X / Reddit 只通过 repo 外的本地只读 safe-social wrapper 调用；可用 `APODEX_SAFE_SOCIAL` 指定路径。账号名单与竞品红线在 [`config/watchlist.yml`](config/watchlist.yml)。

## 两层 Judge

- Recall Judge：按 60 条分批全量判断，再合并校验；每条只能是 `keep_for_enrichment`、`interaction_only`、`watch_only` 或 `reject`。
- Evidence Judge：读取 enrichment 后做终审；每条只能是 `keep`、`interaction`、`watch` 或 `reject`。
- [`oracle/score.py`](oracle/score.py) 校验每个输入 ID 恰好被决定一次、column/action 合法、互动平台匹配。模型漏判、重复判或输出非法动作时，运行直接失败，不静默产出。
- 终审保留项必须拆出 `source_says`、`why_now`、`why_apodex`、`possible_angle`、`inference_boundary` 与 `needs_verification`；成熟度统一为 `Idea`。

Judge 规则在 [`personas/选题judge-X.md`](personas/选题judge-X.md)。

## SQLite 轻量状态

默认位置：

```text
~/Library/Application Support/Apodex Content Machine/oracle.sqlite3
```

数据库在仓库外，只保存外部 candidate 标识、跨日出现记录、账号统计和 Judge outcome。默认 exact 去重 45 天、story 去重 14 天。可用 `APODEX_CONTENT_STATE_DB` 指向测试或其他本地路径。

## 运行

```bash
./run_oracle.sh
./run_oracle.sh --no-x
./run_oracle.sh --no-reddit
./run_oracle.sh --no-judge
```

流程产物写入 `vault/` 且被 gitignore：raw、Recall 输入与决定、enrichment、Evidence 决定，以及最终 `YYYY-MM-DD-judged.md`。

所需环境：Python + PyYAML、只读 safe-social、`claude` CLI。可用 `APODEX_CONTENT_PY` 指定 Python。采集器不会自动发帖、回复、点赞或关注。

## 关键文件

- [`oracle/collect.py`](oracle/collect.py)：采集、统一 schema、去重合并、enrichment。
- [`oracle/score.py`](oracle/score.py)：硬排除、SQLite、Judge contract 校验、报告渲染。
- [`config/sources.yml`](config/sources.yml)：外部母库、X 四路与优先级。
- [`config/pillars.yml`](config/pillars.yml)：column / domain / research task / action。
- [`run_oracle.sh`](run_oracle.sh)：七步总管线。

更多说明：[`docs/01-选题哲学.md`](docs/01-选题哲学.md) · [`docs/02-内容栏目.md`](docs/02-内容栏目.md) · [`docs/03-spike打分.md`](docs/03-spike打分.md)。

## TODO｜观察真实产量后再定

- X 的原创素材资格与官号互动资格拆成两套判断：低流量但真实、有价值的内容仍可进入原创母库；`x_reply` / `x_quote` 增加传播量硬门槛与少量可解释的战略例外。
- 先用当前版本观察实际保留线索数量与质量，由 Selene 人工判断；有足够样本后再校准 views、engagement velocity、账号层级与例外条件。
- 动态账号统计后续拆为 `content_keep_count` 与 `interaction_grade_count`，避免“经常提供好选题”被自动等同于“值得官号持续互动”。
