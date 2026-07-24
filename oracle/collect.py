#!/usr/bin/env python3
"""collect.py — Oracle 采集层
扫 sources.yml 里的源，产出候选 spike（原始，未打分）→ stdout / --out。
全部复用现有工具：X 只走 safe-social（@kw90qk 只读），其余零配置 curl / agent-reach。
用法：
    python3 collect.py                 # 采集全部源，打印摘要
    python3 collect.py --out raw.json  # 落盘
依赖：/Users/admin/.agent-reach-venv/bin/python（含 safe-social 运行环境）
"""
import json, subprocess, time, datetime as dt, argparse, os, re, urllib.request, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SAFE_SOCIAL = "/Users/admin/Apodex/内容/safe-social"

def load_yaml(path):
    # 极简 yaml 读取，避免额外依赖；仅够读本 repo 的 config
    import yaml  # PyYAML 常见可用；不可用时见 README 安装说明
    return yaml.safe_load(open(path, encoding="utf-8"))

def iso_age_h(iso, now):
    try:
        t = dt.datetime.fromisoformat(iso)
        return (now - t).total_seconds() / 3600
    except Exception:
        return None

# ---------- X watchlist（safe-social 只读） ----------
def collect_x(watch_cfg, now):
    out = []
    handles = []
    for tier, hs in watch_cfg["watchlist"].items():
        for h in hs:
            handles.append((tier, h, "C4"))
    # 机构/journal 官号：科学新闻当 post 素材（pillar_hint=None 按内容分类，不强塞 C4）
    for h in watch_cfg.get("institutions", []):
        handles.append(("institution", h, None))
    for tier, h, hint in handles:
        try:
            r = subprocess.run([SAFE_SOCIAL, "x", "user-posts", h, "-n", "10", "--json"],
                               capture_output=True, text=True, timeout=90)
            posts = (json.loads(r.stdout) or {}).get("data") or []
        except Exception as e:
            print(f"  [x] {h}: ERR {e}")
            time.sleep(1.2); continue
        for p in posts:
            if p.get("isRetweet"):
                continue
            age = iso_age_h(p.get("createdAtISO", ""), now)
            if age is None or age > 48 or age < 0:
                continue
            m = p.get("metrics") or {}
            eng = m.get("likes",0)+2*m.get("retweets",0)+2*m.get("quotes",0)+m.get("replies",0)
            out.append({
                "source": "x_watchlist", "pillar_hint": hint, "tier": tier,
                "handle": h, "url": f"https://x.com/{h}/status/{p['id']}",
                "text": p.get("text","")[:500], "lang": p.get("lang"),
                "age_h": round(age,1), "eng": eng, "metrics": m,
            })
        time.sleep(1.2)
    return out

# ---------- 零配置 API 源 ----------
def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": "content-machine/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def collect_hn():
    out = []
    try:
        d = _get("https://hn.algolia.com/api/v1/search?tags=front_page&query=AI&hitsPerPage=8")
        for h in d.get("hits", []):
            out.append({
                "source": "hackernews", "pillar_hint": None,
                "url": h.get("url") or f"https://news.ycombinator.com/item?id={h['objectID']}",
                "text": h.get("title",""), "lang": "en",
                "points": h.get("points",0), "age_h": 0, "eng": h.get("points",0),
            })
    except Exception as e:
        print(f"  [hn] ERR {e}")
    return out

