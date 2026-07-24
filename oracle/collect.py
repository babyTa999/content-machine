#!/usr/bin/env python3
"""collect.py — Oracle 采集层（2026-07-24 配置化：sources.yml 真驱动）
读 config/sources.yml，对 status==auto 的源采集候选 spike → stdout / --out。
每个源的参数（subs/queries/feeds/window/limit）来自 sources.yml，不再硬编码——
改 sources.yml = 改采集行为。解析逻辑（RSS/Jina/arXiv XML）仍在 python。
X 只走 safe-social（@kw90qk 只读）；其余 curl / Jina（免费）。
用法：
    python3 collect.py --out raw.json
    --no-x / --no-reddit 跳过对应源
依赖：/Users/admin/.agent-reach-venv/bin/python（含 PyYAML + safe-social 环境）
"""
import json, subprocess, time, datetime as dt, argparse, os, re, urllib.request, urllib.parse
import xml.etree.ElementTree as ET

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
SAFE_SOCIAL = "/Users/admin/Apodex/内容/safe-social"
UA = "Mozilla/5.0 content-machine/1.0"
_LNAME = lambda el: el.tag.rsplit("}", 1)[-1]   # 去 XML 命名空间

def load_yaml(path):
    import yaml
    return yaml.safe_load(open(path, encoding="utf-8"))

def iso_age_h(iso, now):
    try:
        t = dt.datetime.fromisoformat(iso)
        return (now - t).total_seconds() / 3600
    except Exception:
        return None

def _get(url, timeout=20):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())

def _fetch_xml(url, timeout=25):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return ET.fromstring(r.read())

