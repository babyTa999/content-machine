#!/usr/bin/env python3
"""score.py — 预筛层（2026-07-24 改：去掉关键词 gate，改语义判官在下游做分类）
只做两件事：① 硬剔除（中文/噪音/骂战/反AI能力/预测结果——这些是真垃圾）
           ② 给幸存者打软标签（keyword col-hint + ICP + 粗排分，仅供判官参考，绝不据此毙）
不再有 no-column 毙人：on-主线但没命中字面词的（如 "LLMs Get Lost in Evolving User Intent"）
一律送进判官，由 LLM 语义判栏目。判官(claude -p)才是真正的分类+culls。
用法：python3 score.py raw.json --out prefilter.md
"""
import json, argparse, os, re, datetime as dt

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)

def load_yaml(p):
    import yaml
    return yaml.safe_load(open(p, encoding="utf-8"))

def hard_excluded(text, lang, cfg):
    """只做硬剔除（真垃圾）。返回原因或 None。"""
    t = (text or "").lower()
    for ex in cfg.get("exclude", []):
        if ex.get("rule", "").startswith("lang") and lang and lang != "en":
            return ex["name"]
        for kw in ex.get("keywords", []):
            if kw.lower() in t:
                return ex["name"]
    return None

def col_hint(text, pillar_hint, cfg):
    """软栏目提示（keyword 命中），仅供判官参考。没命中就返回 '?'，不毙。"""
    if pillar_hint:            # 采集给的 hint（C4=watchlist）
        return pillar_hint
    t = (text or "").lower()
    best, best_hits = "?", 0
    for cid, cdef in cfg["columns"].items():
        for kw in (cdef.get("keywords") or []):
            if kw.lower() in t:
                hits = sum(1 for k in cdef["keywords"] if k.lower() in t)
                if hits > best_hits: best, best_hits = cid, hits
                break
    return best

def icp_tag(text, cfg):
    t = (text or "").lower()
    scores = {n: sum(1 for kw in d.get("keywords", []) if kw.lower() in t)
              for n, d in cfg.get("icp", {}).items()}
    best = max(scores, key=scores.get) if scores else None
    return best if best and scores[best] > 0 else "—"

def soft_score(c):
    """粗排分（仅用于给判官排序，不是 gate）。"""
    text = c.get("text") or ""; s = 0
    if c.get("col") and c["col"] != "?": s += 3          # 有栏目倾向
    if c.get("icp") and c["icp"] != "—": s += 1          # 有 ICP 倾向
    if "?" in text or re.search(r"\b(vs|wrong|fails?|myth|hallucinat|verif)\b", text.lower()): s += 2
    age = c.get("age_h", 0) or 0
    if age <= 24: s += 2
    elif age <= 72: s += 1
    if c.get("source", "").startswith(("arxiv", "rss", "hf")): s += 1  # post 素材源略提
    return s

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("raw"); ap.add_argument("--out")
    args = ap.parse_args()
    cfg = load_yaml(os.path.join(REPO, "config", "pillars.yml"))
    colname = {cid: cd["name"] for cid, cd in cfg["columns"].items()}
    raw = json.load(open(args.raw))

    survivors, excluded = [], []
    for c in raw["candidates"]:
        why = hard_excluded(c.get("text"), c.get("lang"), cfg)
        if why:
            excluded.append({**c, "reject": why}); continue
        c["col"] = col_hint(c.get("text"), c.get("pillar_hint"), cfg)
        c["icp"] = icp_tag(c.get("text"), cfg)
        c["s"] = 0; c["s"] = soft_score(c)
        survivors.append(c)
    survivors.sort(key=lambda x: -x["s"])

    today = dt.date.today().isoformat()
    md = [f"# 预筛（送判官）｜{today}",
          f"> 采集 {raw['count']} → 硬剔除 {len(excluded)} → 送判官 {len(survivors)}（关键词只做软提示，不毙 no-column）",
          f"\n## 📥 送判官候选（{len(survivors)}）\n"]
    for c in survivors:
        tag = f"[{c['col']}·{c['icp']}·s{c['s']}]"
        md.append(f"- {tag} `{c['source']}` {c.get('handle','')} — {(c.get('text') or '')[:180].strip()}")
        md.append(f"  - {c['url']}")
    md.append(f"\n## 🗑️ 硬剔除（{len(excluded)}）\n")
    for c in excluded:
        md.append(f"- `{c['reject']}` {(c.get('text') or '')[:70]}")
    text = "\n".join(md)
    if args.out:
        open(args.out, "w").write(text)
        print(f"wrote -> {args.out}  (送判官 {len(survivors)} / 硬剔除 {len(excluded)})")
    else:
        print(text)

if __name__ == "__main__":
    main()
