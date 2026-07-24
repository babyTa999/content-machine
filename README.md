# content-machine — Apodex X 选题 Oracle

把"今天发这个明天发那个"换成**平台逻辑驱动 + PROBLEM-FIRST 的选题**。骨架借自 Alex Lieberman
的 content machine，脑子换成 **Apodex 算法适配的机械预筛 + 语义判官 + 5 内容栏目**（数据实证，非拍脑袋）。

> 面向：Apodex 社媒 + 想复用的同事（cc / codex 直接跑）。X 主阵地，先做 X。

## 30 秒理解

```
采集(collect) → 预筛(score) → 判官(judge·LLM语义culls) → 你挑 → 角度/hook → 起草 → 质检 → 发布/分发
   └ safe-social+agent-reach   └ 去重+硬剔除+软标签   └ 选题judge-X            └ personas   └ voice skills
```

**两条信条**：① 选题不是"发什么"，是"发这条 X 会把我推给谁"。② PROBLEM-FIRST——先亮 ICP 的痛，再说 solution。见 `docs/01`。

## 目录

| 路径 | 是什么 |
|---|---|
| `docs/01-选题哲学.md` | **先读这个**。两核：账号主线 + PROBLEM-FIRST |
| `docs/02-内容栏目.md` | 5 栏目（①难题 ②验证 ③世界观 ④接话 ⑤Solvers）+ PROBLEM-FIRST + 两大 ICP |
| `docs/03-spike打分.md` | 预筛(score:去重+硬剔除+软标签) + 判官(语义分类+culls) 两层 |
| `docs/04-算法纪律.md` | 格式→信号映射 + 平台红线 + 发布前质检 |
| `docs/05-写作与分发.md` | 写作不锁框架（人 taste + à la carte skill + QC 兜底）+ repurpose 分发 |
| `config/*.yml` | pillars（栏目+exclude+icp）/ watchlist / sources（脚本读这些） |
| `oracle/collect.py` | 采集层（safe-social X + reddit问痛 + HN/HF/arXiv + journal/Retraction RSS） |
| `oracle/score.py` | 机械预筛（去重 + 硬剔除 + 软标签排序，不做决策） |
| `oracle/brief.py` | research brief（stub，推荐走 Claude 会话生成） |
| `personas/` | 面试团+创作+判官：产品技术专家 / 角度生成器-X / hook生成器-X / 狠编辑-X（质检）/ 选题judge-X（语义终审） |
| `vault/` | 选题库：预筛产出 + 判官日报（每日 `YYYY-MM-DD.md` / `-judged.md`） |
| `run_oracle.sh` | 一键：采集→预筛→判官→落 vault |

## 快速开始

```bash
/Users/admin/.agent-reach-venv/bin/pip install pyyaml   # 依赖，若缺

./run_oracle.sh            # 完整：采集(含X watchlist)→预筛→判官
./run_oracle.sh --no-x     # 跳过 X（safe-social 不可用时）
./run_oracle.sh --no-judge # 只到预筛，不跑判官
```

产出：`vault/DATE.md`（预筛：送判官候选 + 硬剔除）+ `vault/DATE-judged.md`（判官：保留 + 砍掉，金标准格式）。

## 与现有资产的关系

- **X 只读**：只走 `~/Apodex/内容/safe-social`（@kw90qk / manual:edge）。**绝不用裸 twitter**——红线见 `~/.claude/CLAUDE.md`。X 搜索能用，注意别连甩(限流)、别用 macOS 没有的 `timeout`。
- **采集源**：agent-reach（HN/RSS/YouTube）+ **Exa 免费网页搜索**（`mcporter call exa.web_search_exa`）。
- **起草声音**：apodex-tech-voice / karpathy-explain / eddie-shleyner 等 skill（à la carte，不锁框架）。
- **生产流程**：判官保留的选题接 `selene-content-sop`。
- **watchlist**：与 memory `x-watchlist-2026` 同源。
- **姊妹项目**：`community-os`（社区参与）—— 本 repo 管"内容选题/创作"，两个关注点分开。

## 迭代纪律

模型评的是"互动倾向"不是"文案质量"。跑几周后，用 **X 后台真实互动率**（reply/repost/收藏/完读）
校准 **`config/pillars.yml`（exclude/keywords）+ 判官口径（`personas/选题judge-X.md`）**。数据说话，不凭感觉。
