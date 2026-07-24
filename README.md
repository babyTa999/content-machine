# content-machine — Apodex X 选题 Oracle

把"今天发这个明天发那个"换成**平台逻辑驱动 + PROBLEM-FIRST 的选题**。骨架借自 Alex Lieberman
的 content machine，脑子换成 **Apodex 算法适配的机械预筛 + 语义判官 + 5 内容栏目**（数据实证，非拍脑袋）。

> 面向：Apodex 社媒 + 想复用的同事（cc / codex 直接跑）。X 主阵地，先做 X。

**两条信条**：① 选题不是"发什么"，是"发这条 X 会把我推给谁"（Phoenix 双塔认身份一致性）。
② **PROBLEM-FIRST**——先亮 ICP 的痛，让人 problem-aware，*之后*才说 solution 和 Apodex。

## 30 秒理解 —— 内容生产 7 步

```
① 身份 → ② 栏目 → ③ 来源 → ④ 跑 Oracle → ⑤ 选题·角度·hook·起草 → ⑥ QC → ⑦ 发布·分发
  主线    5 栏     源×栏映射   collect→score      判官挑→persona→voice     狠编辑    anchor→单推+LinkedIn
                              →judge→vault        skill(à la carte)
```

前 4 步 = 本 repo 自动化（选题引擎）；后 3 步 = **人 taste 主导 + 按需 skill + gate 兜底**（写作不锁框架）。

---

## Phase 1 · 身份（主线）— 解决身份漂移

**身份 = AI for〔science research labs + deeptech startups〕，verification 为内核。**
这两类 = AFP 两大 ICP 线 = 两大内容线。"我们就是 AI4 他们。"

- **领域（bio / 材料 / 金融 / 监管）只做 instance，不做身份**——今天 bio 明天材料，只要话术骨架 / hashtag / 互动人群一致，Phoenix 眼里仍是同一个号。散一次就学乱一次。
- **两大 ICP 命名**（走 hashtag + 系列名，不用 X List）：`#Apodex4Science` / `#Apodex4DeepTech` / `#ApodexSolvers`（C 端 UGC）。

📄 全文：[`docs/01-选题哲学.md`](docs/01-选题哲学.md)

## Phase 2 · 内容栏目（5 栏）

每栏找的都是 **PROBLEM / 痛点 / 共鸣**——不自夸、不 showcase 论文、不夸人踩竞品。通用框架 ↔ 我们的栏目：

| 通用栏目 | 我们的栏目 | 找什么（Apodex 具体） | 信号 |
|---|---|---|---|
| **Problem-aware** | ① 难题求解（主池） | ICP 真正卡住的硬 PROBLEM（含"预测切面"7+5 类；SSPP 1,482 题库） | 心智·收藏·曝光 |
| **Product-aware** | ② 验证拆解 / 反 AI 幻觉 | 我们主打 verification → **别人没做好验证、幻觉闯祸的真实 case**（剂量错 / 假判例 / 编造数据） | 收藏·评论 |
| **Worldview** | ③ 世界观（底色） | discovery model ≠ generative——**从老板 blog 搬现成** | 心智·涨粉 |
| **Engagement** | ④ 圈内接话（回复 / quote） | (a) 共鸣（同哲学→自陈）(b) 痛点（有人吐槽我们能解的→荐己）。不夸不踩 | 曝光·profile_click |
| **Social Proof** | ⑤ ApodexSolvers（UGC） | C 端用户用 Apodex 跑的 prompts 展示（Selene 供料） | 涨粉·社区·AFP |

📄 全文：[`docs/02-内容栏目.md`](docs/02-内容栏目.md) ｜ 配置：[`config/pillars.yml`](config/pillars.yml)（栏目 keywords + exclude + icp）

## Phase 3 · 内容来源（源 × 栏目映射）

**原则：每个源过一遍、看命中哪些栏目（多对多），不是一栏一源。** `auto`=脚本已采，`staged`=待接，人工=不走采集。

| 栏目 | 来源 | 状态 |
|---|---|---|
| ① Problem-aware | Reddit 7 个问痛版（bioinformatics / computationalbiology / chemistry / statistics / MLQuestions / datascience / MachineLearning） | auto |
| | Metaculus 预测题（Jina 抓页）· 官方悬赏（USA.gov / XPRIZE / ARPA-H，Jina + 路径过滤） | auto |
| | arXiv 多 query（降权——多方法论文，判官 Q0 过滤，只留亮出 PROBLEM 的） | auto |
| ② Product-aware | Retraction Watch RSS（AI 编造 / 撤稿） | auto |
| | AI Incident Database（AIAAIC，Exa 免费搜） | staged |
| ④ Engagement | X watchlist（个人 + 机构官号，见 `watchlist.yml`） | auto |
| | X 关键词搜生人（共鸣 / 痛点 query）—— **代码未实现，目前只跑 watchlist** | staged |
| 基础源 | HackerNews front-page · HF daily papers · journal RSS（Nature / NatureComms / Science） | auto |
| ③ / ⑤ | 老板 blog（③）· 内部 UGC（⑤） | 人工 |

📄 全文：[`config/sources.yml`](config/sources.yml)（每条源的 method / status / query）

## Phase 4 · 跑 Oracle（collect → score → judge → vault）

一键：`./run_oracle.sh`。四步各自的重要部分：

