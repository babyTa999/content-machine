# 03 · 预筛 + 判官 —— 选题怎么筛

> **2026-07-24 改（B 重构）**：以前是"关键词 gate + 5 维打分 + 过线阈值"（score.py 硬筛）——**已废**，
> 因为关键词 gate 会把 on-主线但没命中字面词的（如 "LLMs Get Lost in Evolving User Intent"）误杀。
> 现在两层：**score.py = 机械预筛（不做决策）→ 判官(LLM) = 真正的语义分类 + culls。**

---

## 一、score.py — 机械预筛（`oracle/score.py`，不做决策）

1. **硬剔除**（真垃圾，直接扔）：中文 / 噪音(个人生活·寒暄·直播吆喝) / 竞品自吹·骂战 / 反 AI 能力 frame / 预测结果 / 法律 AI 幻觉判例(已写过)。规则见 `config/pillars.yml` 的 `exclude`。
2. **软标签**（仅供判官参考，**绝不据此毙**）：
   - `col`：keyword 命中的栏目提示（C1-C5，没命中=`?`）
   - `icp`：Apodex4Science / Apodex4DeepTech
   - `s`：粗排分（soft_score，让判官先看 likely-good；逻辑写死在 score.py，无外部配置）
3. **不再有 gate / 阈值 / no-column 毙人**——除硬垃圾外全部送判官。
4. 产出 `vault/YYYY-MM-DD.md`：「📥 送判官候选」+「🗑️ 硬剔除」。

## 二、判官 — 真正的筛（`personas/选题judge-X.md`，run_oracle 里走 `claude -p`）

- 读**全量**送判官候选，逐条**语义**判栏目 + 过否决问（PROBLEM-FIRST / 关系大不大 / frame 对不对 / 竞品自吹 / 骂战站队 / 噪音 / politics / 我们接得住吗）+ **救回被关键词漏杀的**。
- culls 到真正能用的 ~8-12，出金标准格式（日期 H1 / 栏目 H2 / 每栏表格 + 可点击 link / 发·接分开 / 砍掉清单带原因）。
- 详见判官 persona。

## 调优在哪（没有打分配置文件了）
- 想调**硬剔除/软提示** → 改 `config/pillars.yml`（exclude / keywords）。
- 想调**判官口径**（关系/frame/否决问松紧） → 改 `personas/选题judge-X.md`。
- 依据 = **X 后台真实互动率**（reply/repost/收藏/完读）：模型评的是"互动倾向"不是"文案质量"，用真实数据校准口径，不凭感觉。

相关：`02-内容栏目.md`、`04-算法纪律.md`、`personas/选题judge-X.md`、`oracle/score.py`