def collect_arxiv(now):
    """arXiv 多 query 采集——C1/C2 主供给。用 Atom 命名空间字典，避免全局 register 污染。"""
    import xml.etree.ElementTree as ET
    NS = {"a": "http://www.w3.org/2005/Atom"}
    queries = {
        "eval/verification": '(cat:cs.AI OR cat:cs.LG) AND (abs:verification OR abs:evaluation OR abs:benchmark OR abs:hallucination OR abs:calibration OR abs:reproducibility)',
        "ai-for-science": 'abs:"AI for science" OR abs:"autonomous research" OR abs:"AI scientist" OR abs:"hypothesis generation" OR abs:"scientific discovery" OR abs:"deep research"',
        "agents/reasoning": '(cat:cs.AI OR cat:cs.CL) AND (abs:"LLM agent" OR abs:"multi-agent" OR abs:reasoning OR abs:"self-improving")',
        "forecasting": '(abs:forecasting OR abs:prediction) AND (abs:uncertainty OR abs:calibration OR abs:probability)',
        # C2 供给：幻觉/可靠性/事实性（反 AI 幻觉的 post 弹药）
        "hallucination/reliability": '(cat:cs.CL OR cat:cs.AI) AND (abs:hallucination OR abs:"factual error" OR abs:faithfulness OR abs:"unreliable" OR abs:"misleading" OR abs:"citation" OR abs:"fact verification")',
        # C1 A4Science instance 供给：药物/蛋白/材料/基因组 发现 AI
        "biomed/materials-discovery": '(abs:"drug discovery" OR abs:"protein design" OR abs:"materials discovery" OR abs:"molecular design" OR abs:"genomic") AND (abs:"machine learning" OR abs:model OR abs:agent)',
    }
    out = []
    for label, q in queries.items():
        try:
            url = ("http://export.arxiv.org/api/query?search_query=" +
                   urllib.parse.quote(q) +
                   "&sortBy=submittedDate&sortOrder=descending&max_results=10")
            req = urllib.request.Request(url, headers={"User-Agent": "content-machine/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                root = ET.fromstring(r.read().decode())
        except Exception as e:
            print(f"  [arxiv:{label}] ERR {e}"); time.sleep(2); continue
        for e in root.findall("a:entry", NS):
            title = (e.findtext("a:title", "", NS) or "").strip().replace("\n", " ")
            aid = (e.findtext("a:id", "", NS) or "").strip()
            pub = (e.findtext("a:published", "", NS) or "").strip()
            age = iso_age_h(pub.replace("Z", "+00:00"), now) if pub else None
            if age is None or age > 168:  # 只要一周内
                continue
            out.append({
                "source": "arxiv", "pillar_hint": None, "arxiv_q": label,
                "url": aid, "text": title, "lang": "en",
                "age_h": round(age, 1), "eng": 0,
            })
        time.sleep(3)  # arXiv 礼貌间隔
    return out

def collect_rss(now):
    """Journal RSS——喂"今日能发"的 bio/science 源（Selene 2026-07-24 加）。"""
    import xml.etree.ElementTree as ET
    feeds = {
        "Nature": "https://www.nature.com/nature.rss",
        "NatureComms": "https://www.nature.com/ncomms.rss",
        "Science": "https://www.science.org/rss/news_current.xml",
        "RetractionWatch": "https://retractionwatch.com/feed/",   # ② AI 编造/撤稿真实案例
    }
    lname = lambda el: el.tag.rsplit("}", 1)[-1]  # 去命名空间
    out = []
    for name, url in feeds.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 content-machine/1.0"})
            with urllib.request.urlopen(req, timeout=25) as r:
                root = ET.fromstring(r.read())
        except Exception as e:
            print(f"  [rss:{name}] ERR {e}"); continue
        items = [el for el in root.iter() if lname(el) in ("item", "entry")]  # RSS1.0/2.0/Atom 通吃
        for it in items[:15]:
            title, link = "", ""
            for ch in it:
                ln = lname(ch)
                if ln == "title" and ch.text: title = ch.text.strip()
                elif ln == "link":
                    link = (ch.get("href") or ch.text or "").strip()  # Atom href / RSS text
            if title and link:
                out.append({
                    "source": f"rss:{name}", "pillar_hint": None,
                    "url": link, "text": title, "lang": "en", "age_h": 0, "eng": 0,
                })
    return out

def collect_hf():
    out = []
    try:
        d = _get("https://huggingface.co/api/daily_papers?limit=8")
        for p in d:
            pa = p.get("paper", {})
            out.append({
                # 不给 blanket hint：hint=None 交给判官语义判栏目（不再有关键词 gate）
                "source": "hf_papers", "pillar_hint": None,
                "url": f"https://arxiv.org/abs/{pa.get('id')}",
                "text": pa.get("title",""), "lang": "en",
                "upvotes": pa.get("upvotes",0), "age_h": 0, "eng": pa.get("upvotes",0),
            })
    except Exception as e:
        print(f"  [hf] ERR {e}")
    return out

def collect_reddit(now, subs=("bioinformatics", "computationalbiology", "chemistry",
                              "statistics", "MLQuestions", "datascience", "MachineLearning")):
    """Reddit via safe-social（u/Top_Shop_6167 只读）——从业者问痛(①)供给。
    子版已 agent-reach 调研锁定 2026-07-24：只取有 S.O.S./Help/[Q]/[R] 求助文化的技术版。"""
    out = []
    for sub in subs:
        try:
            r = subprocess.run([SAFE_SOCIAL, "reddit", "sub", sub, "--limit", "15", "--json"],
                               capture_output=True, text=True, timeout=90)
            children = (((json.loads(r.stdout) or {}).get("data") or {}).get("data") or {}).get("children") or []
        except Exception as e:
            print(f"  [reddit:{sub}] ERR {e}"); time.sleep(1.5); continue
        for ch in children:
            p = ch.get("data") or {}
            if p.get("stickied"):
                continue
            created = p.get("created_utc")
            age = None
            if created:
                age = (now.timestamp() - float(created)) / 3600
            if age is not None and age > 72:
                continue
            title = p.get("title", "")
            body = (p.get("selftext") or "")[:200]
            out.append({
                "source": "reddit", "pillar_hint": None, "sub": sub,
                "url": "https://reddit.com" + (p.get("permalink") or ""),
                "text": (title + " " + body).strip()[:500], "lang": "en",
                "age_h": round(age, 1) if age is not None else 0,
                "eng": p.get("score", 0) + p.get("num_comments", 0),
            })
        time.sleep(1.2)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--no-x", action="store_true", help="跳过 X（safe-social 不可用时）")
    ap.add_argument("--no-reddit", action="store_true", help="跳过 Reddit")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    watch = load_yaml(os.path.join(REPO, "config", "watchlist.yml"))

    cands = []
    print("collect: HN + HF papers + arXiv (multi-query) ...")
    cands += collect_hn()
    cands += collect_hf()
    cands += collect_arxiv(now)
    cands += collect_rss(now)
    if not args.no_reddit:
        print("collect: Reddit via safe-social (u/Top_Shop_6167 read-only) ...")
        cands += collect_reddit(now)
    if not args.no_x:
        print("collect: X watchlist via safe-social (@kw90qk read-only) ...")
        cands += collect_x(watch, now)

    # 去重：按归一化 URL（arXiv 抹掉 abs/pdf/export 差异 + 版本号 vN）
    def norm(u):
        u = (u or "").lower().split("://")[-1].rstrip("/")
        m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d+)", u)
        if m: return "arxiv:" + m.group(1)
        return u
    seen, deduped = set(), []
    for c in cands:
        k = norm(c.get("url"))
        if k in seen: continue
        seen.add(k); deduped.append(c)
    dropped = len(cands) - len(deduped)
    cands = deduped
    payload = {"collected_at": now.isoformat(), "count": len(cands),
               "deduped": dropped, "candidates": cands}
    print(f"dedup: 去重 {dropped} 条")
    if args.out:
        json.dump(payload, open(args.out, "w"), ensure_ascii=False, indent=1)
        print(f"wrote {len(cands)} candidates -> {args.out}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=1)[:2000])
    return payload

if __name__ == "__main__":
    main()