| 步 | 文件 | 干什么（重要部分） |
|---|---|---|
| **collect** | [`oracle/collect.py`](oracle/collect.py) | 扫 Phase 3 的源采候选。**X 只走 safe-social（@kw90qk 只读）**。**时间窗口按内容性质**：接话 48h（要鲜）/ X 机构号 168h / Reddit 痛点 168h（常青种子）/ 预测题·悬赏无限制。URL 归一化去重（arXiv 抹版本号） |
| **score** | [`oracle/score.py`](oracle/score.py) | **机械预筛，不做决策**。①硬剔除真垃圾（`pillars.yml` 的 exclude）②软标签 `[col·icp·s]`（关键词只做提示，**绝不据此毙**）③**problem-first 结构信号**：Reddit 求助帖（help/how/error/[Q][R]）+4 置顶——真痛点是大白话、命中不了术语库，靠结构信号浮上来。**不再有 gate / 阈值** |
| **judge** | [`personas/选题judge-X.md`](personas/选题judge-X.md)（`claude -p`） | **真正的筛**。读全量候选，逐条**语义**判栏目 + 过 8 条否决问（Q0=PROBLEM-FIRST / 关系 / frame 反幻觉不反能力 / 竞品自吹 / 骂战 / 噪音 / politics / 接得住吗）+ **救回被关键词漏杀的**。culls 到 ~8-12 |
| **vault** | [`vault/`](vault/) | `DATE.md`（预筛：送判官 + 硬剔除）+ `DATE-judged.md`（判官：保留 + 砍掉，**金标准格式**：日期 H1 / 栏目 H2 / 每栏表格 + 可点击 link / 发·接分开） |

📄 全文：[`docs/03-spike打分.md`](docs/03-spike打分.md)

## Phase 5 · 选题 → 角度 → hook → 起草

判官产出后**人 taste 接手**。各步用什么：

| 步 | 用什么 | 说明 |
|---|---|---|
| **选题** | 你从 `vault/DATE-judged.md` 保留清单里挑 | 判官已做语义终审 + 给理由，你拍板 |
| **角度** | [`personas/角度生成器-X.md`](personas/角度生成器-X.md) | 6 lenses 切角度 + so-what gate |
| **hook** | [`personas/hook生成器-X.md`](personas/hook生成器-X.md) → skill `eddie-shleyner` 打磨 | 10 moves + Apodex Hook 三型；`julian-shapiro` 备选（偏 viral，不常备） |
| **起草** | voice skill **à la carte**（想调味取 1 个，不叠不强制） | ①`apodex-tech-voice` / `karpathy-explain`　②`apodex-tech-voice` / `selene-academic-voice`　③搬老板 blog　④`selene-community-voice`　｜ 大件迭代 `selene-content-sop` |
| **事实核** | [`personas/产品技术专家.md`](personas/产品技术专家.md) | 数字 verbatim / claim 回一手源 / RULES 红线 |

> ⚠️ **不学旧声音**：`apodex-x-official-style` 已移出写作链（pivot 前旧帖，限制内容 + 夹带作废口径）。写作只学**算法**（Phase 6 + `Desktop/X 渠道运营.md`）+ **红线**（RULES.md）。

📄 全文：[`docs/05-写作与分发.md`](docs/05-写作与分发.md)（voice skill 备查表）

## Phase 6 · QC（发布前质检）

**打分官 = [`personas/狠编辑-X.md`](personas/狠编辑-X.md)**（改自 newsjack meanest-editor）。7 项 rubric 打分 + 揪 top3 + 逐行开刀 + 重写 hook。三关并进它：

1. **triple-translation test**（声音自然、不像 AI slop）
2. **算法自查三问**：能独立站住吗？有深互动钩吗？是英文且中 ICP 吗？
3. **RULES 红线**：slogan verbatim / 无未确认 access·API·pricing / 数字回一手源 / **无偷偷加的承诺（成稿 = 无承诺安全版）**

📄 全文：[`docs/04-算法纪律.md`](docs/04-算法纪律.md)（格式→信号映射 + 平台红线）

## Phase 7 · 发布 + 分发（anchor → repurpose）

一条 anchor（通常 thread）→ 两个方向，**人 taste 主导，狠编辑兜底**：

| 方向 | 用什么 | 关键 |
|---|---|---|
| **单推（X 内）** | X 算法（`docs/04`） | 从 anchor 挑能**独立成立**的点各自成推——每条过 **candidate isolation**（自带 hook，不靠上下文） |
| **LinkedIn 版** | skill `/linkedin-algorithm`（LinkedIn 算法脑）+ `apodex-linkedin-official-style`（只学格式，去老口径） | **不是复制粘贴**，按 LinkedIn 算法（dwell / carousel / golden hour）重排 |

编排走 `selene-content-sop` 的分发步。

---

## 快速开始

```bash
/Users/admin/.agent-reach-venv/bin/pip install pyyaml   # 依赖，若缺

./run_oracle.sh            # 完整：采集(含X watchlist)→预筛→判官→vault
./run_oracle.sh --no-x     # 跳过 X（safe-social 不可用时）
./run_oracle.sh --no-reddit
./run_oracle.sh --no-judge # 只到预筛，不跑判官
```

## 红线（`~/.claude/CLAUDE.md`）

- **X 只读只走 `~/Apodex/内容/safe-social`（@kw90qk / manual:edge）**——绝不用裸 twitter / agent-reach twitter。X 搜索能用，但别连甩（限流）、**别用 macOS 没有的 `timeout`**。
- cookie 工具**只读不写**：不发帖 / 回复 / 点赞 / 关注。接话回复本身是人工动作。
- 对外文案**无偷偷加的承诺**，成稿默认无承诺安全版。

## 迭代纪律

模型评的是"互动倾向"不是"文案质量"。跑几周后，用 **X 后台真实互动率**（reply / repost / 收藏 / 完读）
校准 **`config/pillars.yml`（exclude / keywords）+ 判官口径（`personas/选题judge-X.md`）**。没有打分配置文件了——调优只在这两处。数据说话，不凭感觉。