def _jina_read(url, max_chars=8000):
    req = urllib.request.Request(f"https://r.jina.ai/{url}",
        headers={"User-Agent": "content-machine/1.0", "Accept": "text/plain"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode()[:max_chars]

def _strip_html(s):
    return re.sub(r"<[^>]+>", "", s or "").strip()

# ---------- X watchlist（safe-social 只读；参数来自 sources.yml） ----------
def collect_x(watch_cfg, now, cfg):
    n = cfg.get("posts_per_handle", 10)
    w_ind = cfg.get("window_h_individual", 48)
    w_inst = cfg.get("window_h_institution", 168)
    out, handles = [], []
    for tier, hs in watch_cfg["watchlist"].items():
        for h in hs:
            handles.append((tier, h, "C4"))
    for h in watch_cfg.get("institutions", []):   # 机构官号：科学新闻当选题素材，不强塞 C4
        handles.append(("institution", h, None))
    for tier, h, hint in handles:
        try:
            r = subprocess.run([SAFE_SOCIAL, "x", "user-posts", h, "-n", str(n), "--json"],
                               capture_output=True, text=True, timeout=90)
            posts = (json.loads(r.stdout) or {}).get("data") or []
        except Exception as e:
            print(f"  [x] {h}: ERR {e}"); time.sleep(1.2); continue
        for p in posts:
            if p.get("isRetweet"): continue
            age = iso_age_h(p.get("createdAtISO", ""), now)
            max_age = w_inst if tier == "institution" else w_ind
            if age is None or age > max_age or age < 0: continue
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

def _x_age_h(created, now):
    """X search 的 createdAt 是 Twitter 格式 'Thu Jul 23 10:22:18 +0000 2026'。"""
    try:
        t = dt.datetime.strptime(created, "%a %b %d %H:%M:%S %z %Y")
        return (now - t).total_seconds() / 3600
    except Exception:
        return None

def collect_x_search(now, cfg):
    """① / ④ 从 X 捞非 watchlist 的真实抱怨/争论/翻车（safe-social x search，@kw90qk 只读）。
    ⚠️ 拉开间隔别连甩(限流)、别用 macOS 没有的 timeout。判官判 ①(做原创难题) 或 ④(顺势接话)。"""
    groups = cfg.get("queries", {})
    queries = []
    if isinstance(groups, dict):
        for qs in groups.values(): queries += list(qs or [])
    else:
        queries = list(groups or [])
    n = cfg.get("per_query", 6)
    window = cfg.get("window_h", 336)
    min_eng = cfg.get("min_engagement", 5)
    out, seen = [], set()
    for q in queries:
        try:
            r = subprocess.run([SAFE_SOCIAL, "x", "search", q, "-n", str(n), "--json"],
                               capture_output=True, text=True, timeout=90)
            data = (json.loads(r.stdout) or {}).get("data") or []
        except Exception as e:
            print(f"  [x_search:{q[:20]}] ERR {e}"); time.sleep(2.5); continue
        for p in data:
            pid, text = p.get("id"), p.get("text", "")
            if not pid or pid in seen: continue
            m = p.get("metrics") or {}
            eng = m.get("likes",0)+2*m.get("retweets",0)+2*m.get("quotes",0)+m.get("replies",0)
            if eng < min_eng: continue                       # 过滤零互动噪音（生人搜索噪音多）
            age = _x_age_h(p.get("createdAt", ""), now)
            if age is not None and window and (age > window or age < 0): continue
            seen.add(pid)
            author = (p.get("author") or {}).get("screenName", "")
            out.append({
                "source": "x_search", "pillar_hint": None, "query": q,
                "handle": author, "url": f"https://x.com/{author}/status/{pid}",
                "text": text[:500], "lang": p.get("lang"),
                "age_h": round(age,1) if age is not None else 0, "eng": eng, "metrics": m,
            })
        time.sleep(2.5)  # 拉开间隔防限流
    return out

def collect_hn(cfg):
    out, q, hits = [], cfg.get("query", "AI"), cfg.get("hits", 8)
    try:
        d = _get(f"https://hn.algolia.com/api/v1/search?tags=front_page&query={urllib.parse.quote(q)}&hitsPerPage={hits}")
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

def collect_arxiv(now, cfg):
    """arXiv 多 query——存 title + summary 片段（judge 需要正文不只标题）。"""
    NS = {"a": "http://www.w3.org/2005/Atom"}
    queries = cfg.get("queries", {}) or {}
    window, mx = cfg.get("window_h", 168), cfg.get("max_results", 10)
    out = []
    for label, q in queries.items():
        try:
            url = ("http://export.arxiv.org/api/query?search_query=" + urllib.parse.quote(q) +
                   f"&sortBy=submittedDate&sortOrder=descending&max_results={mx}")
            root = _fetch_xml(url)
        except Exception as e:
            print(f"  [arxiv:{label}] ERR {e}"); time.sleep(2); continue
        for e in root.findall("a:entry", NS):
            title = (e.findtext("a:title", "", NS) or "").strip().replace("\n", " ")
            summ = (e.findtext("a:summary", "", NS) or "").strip().replace("\n", " ")
            aid = (e.findtext("a:id", "", NS) or "").strip()
            pub = (e.findtext("a:published", "", NS) or "").strip()
            age = iso_age_h(pub.replace("Z", "+00:00"), now) if pub else None
            if age is None or age > window: continue
            text = title if not summ else f"{title} — {summ}"
            out.append({
                "source": "arxiv", "pillar_hint": None, "arxiv_q": label,
                "url": aid, "text": text[:600], "lang": "en", "age_h": round(age, 1), "eng": 0,
            })
        time.sleep(3)  # arXiv 礼貌间隔
    return out

def collect_rss(now, cfg):
    """Journal RSS（含 Retraction Watch）——存 title + description 片段。"""
    feeds, lim = cfg.get("feeds", {}) or {}, cfg.get("limit_per_feed", 15)
    out = []
    for name, url in feeds.items():
        try:
            root = _fetch_xml(url)
        except Exception as e:
            print(f"  [rss:{name}] ERR {e}"); continue
        items = [el for el in root.iter() if _LNAME(el) in ("item", "entry")]  # RSS1.0/2.0/Atom 通吃
        for it in items[:lim]:
            title, link, desc = "", "", ""
            for ch in it:
                ln = _LNAME(ch)
                if ln == "title" and ch.text: title = ch.text.strip()
                elif ln == "link": link = (ch.get("href") or ch.text or "").strip()
                elif ln in ("description", "summary") and ch.text: desc = _strip_html(ch.text)[:200]
            if title and link:
                text = title if not desc else f"{title} — {desc}"
                out.append({
                    "source": f"rss:{name}", "pillar_hint": None,
                    "url": link, "text": text[:500], "lang": "en", "age_h": 0, "eng": 0,
                })
    return out

def collect_hf(cfg):
    out, lim = [], cfg.get("limit", 8)
    try:
        d = _get(f"https://huggingface.co/api/daily_papers?limit={lim}")
        for p in d:
            pa = p.get("paper", {})
            title = pa.get("title","")
            summ = (pa.get("summary") or "").strip().replace("\n"," ")
            text = title if not summ else f"{title} — {summ}"
            out.append({
                "source": "hf_papers", "pillar_hint": None,
                "url": f"https://arxiv.org/abs/{pa.get('id')}",
                "text": text[:600], "lang": "en",
                "upvotes": pa.get("upvotes",0), "age_h": 0, "eng": pa.get("upvotes",0),
            })
    except Exception as e:
        print(f"  [hf] ERR {e}")
    return out

def collect_reddit(now, cfg):
    """Reddit via safe-social（只读）——从业者问痛(①)。subs 分 ICP 组，帖子带 icp_hint（比关键词准）。"""
    window, lim = cfg.get("window_h", 168), cfg.get("limit_per_sub", 15)
    subs_cfg = cfg.get("subs", {})
    pairs = []
    if isinstance(subs_cfg, dict):                 # 分组 {ICP: [subs]}
        for icp, subs in subs_cfg.items():
            for s in (subs or []): pairs.append((s, icp))
    else:                                          # 平铺 [subs]
        for s in (subs_cfg or []): pairs.append((s, None))
    out = []
    for sub, icp in pairs:
        try:
            r = subprocess.run([SAFE_SOCIAL, "reddit", "sub", sub, "--limit", str(lim), "--json"],
                               capture_output=True, text=True, timeout=90)
            children = (((json.loads(r.stdout) or {}).get("data") or {}).get("data") or {}).get("children") or []
        except Exception as e:
            print(f"  [reddit:{sub}] ERR {e}"); time.sleep(1.5); continue
        for ch in children:
            p = ch.get("data") or {}
            if p.get("stickied"): continue
            created = p.get("created_utc")
            age = (now.timestamp() - float(created)) / 3600 if created else None
            if age is not None and age > window: continue
            title, body = p.get("title", ""), (p.get("selftext") or "")[:300]
            out.append({
                "source": "reddit", "pillar_hint": None, "sub": sub, "icp_hint": icp,
                "url": "https://reddit.com" + (p.get("permalink") or ""),
                "text": (title + " " + body).strip()[:500], "lang": "en",
                "age_h": round(age, 1) if age is not None else 0,
                "eng": p.get("score", 0) + p.get("num_comments", 0),
            })
        time.sleep(1.2)
    return out

def collect_metaculus(cfg):
    """Metaculus 预测题——Jina 抓搜索页 #### [title](url)（REST API 已 403）。"""
    url, out = cfg.get("url"), []
    if not url: return out
    try:
        text = _jina_read(url, max_chars=8000)
        for m in re.finditer(r'####\s*\[([^\]]{15,})\]\((https://www\.metaculus\.com/questions/\d+/[^\)#]+)\)', text):
            out.append({
                "source": "prediction_banks", "pillar_hint": "C1",
                "url": m.group(2).strip(), "text": m.group(1).strip(), "lang": "en", "age_h": 0, "eng": 0,
            })
    except Exception as e:
        print(f"  [metaculus] ERR {e}")
    return out

def collect_official_challenges(cfg):
    """官方悬赏——Jina 抓页链接 + 路径过滤（过滤规则硬编码，pages 配置化）。"""
    pages, out = cfg.get("pages", {}) or {}, []
    for name, url in pages.items():
        try:
            text = _jina_read(url)
            for m in re.finditer(r'\[([^\]]{15,})\]\((https?://[^\)]+)\)', text):
                title, link = m.group(1).strip(), m.group(2).strip()
                if link.endswith(('.svg', '.png', '.jpg')): continue
                if 'usa.gov' in link and not re.search(r'/challenges/[a-z]', link): continue
                if 'xprize.org' in link and not re.search(r'/competitions/[a-z]', link): continue
                if 'arpa-h.gov' in link and '/programs/' not in link and '/open-funding' not in link: continue
                out.append({
                    "source": "official_challenges", "pillar_hint": "C1",
                    "url": link, "text": f"[{name}] {title}", "lang": "en", "age_h": 0, "eng": 0,
                })
        except Exception as e:
            print(f"  [challenges:{name}] ERR {e}")
        time.sleep(1)
    return out

def collect_ai_incidents(cfg):
    """② 事件源——Google News RSS（ICP 镜头 science/deeptech）。存 title + description。
    ⚠️ 只锚科研/deeptech 的 AI 闯祸；判官 ICP 闸砍客服/消费噪音。"""
    queries, per = cfg.get("queries", []) or [], cfg.get("per_query", 8)
    out, seen = [], set()
    for q in queries:
        try:
            url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(q) + "&hl=en-US&gl=US&ceid=US:en"
            root = _fetch_xml(url)
        except Exception as e:
            print(f"  [ai_incident:{q[:22]}] ERR {e}"); time.sleep(1.5); continue
        items = [el for el in root.iter() if _LNAME(el) == "item"]
        for it in items[:per]:
            title, link, desc = "", "", ""
            for ch in it:
                ln = _LNAME(ch)
                if ln == "title" and ch.text: title = ch.text.strip()
                elif ln == "link" and ch.text: link = ch.text.strip()
                elif ln == "description" and ch.text: desc = _strip_html(ch.text)[:180]
            if not title or not link or title.endswith("- Google News") or title in seen: continue
            seen.add(title)
            text = title if not desc else f"{title} — {desc}"
            out.append({
                "source": "ai_incident", "pillar_hint": None,
                "url": link, "text": text[:500], "lang": "en", "age_h": 0, "eng": 0,
            })
        time.sleep(1.5)  # 礼貌间隔
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out")
    ap.add_argument("--no-x", action="store_true", help="跳过 X（safe-social 不可用时）")
    ap.add_argument("--no-reddit", action="store_true", help="跳过 Reddit")
    args = ap.parse_args()

    now = dt.datetime.now(dt.timezone.utc)
    src = load_yaml(os.path.join(REPO, "config", "sources.yml"))
    watch = load_yaml(os.path.join(REPO, "config", "watchlist.yml"))
    def cfg(name): return src.get(name) or {}
    def on(name): return cfg(name).get("status") == "auto"

    cands = []
    if on("hackernews"): cands += collect_hn(cfg("hackernews"))
    if on("hf_papers"): cands += collect_hf(cfg("hf_papers"))
    if on("arxiv"):
        print("collect: arxiv (multi-query) ..."); cands += collect_arxiv(now, cfg("arxiv"))
    if on("journal_rss"): cands += collect_rss(now, cfg("journal_rss"))
    if on("prediction_banks") or on("official_challenges"):
        print("collect: C1 专属 (Metaculus + 悬赏) ...")
        if on("prediction_banks"): cands += collect_metaculus(cfg("prediction_banks"))
        if on("official_challenges"): cands += collect_official_challenges(cfg("official_challenges"))
    if on("ai_incident_db"):
        print("collect: ② AI 闯祸事件 (Google News, ICP 镜头) ...")
        cands += collect_ai_incidents(cfg("ai_incident_db"))
    if on("practitioner_pain_reddit") and not args.no_reddit:
        print("collect: Reddit (safe-social 只读) ...")
        cands += collect_reddit(now, cfg("practitioner_pain_reddit"))
    if on("x_watchlist") and not args.no_x:
        print("collect: X watchlist (safe-social 只读) ...")
        cands += collect_x(watch, now, cfg("x_watchlist"))
    if on("x_keyword_search") and not args.no_x:
        print("collect: X 关键词搜生人 (safe-social 只读, 判官判①/④) ...")
        cands += collect_x_search(now, cfg("x_keyword_search"))

    # 去重：归一化 URL（arXiv 抹 abs/pdf/版本号差异）
    def norm(u):
        u = (u or "").lower().split("://")[-1].rstrip("/")
        m = re.search(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d+)", u)
        return "arxiv:" + m.group(1) if m else u
    seen, deduped = set(), []
    for c in cands:
        k = norm(c.get("url"))
        if k in seen: continue
        seen.add(k); deduped.append(c)
    dropped = len(cands) - len(deduped); cands = deduped
    payload = {"collected_at": now.isoformat(), "count": len(cands), "deduped": dropped, "candidates": cands}
    print(f"dedup: 去重 {dropped} 条")
    if args.out:
        json.dump(payload, open(args.out, "w"), ensure_ascii=False, indent=1)
        print(f"wrote {len(cands)} candidates -> {args.out}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=1)[:2000])
    return payload

if __name__ == "__main__":
    main()
