#!/usr/bin/env python3
"""Oracle collection and deterministic enrichment for the product-led content pipeline.

All external signals are normalized into one candidate schema. X and Reddit stay
read-only through safe-social. Internal materials are never read or persisted here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
SAFE_SOCIAL = os.environ.get("APODEX_SAFE_SOCIAL", str(Path.home() / "Apodex" / "内容" / "safe-social"))
STATE_DB = Path(
    os.environ.get(
        "APODEX_CONTENT_STATE_DB",
        str(Path.home() / "Library" / "Application Support" / "Apodex Content Machine" / "oracle.sqlite3"),
    )
).expanduser()
UA = "Mozilla/5.0 content-machine/2.0"
SOURCE_HEALTH: dict[str, dict[str, Any]] = {}


class XSourceHalt(RuntimeError):
    """Stop remaining X paths after an authentication or rate-limit failure."""


def load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _health(label: str, *, ok: bool, count: int = 0, error: str | None = None) -> None:
    row = SOURCE_HEALTH.setdefault(label, {"calls": 0, "ok_calls": 0, "errors": 0, "items": 0})
    row["calls"] += 1
    row["items"] += count
    if ok:
        row["ok_calls"] += 1
    else:
        row["errors"] += 1
        row["last_error"] = (error or "unknown error")[:500]


def _unwrap(payload: Any) -> Any:
    if isinstance(payload, dict) and payload.get("ok") is True and "data" in payload:
        return payload["data"]
    return payload


def _run_safe_json(args: list[str], label: str, timeout: int = 90) -> Any:
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or f"exit {result.returncode}").strip()
        _health(label, ok=False, error=detail)
        lowered = detail.lower()
        if label.startswith("x_") and (
            "rate_limited" in lowered
            or "rate limited" in lowered
            or "http 429" in lowered
            or "could not authenticate" in lowered
            or "unable to verify x identity" in lowered
            or "http 401" in lowered
        ):
            reason = (
                "authentication_failed"
                if "401" in lowered or "authenticate" in lowered or "verify x identity" in lowered
                else "rate_limited"
            )
            raise XSourceHalt(reason)
        raise RuntimeError(detail[:500])
    raw = result.stdout.lstrip()
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as strict_error:
        try:
            payload, end = json.JSONDecoder().raw_decode(raw)
        except json.JSONDecodeError as exc:
            _health(label, ok=False, error=f"invalid JSON: {exc}")
            raise RuntimeError(f"invalid JSON from {label}") from exc
        remainder = raw[end:].strip()
        if remainder and "More:" not in remainder:
            _health(label, ok=False, error=f"unexpected text after JSON: {strict_error}")
            raise RuntimeError(f"unexpected text after JSON from {label}")
    payload = _unwrap(payload)
    size = len(payload) if isinstance(payload, list) else 1
    _health(label, ok=True, count=size)
    return payload


def _get_json(url: str, timeout: int = 25) -> Any:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _fetch_xml(url: str, timeout: int = 25) -> ET.Element:
    request = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return ET.fromstring(response.read())


def _jina_read(url: str, max_chars: int = 12000) -> str:
    request = urllib.request.Request(
        f"https://r.jina.ai/{url}",
        headers={"User-Agent": UA, "Accept": "text/plain"},
    )
    with urllib.request.urlopen(request, timeout=35) as response:
        return response.read().decode(errors="replace")[:max_chars]


# A reader that returns a bot-check interstitial still returns 200 with a body.
# Treating that body as primary evidence is how a lead_only source silently gets
# upgraded on a fetch that never reached the article.
_BLOCKED_STRONG = (
    "just a moment",
    "performing security verification",
    "checking your browser",
    "checking if the site connection is secure",
    "attention required! | cloudflare",
    "enable javascript and cookies to continue",
    "requiring captcha",
    "are you a robot",
    "access to this page has been denied",
    "access denied",
)
_BLOCKED_WEAK = (
    "captcha",
    "cloudflare",
    "403 forbidden",
    "429 too many requests",
    "subscribe to continue",
    "subscribers only",
    "sign in to read",
    "create an account to continue",
)
MIN_USEFUL_CHARS = 400


def _fetch_quality_issue(content: str) -> str | None:
    """Return a reason string when fetched text cannot serve as primary evidence."""
    text = (content or "").strip()
    if not text:
        return "empty response"
    head = text[:1500].lower()
    for marker in _BLOCKED_STRONG:
        if marker in head:
            return f"interstitial or block page ({marker})"
    if len(text) < MIN_USEFUL_CHARS:
        for marker in _BLOCKED_WEAK:
            if marker in head:
                return f"interstitial or block page ({marker})"
        return f"body too short to be primary evidence ({len(text)} chars)"
    return None


def _wayback_snapshot(url: str) -> str | None:
    api = "https://archive.org/wayback/available?url=" + urllib.parse.quote(url, safe="")
    try:
        request = urllib.request.Request(api, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=20) as response:
            data = json.loads(response.read().decode(errors="replace"))
    except Exception:
        return None
    snapshot = ((data.get("archived_snapshots") or {}).get("closest") or {})
    if snapshot.get("available") and snapshot.get("url"):
        return str(snapshot["url"])
    return None


def _wayback_read(snapshot_url: str, max_chars: int) -> str:
    """Read an archived snapshot directly. r.jina.ai returns 403 for archive.org."""
    request = urllib.request.Request(snapshot_url, headers={"User-Agent": UA})
    with urllib.request.urlopen(request, timeout=35) as response:
        html = response.read().decode(errors="replace")
    body = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html)
    text = re.sub(r"\s+", " ", _strip_html(body)).strip()
    # Drop the Wayback banner so the judge reads the article, not capture metadata.
    # The injected toolbar always ends with the snapshot's own archive URL.
    parts = re.split(r"The Wayback Machine - https?://web\.archive\.org/web/\S+", text, maxsplit=1)
    text = parts[-1].strip() if len(parts) > 1 else text
    return text[:max_chars]


# Half-width parens stay in the character class: Elsevier PII DOIs contain them
# (10.1016/S0140-6736(19)33220-9). Full-width ones are excluded because Chinese
# explanatory text after a locator is wrapped in them.
_DOI_RE = re.compile(r"10\.\d{4,9}/[^\s;；,，、（）\[\]]+")


def _extract_doi(*values: Any) -> str | None:
    for value in values:
        match = _DOI_RE.search(str(value or ""))
        if match:
            return match.group(0).rstrip(".)")
    return None


def _crossref_metadata(doi: str) -> dict[str, Any] | None:
    url = "https://api.crossref.org/works/" + urllib.parse.quote(doi)
    try:
        request = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(request, timeout=25) as response:
            return json.loads(response.read().decode(errors="replace")).get("message")
    except Exception:
        return None


# A curated anchor has a shelf life that depends on what kind of claim it carries.
# A reporting standard from 2010 is still the standard; a compliance rate from 2019
# is a snapshot that may have moved. Conflating the two is how a six-year-old
# percentage ends up in copy as a description of today.
MEASUREMENT_WARN_YEARS = 3
MEASUREMENT_STALE_YEARS = 8


def _anchor_age_note(entry_curated: dict[str, Any], crossref_year: Any) -> tuple[str | None, str]:
    """Return (warning, freshness_label) for a curated anchor.

    Only `measurement` anchors age: they report a real-world rate that can move.
    `standard` anchors do not (a checklist stays the checklist). `case` anchors do
    not age either, but they must never be generalised into a present-day claim.
    """
    anchor_type = str(entry_curated.get("anchor_type") or "").strip().lower()
    window = str(entry_curated.get("measured_window") or "").strip()
    today = dt.date.today().year
    years = None
    tail = re.findall(r"(19|20)(\d{2})", window)
    if tail:
        years = today - int(f"{tail[-1][0]}{tail[-1][1]}")
    elif crossref_year:
        try:
            years = today - int(crossref_year)
        except (TypeError, ValueError):
            years = None

    if anchor_type == "case":
        return (
            "这是一个已完成的具体案例，案例本身不会过期，但不得写成『现在普遍如此』——"
            "它描述的是那一次调查的结果，不是当下的总体状态。",
            "case（案例，不可推广为当下状态）",
        )
    if anchor_type != "measurement":
        return None, f"{anchor_type or 'standard'}（规范或机制，年份不构成时效风险）"
    if years is None:
        return (
            "这是一个测量值，但母表没写测量窗口，无法判断它是否仍然成立。"
            "发布前必须查清测量时点并确认有无更新。",
            "measurement（测量窗口缺失）",
        )
    label = f"measurement（测量距今约 {years} 年）"
    if years >= MEASUREMENT_STALE_YEARS:
        return (
            f"这个数字测量于约 {years} 年前，几乎肯定已有更新。"
            f"不得写成当下状态；发布前必须先查最新数据，并考虑『当年如此、现在如何』"
            f"本身是不是更好的选题角度。",
            label,
        )
    if years >= MEASUREMENT_WARN_YEARS:
        return (
            f"这个数字测量于约 {years} 年前，可能已有更新。"
            f"发布前必须查有无更新数据，并在文案里写清测量时点，不得写成当下状态。",
            label,
        )
    return None, label


def verify_curated_anchor(candidate: dict[str, Any]) -> tuple[str, str, str | None]:
    """Check a curated cell's citation against Crossref.

    The publisher blocks the reader for most DOIs, so full text is not reachable.
    What matters more is that the citation the library asserts is real: without this
    check the pipeline would believe whatever the library says about its own source.
    Returns (content, method, issue).
    """
    curated = candidate.get("curated") or {}
    anchor = curated.get("anchor") or {}
    doi = _extract_doi(anchor.get("locator"), anchor.get("url"), candidate.get("url"))
    if not doi:
        # Standards and reports often have no DOI (checklists, journal policies, URLs).
        # That is not an error — it just means a human must check the citation. Do not
        # block the cell, but say so plainly so the judge sees the gap.
        age_warning, freshness = _anchor_age_note(curated, anchor.get("year"))
        content = json.dumps(
            {
                "library_claims": anchor,
                "anchor_freshness": freshness,
                "freshness_warning": age_warning,
                "note": (
                    "该锚没有 DOI（多为 checklist、期刊政策或机构页面），"
                    "引证未经机器校验，发布前必须人工核到一手。"
                ),
            },
            ensure_ascii=False,
            indent=2,
        )
        return content, "curated anchor (no DOI)", None
    meta = _crossref_metadata(doi)
    if not meta:
        return "", "crossref", f"Crossref 查不到该 DOI：{doi}"
    authors = meta.get("author") or []
    first_family = str((authors[0].get("family") if authors else "") or "")
    parts = (
        (meta.get("published-print") or meta.get("published-online") or {}).get("date-parts")
        or [[None]]
    )
    year = parts[0][0]
    real_title = (meta.get("title") or [""])[0]
    real_venue = (meta.get("container-title") or [""])[0]
    mismatches: list[str] = []
    claimed_year = anchor.get("year")
    if claimed_year and year and int(claimed_year) != int(year):
        mismatches.append(f"年份：母表写 {claimed_year}，Crossref 是 {year}")
    claimed_venue = str(anchor.get("venue") or "")
    if claimed_venue and real_venue:
        left = re.sub(r"[^a-z]", "", claimed_venue.lower())
        right = re.sub(r"[^a-z]", "", real_venue.lower())
        if left and right and left not in right and right not in left:
            mismatches.append(f"期刊：母表写 {claimed_venue}，Crossref 是 {real_venue}")
    citation = str(anchor.get("citation") or "")
    if first_family and citation and first_family.lower() not in citation.lower():
        mismatches.append(f"第一作者：Crossref 是 {first_family}，母表 citation 里没有")
    age_warning, freshness = _anchor_age_note(curated, year)
    content = json.dumps(
        {
            "doi": doi,
            "crossref_title": real_title,
            "crossref_venue": real_venue,
            "crossref_year": year,
            "crossref_first_author": first_family,
            "crossref_volume": meta.get("volume"),
            "crossref_issue": meta.get("issue"),
            "crossref_page": meta.get("page"),
            "library_claims": anchor,
            "anchor_freshness": freshness,
            "measured_window": curated.get("measured_window"),
            "freshness_warning": age_warning,
            "note": (
                "出版商阻止全文抓取，此处为 Crossref 元数据核对结果。"
                "引证本身已核实；正文里的具体表述仍须按 needs_verification 回原文确认。"
            ),
        },
        ensure_ascii=False,
        indent=2,
    )
    if mismatches:
        return content, "crossref", "引证与 Crossref 不一致：" + "；".join(mismatches)
    # A stale measurement is not a fetch failure: the judge still gets the content,
    # but it must see the warning rather than quietly reuse an old percentage.
    return content, "crossref", None


def _read_web(url: str, max_chars: int) -> tuple[str, str, str | None]:
    """Fetch a page, falling back to a Wayback snapshot when the live site blocks us.

    Returns (content, method, issue). A non-None issue means the content must not
    be treated as primary evidence.
    """
    content = _jina_read(url, max_chars)
    issue = _fetch_quality_issue(content)
    if not issue:
        return content, "jina reader", None
    snapshot = _wayback_snapshot(url)
    if snapshot:
        try:
            archived = _wayback_read(snapshot, max_chars)
        except Exception:
            archived = ""
        if archived and not _fetch_quality_issue(archived):
            return archived, "wayback snapshot", None
    return content, "jina reader", issue


def _strip_html(value: str | None) -> str:
    return re.sub(r"<[^>]+>", " ", value or "").replace("&nbsp;", " ").strip()


def _parse_datetime(value: Any) -> dt.datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        return dt.datetime.fromtimestamp(float(value), tz=dt.timezone.utc)
    raw = str(value).strip()
    for parser in (
        lambda: dt.datetime.fromisoformat(raw.replace("Z", "+00:00")),
        lambda: dt.datetime.strptime(raw, "%a %b %d %H:%M:%S %z %Y"),
        lambda: email.utils.parsedate_to_datetime(raw),
    ):
        try:
            parsed = parser()
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return parsed.astimezone(dt.timezone.utc)
        except (ValueError, TypeError, OverflowError):
            continue
    return None


def _age_h(value: Any, now: dt.datetime) -> float | None:
    parsed = _parse_datetime(value)
    return (now - parsed).total_seconds() / 3600 if parsed else None


def _canonical_url(url: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlsplit(raw if "://" in raw else f"https://{raw}")
    host = parsed.netloc.lower().removeprefix("www.")
    path = parsed.path.rstrip("/") or "/"
    if host in {"x.com", "twitter.com", "reddit.com", "old.reddit.com"}:
        query = ""
    else:
        kept = [
            (key, value)
            for key, value in urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
            if not key.lower().startswith("utm_")
        ]
        query = urllib.parse.urlencode(kept)
    return urllib.parse.urlunsplit(("https", host, path, query, ""))


def _normalized_text(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"https?://\S+", "", text or "")).strip().lower()


def _action_hints(platform: str) -> list[str]:
    if platform == "x":
        return ["original_post", "x_reply", "x_quote"]
    if platform == "reddit":
        return ["original_post", "reddit_reply"]
    return ["original_post"]


def make_signal(
    *,
    platform: str,
    source_path: str,
    url: str,
    text: str,
    external_id: str | None = None,
    title: str | None = None,
    author: dict[str, Any] | None = None,
    published_at: Any = None,
    metrics: dict[str, Any] | None = None,
    source_mode: str | None = None,
    provenance: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    lang: str | None = "en",
    known_author: bool = False,
    institution: bool = False,
    evidence_role: str | None = None,
    signal_group: str | None = None,
    lead_only: bool = False,
) -> dict[str, Any]:
    canonical = _canonical_url(url)
    clean_text = _normalized_text(text)
    stable_key = f"{platform}:{external_id}" if external_id else canonical or clean_text
    candidate_id = "cand_" + hashlib.sha256(stable_key.encode()).hexdigest()[:16]
    content_hash = hashlib.sha256(clean_text.encode()).hexdigest()
    story_basis = re.sub(r"[^a-z0-9 ]", "", clean_text)[:500]
    story_key = hashlib.sha256(story_basis.encode()).hexdigest()[:20]
    published = _parse_datetime(published_at)
    metric_values = metrics or {}
    engagement = (
        int(metric_values.get("likes") or metric_values.get("score") or 0)
        + 2 * int(metric_values.get("retweets") or 0)
        + 2 * int(metric_values.get("quotes") or 0)
        + int(metric_values.get("replies") or metric_values.get("num_comments") or 0)
    )
    return {
        "candidate_id": candidate_id,
        "external_id": str(external_id or ""),
        "platform": platform,
        "source": platform,
        "source_role": evidence_role or "discovery",
        "source_path": source_path,
        "discovery_paths": [source_path],
        "source_mode": source_mode,
        "url": url,
        "canonical_url": canonical,
        "title": title or "",
        "text": (text or "").strip()[:4000],
        "content_hash": content_hash,
        "story_key": story_key,
        "lang": lang,
        "published_at": published.isoformat() if published else None,
        "age_h": None,
        "author": author or {},
        "authority": {
            "verified": bool((author or {}).get("verified")),
            "known_watchlist": known_author,
            "institution": institution,
        },
        "metrics": metric_values,
        "engagement": engagement,
        "provenance": [provenance or {}],
        "context": {
            **(context or {}),
            "evidence_role": evidence_role,
            "signal_group": signal_group,
            "lead_only": lead_only,
        },
        "problem_shape_hints": [],
        "thesis_hints": [],
        "action_hints": _action_hints(platform),
    }


def _tweet_signal(
    post: dict[str, Any],
    *,
    source_path: str,
    now: dt.datetime,
    source_mode: str | None = None,
    provenance: dict[str, Any] | None = None,
    known_author: bool = False,
    institution: bool = False,
) -> dict[str, Any] | None:
    post_id = str(post.get("id") or "")
    author_data = post.get("author") or {}
    handle = str(author_data.get("screenName") or provenance and provenance.get("handle") or "").lstrip("@")
    if not post_id or not handle:
        return None
    article = " ".join(filter(None, [post.get("articleTitle"), post.get("articleText")]))
    text = "\n\n".join(filter(None, [str(post.get("text") or ""), article]))
    created = post.get("createdAtISO") or post.get("createdAt")
    signal = make_signal(
        platform="x",
        source_path=source_path,
        url=f"https://x.com/{handle}/status/{post_id}",
        external_id=post_id,
        text=text,
        author={
            "id": author_data.get("id"),
            "handle": handle,
            "name": author_data.get("name"),
            "verified": bool(author_data.get("verified")),
        },
        published_at=created,
        metrics=post.get("metrics") or {},
        source_mode=source_mode,
        provenance=provenance,
        context={
            "urls": post.get("urls") or [],
            "media": post.get("media") or [],
            "quoted": post.get("quotedTweet"),
        },
        lang=post.get("lang") or "en",
        known_author=known_author,
        institution=institution,
    )
    signal["age_h"] = round(_age_h(created, now), 1) if _age_h(created, now) is not None else None
    return signal


def _quoted_signal(parent: dict[str, Any], now: dt.datetime, parent_signal: dict[str, Any]) -> dict[str, Any] | None:
    quoted = parent.get("quotedTweet")
    if not isinstance(quoted, dict):
        return None
    quoted_id = str(quoted.get("id") or "")
    quoted_author = quoted.get("author") or {}
    handle = str(quoted_author.get("screenName") or "").lstrip("@")
    if not quoted_id or not handle:
        return None
    return make_signal(
        platform="x",
        source_path="x_conversation_quote",
        url=f"https://x.com/{handle}/status/{quoted_id}",
        external_id=quoted_id,
        text=str(quoted.get("text") or ""),
        author={"handle": handle, "name": quoted_author.get("name"), "verified": False},
        metrics={},
        provenance={"found_in": parent_signal["candidate_id"], "via": "quote"},
        context={"quoted_by": parent_signal["url"]},
        lang=parent.get("lang") or "en",
    )


def _x_signals(
    posts: list[dict[str, Any]],
    *,
    source_path: str,
    now: dt.datetime,
    source_mode: str | None,
    provenance: dict[str, Any],
    window_h: int,
    min_engagement: int,
    known_author: bool = False,
    institution: bool = False,
) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for post in posts:
        if post.get("isRetweet"):
            continue
        signal = _tweet_signal(
            post,
            source_path=source_path,
            now=now,
            source_mode=source_mode,
            provenance=provenance,
            known_author=known_author,
            institution=institution,
        )
        if not signal:
            continue
        age = signal.get("age_h")
        if age is not None and (age < 0 or age > window_h):
            continue
        if signal["engagement"] < min_engagement:
            continue
        output.append(signal)
        quoted = _quoted_signal(post, now, signal)
        if quoted:
            output.append(quoted)
    return output


def _watch_handles(watch_cfg: dict[str, Any]) -> list[tuple[str, str, bool]]:
    handles: list[tuple[str, str, bool]] = []
    for tier, names in (watch_cfg.get("watchlist") or {}).items():
        handles.extend((str(name).lstrip("@"), str(tier), False) for name in names or [])
    handles.extend((str(name).lstrip("@"), "institution", True) for name in watch_cfg.get("institutions") or [])
    return handles


# A KOL post is worth quoting when it carries a position, not when it announces a paper.
# Ambient's 81 posts show the pattern: it quotes what people *claim*, never what they publish.
# See 内容/竞品brain.md §6.2.
_STANCE_MARKERS = (
    "i think", "i believe", "my view", "the problem is", "the real question",
    "nobody is talking about", "everyone assumes", "this is why", "the mistake",
    "we should", "we need to", "it's time", "unpopular opinion", "hot take",
    "disagree", "wrong about", "misleading", "overstated", "overhyped",
    "doesn't mean", "does not mean", "is not the same", "worth asking",
    "here is the thing", "here's the thing", "let me explain", "the tell",
    "should worry", "concerning", "skeptical", "i don't buy",
)
# Paper-promo and self-promo shapes. Ambient/Epoch never quote these.
_PROMO_MARKERS = (
    "new paper", "our paper", "accepted at", "check out our", "excited to share",
    "excited to announce", "thrilled to", "proud to", "happy to share",
    "now published", "out now in", "preprint is", "read the paper",
    "congrats", "congratulations", "thanks to", "grateful", "honored",
    "we're hiring", "we are hiring", "apply here", "join us", "register",
    "livestream", "webinar", "our new blog", "link in", "deadline to submit",
)


def _stance_score(text: str) -> tuple[int, str]:
    """Rank a KOL post for quote-worthiness. Positive = carries a position."""
    low = (text or "").lower()
    promo = sum(1 for marker in _PROMO_MARKERS if marker in low)
    stance = sum(1 for marker in _STANCE_MARKERS if marker in low)
    # A question that is not promo is usually a framing question worth engaging.
    question = 1 if "?" in low and promo == 0 else 0
    score = stance * 2 + question - promo * 3
    reason = f"stance={stance} question={question} promo={promo}"
    return score, reason


def collect_x_interaction_pool(
    watch_cfg: dict[str, Any], now: dt.datetime, cfg: dict[str, Any]
) -> list[dict[str, Any]]:
    """KOL individual accounts, for C8 interaction only — never the originals pool.

    Separate from collect_x_watchlist on purpose: institution accounts post paper
    promos (07-27: 48 candidates, 0 keep), while individual KOLs post positions,
    which is what a quote needs as raw material.
    """
    pool: list[tuple[str, str]] = []
    for group, entries in (watch_cfg.get("interaction_pool") or {}).items():
        for entry in entries or []:
            handle = str((entry or {}).get("handle") or "").lstrip("@")
            if handle:
                pool.append((handle, str(group)))
    if not pool:
        return []
    limit = int(cfg.get("posts_per_handle", 8))
    interval = float(cfg.get("request_interval_s", 4.0))
    window_h = int(cfg.get("window_h", 48))
    min_stance = int(cfg.get("min_stance_score", 1))
    run_limit = min(int(cfg.get("handles_per_run", 10)), len(pool))
    start = now.date().toordinal() % len(pool)
    selected = [pool[(start + offset) % len(pool)] for offset in range(run_limit)]
    output: list[dict[str, Any]] = []
    kept = 0
    for handle, group in selected:
        try:
            posts = _run_safe_json(
                [SAFE_SOCIAL, "x", "user-posts", handle, "-n", str(limit), "--json"],
                "x_interaction_pool",
            )
            if not isinstance(posts, list):
                posts = []
            signals = _x_signals(
                posts,
                source_path="x_interaction_pool",
                now=now,
                source_mode="timeline",
                provenance={"handle": handle, "group": group, "pool": "interaction"},
                window_h=window_h,
                min_engagement=0,
                known_author=True,
            )
            for signal in signals:
                score, reason = _stance_score(str(signal.get("text") or ""))
                if score < min_stance:
                    continue
                signal["action_hints"] = ["x_reply", "x_quote"]
                signal["context"] = {
                    **(signal.get("context") or {}),
                    "pool": "interaction",
                    "stance_score": score,
                    "stance_reason": reason,
                }
                output.append(signal)
                kept += 1
        except XSourceHalt:
            raise
        except Exception as exc:
            print(f"  [x_interaction:{handle}] ERR {exc}")
        time.sleep(interval)
    _health("x_interaction_pool", ok=True, count=kept)
    print(f"  interaction pool -> {kept} stance-bearing post(s) from {len(selected)} KOL(s)")
    return output


def collect_x_watchlist(watch_cfg: dict[str, Any], now: dt.datetime, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    limit = int(cfg.get("posts_per_handle", 12))
    interval = float(cfg.get("request_interval_s", 1.2))
    handles = _watch_handles(watch_cfg)
    run_limit = min(int(cfg.get("handles_per_run", len(handles))), len(handles))
    start = now.date().toordinal() % len(handles) if handles else 0
    selected = [handles[(start + offset) % len(handles)] for offset in range(run_limit)]
    for handle, tier, institution in selected:
        try:
            posts = _run_safe_json(
                [SAFE_SOCIAL, "x", "user-posts", handle, "-n", str(limit), "--json"],
                "x_watchlist",
            )
            if not isinstance(posts, list):
                posts = []
            window = int(cfg.get("window_h_institution" if institution else "window_h_individual", 168 if institution else 72))
            output.extend(
                _x_signals(
                    posts,
                    source_path="x_watchlist",
                    now=now,
                    source_mode="timeline",
                    provenance={"handle": handle, "tier": tier},
                    window_h=window,
                    min_engagement=0,
                    known_author=True,
                    institution=institution,
                )
            )
        except XSourceHalt:
            raise
        except Exception as exc:
            print(f"  [x_watchlist:{handle}] ERR {exc}")
        time.sleep(interval)
    return output


def _flatten_queries(groups: Any) -> list[tuple[str, str]]:
    if isinstance(groups, dict):
        return [(str(group), str(query)) for group, queries in groups.items() for query in queries or []]
    return [("default", str(query)) for query in groups or []]


def _editorial_intent_queries(now: dt.datetime) -> list[tuple[str, str]]:
    configured = load_yaml(REPO / "config" / "editorial_intents.yml")
    queries: list[tuple[str, str]] = []
    for intent_id, definition in (configured.get("intents") or {}).items():
        if definition.get("status") != "active":
            continue
        available = (definition.get("retrieval") or {}).get("x_queries") or []
        if available:
            query = available[now.date().toordinal() % len(available)]
            queries.append((str(intent_id), str(query)))
    return queries


def collect_x_search(now: dt.datetime, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    interval = float(cfg.get("request_interval_s", 2.0))
    lang = str(cfg.get("lang", "en"))
    excluded = [str(item) for item in cfg.get("exclude") or []]
    queries = _editorial_intent_queries(now)
    run_limit = min(int(cfg.get("intent_groups_per_run", len(queries))), len(queries))
    start = now.date().toordinal() % len(queries) if queries else 0
    selected = [queries[(start + offset) % len(queries)] for offset in range(run_limit)]
    for group, query in selected:
        for mode, mode_cfg in (cfg.get("modes") or {}).items():
            args = [SAFE_SOCIAL, "x", "search", query, "--type", str(mode), "--lang", lang]
            for item in excluded:
                args.extend(["--exclude", item])
            args.extend(["-n", str(mode_cfg.get("per_query", 10)), "--json"])
            try:
                posts = _run_safe_json(args, f"x_query_{str(mode).lower()}")
                if not isinstance(posts, list):
                    posts = []
                output.extend(
                    _x_signals(
                        posts,
                        source_path=f"x_query_{str(mode).lower()}",
                        now=now,
                        source_mode=str(mode),
                        provenance={"editorial_intent_id": group, "query": query},
                        window_h=int(mode_cfg.get("window_h", 168)),
                        min_engagement=int(mode_cfg.get("min_engagement", 0)),
                    )
                )
            except XSourceHalt:
                raise
            except Exception as exc:
                print(f"  [x_query:{mode}:{query[:30]}] ERR {exc}")
            time.sleep(interval)
    return output


def collect_x_conversation(watch_cfg: dict[str, Any], now: dt.datetime, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    handles = [item[0] for item in _watch_handles(watch_cfg)]
    if not handles:
        return []
    count = min(int(cfg.get("handles_per_run", 8)), len(handles))
    start = now.date().toordinal() % len(handles)
    selected = [handles[(start + offset) % len(handles)] for offset in range(count)]
    output: list[dict[str, Any]] = []
    interval = float(cfg.get("request_interval_s", 2.0))
    mode = str(cfg.get("mode", "Latest"))
    for seed in selected:
        args = [
            SAFE_SOCIAL,
            "x",
            "search",
            "",
            "--to",
            seed,
            "--type",
            mode,
            "--lang",
            "en",
            "--exclude",
            "retweets",
            "-n",
            str(cfg.get("per_handle", 12)),
            "--json",
        ]
        try:
            posts = _run_safe_json(args, "x_conversation")
            if not isinstance(posts, list):
                posts = []
            output.extend(
                _x_signals(
                    posts,
                    source_path="x_conversation",
                    now=now,
                    source_mode=mode,
                    provenance={"seed_handle": seed, "relation": "reply_to"},
                    window_h=int(cfg.get("window_h", 72)),
                    min_engagement=int(cfg.get("min_engagement", 1)),
                )
            )
        except XSourceHalt:
            raise
        except Exception as exc:
            print(f"  [x_conversation:{seed}] ERR {exc}")
        time.sleep(interval)
    return output


def _dynamic_handles(limit: int) -> list[str]:
    if not STATE_DB.exists():
        return []
    try:
        with sqlite3.connect(STATE_DB) as connection:
            rows = connection.execute(
                """SELECT handle FROM accounts
                   WHERE platform='x' AND status='dynamic_watch'
                   ORDER BY evidence_keep_count DESC, seen_days DESC
                   LIMIT ?""",
                (limit,),
            ).fetchall()
        return [str(row[0]) for row in rows]
    except sqlite3.Error:
        return []


def collect_x_dynamic(now: dt.datetime, cfg: dict[str, Any], state_cfg: dict[str, Any]) -> list[dict[str, Any]]:
    handles = _dynamic_handles(int(state_cfg.get("dynamic_watch_limit", 20)))
    output: list[dict[str, Any]] = []
    interval = float(cfg.get("request_interval_s", 1.2))
    for handle in handles:
        try:
            posts = _run_safe_json(
                [SAFE_SOCIAL, "x", "user-posts", handle, "-n", str(cfg.get("posts_per_handle", 10)), "--json"],
                "x_dynamic_watch",
            )
            if not isinstance(posts, list):
                posts = []
            output.extend(
                _x_signals(
                    posts,
                    source_path="x_dynamic_watch",
                    now=now,
                    source_mode="timeline",
                    provenance={"handle": handle, "tier": "dynamic"},
                    window_h=int(cfg.get("window_h", 168)),
                    min_engagement=0,
                    known_author=False,
                )
            )
        except XSourceHalt:
            raise
        except Exception as exc:
            print(f"  [x_dynamic:{handle}] ERR {exc}")
        time.sleep(interval)
    return output


def _reddit_children(payload: Any) -> list[dict[str, Any]]:
    node = payload
    if isinstance(node, dict) and isinstance(node.get("data"), dict):
        node = node["data"]
    children = node.get("children") if isinstance(node, dict) else None
    if not isinstance(children, list):
        return []
    return [item.get("data") or {} for item in children if isinstance(item, dict)]


def collect_reddit(now: dt.datetime, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    communities = [
        (domain, str(sub))
        for domain, subs in (cfg.get("communities") or {}).items()
        for sub in subs or []
    ]
    queries = [str(query) for query in cfg.get("queries") or []]
    modes = cfg.get("modes") or []
    rotate_count = max(1, int(cfg.get("query_rotation_per_sub", 1)))
    interval = float(cfg.get("request_interval_s", 1.5))
    output: list[dict[str, Any]] = []
    for index, (domain, sub) in enumerate(communities):
        if not queries:
            break
        offset = (now.date().toordinal() + index) % len(queries)
        selected_queries = [queries[(offset + step) % len(queries)] for step in range(rotate_count)]
        for query in selected_queries:
            for mode in modes:
                sort = str(mode.get("sort", "top"))
                time_filter = str(mode.get("time", "year"))
                args = [
                    SAFE_SOCIAL,
                    "reddit",
                    "search",
                    query,
                    "-r",
                    sub,
                    "--sort",
                    sort,
                    "--time",
                    time_filter,
                    "--limit",
                    str(mode.get("limit", 12)),
                    "--json",
                ]
                try:
                    rows = _reddit_children(_run_safe_json(args, f"reddit_{sort}"))
                    for row in rows:
                        if row.get("stickied"):
                            continue
                        post_id = str(row.get("id") or "")
                        permalink = str(row.get("permalink") or "")
                        if not post_id or not permalink:
                            continue
                        title = str(row.get("title") or "")
                        body = str(row.get("selftext") or "")[:2200]
                        signal = make_signal(
                            platform="reddit",
                            source_path=f"reddit_{sort}",
                            url="https://reddit.com" + permalink,
                            external_id=post_id,
                            title=title,
                            text="\n\n".join(filter(None, [title, body])),
                            author={"handle": row.get("author"), "verified": False},
                            published_at=row.get("created_utc"),
                            metrics={
                                "score": row.get("score", 0),
                                "num_comments": row.get("num_comments", 0),
                                "upvote_ratio": row.get("upvote_ratio"),
                            },
                            source_mode=f"{sort}/{time_filter}",
                            provenance={"community": sub, "domain": domain, "query": query},
                            context={"subreddit": sub, "domain": domain},
                            lang="en",
                            evidence_role=str(cfg.get("evidence_role") or "community_case_lead"),
                            lead_only=bool(cfg.get("lead_only", True)),
                        )
                        signal["age_h"] = round(_age_h(row.get("created_utc"), now), 1) if row.get("created_utc") else None
                        signal["domain_hints"] = [domain]
                        output.append(signal)
                except Exception as exc:
                    print(f"  [reddit:{sub}:{sort}] ERR {exc}")
                time.sleep(interval)
    return output


def collect_rss(now: dt.datetime, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for name, definition in (cfg.get("feeds") or {}).items():
        if isinstance(definition, dict):
            url = str(definition.get("url") or "")
            evidence_role = str(definition.get("evidence_role") or "reputable_news_lead")
            signal_group = str(definition.get("signal_group") or "") or None
        else:
            url = str(definition)
            evidence_role = "reputable_news_lead"
            signal_group = None
        if not url:
            continue
        try:
            root = _fetch_xml(str(url))
            items = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] in {"item", "entry"}]
            for item in items[: int(cfg.get("limit_per_feed", 18))]:
                fields: dict[str, str] = {}
                for child in item:
                    key = child.tag.rsplit("}", 1)[-1]
                    if key == "link":
                        fields[key] = str(child.get("href") or child.text or "").strip()
                    elif child.text:
                        fields[key] = child.text.strip()
                title = fields.get("title", "")
                link = fields.get("link", "")
                if not title or not link:
                    continue
                summary = _strip_html(fields.get("description") or fields.get("summary"))[:1200]
                published = fields.get("pubDate") or fields.get("published") or fields.get("updated")
                signal = make_signal(
                    platform="rss",
                    source_path=f"rss_{name.lower()}",
                    url=link,
                    title=title,
                    text=" — ".join(filter(None, [title, summary])),
                    published_at=published,
                    provenance={"feed": name, "feed_url": url},
                    context={"feed": name},
                    evidence_role=evidence_role,
                    signal_group=signal_group,
                    lead_only=bool(cfg.get("lead_only", False)),
                )
                signal["age_h"] = round(_age_h(published, now), 1) if _age_h(published, now) is not None else None
                output.append(signal)
            _health(f"rss_{name.lower()}", ok=True, count=len(items))
        except Exception as exc:
            _health(f"rss_{name.lower()}", ok=False, error=str(exc))
            print(f"  [rss:{name}] ERR {exc}")
    return output


def collect_google_news(now: dt.datetime, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    per_query = int(cfg.get("per_query", 6))
    for query in cfg.get("queries") or []:
        url = "https://news.google.com/rss/search?q=" + urllib.parse.quote(str(query)) + "&hl=en-US&gl=US&ceid=US:en"
        try:
            root = _fetch_xml(url)
            items = [element for element in root.iter() if element.tag.rsplit("}", 1)[-1] == "item"]
            for item in items[:per_query]:
                fields = {child.tag.rsplit("}", 1)[-1]: child.text or "" for child in item}
                title, link = fields.get("title", "").strip(), fields.get("link", "").strip()
                if not title or not link:
                    continue
                summary = _strip_html(fields.get("description"))[:800]
                published = fields.get("pubDate")
                signal = make_signal(
                    platform="rss",
                    source_path="google_news_signal",
                    url=link,
                    title=title,
                    text=" — ".join(filter(None, [title, summary])),
                    published_at=published,
                    provenance={"query": query},
                    context={"lead_only": True},
                    evidence_role="reputable_news_lead",
                    lead_only=True,
                )
                signal["age_h"] = round(_age_h(published, now), 1) if _age_h(published, now) is not None else None
                output.append(signal)
            _health("google_news_signal", ok=True, count=len(items[:per_query]))
        except Exception as exc:
            _health("google_news_signal", ok=False, error=str(exc))
            print(f"  [google_news:{str(query)[:28]}] ERR {exc}")
        time.sleep(1.2)
    return output


def collect_official_indexes(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract individual current updates from stable first-party index pages."""
    output: list[dict[str, Any]] = []
    limit = int(cfg.get("limit_per_page", 20))
    for name, definition in (cfg.get("pages") or {}).items():
        url = str((definition or {}).get("url") or "")
        if not url:
            continue
        allowed_hosts = {
            str(host).lower().removeprefix("www.")
            for host in (definition.get("allow_hosts") or [])
        }
        include_paths = [str(path) for path in definition.get("include_paths") or []]
        title_exclude = [str(item).lower() for item in definition.get("title_exclude") or []]
        try:
            content = _jina_read(url, 30000)
            seen: set[str] = set()
            for match in re.finditer(r"\[([^\]]{8,})\]\(([^\)]+)\)", content):
                title = _strip_html(match.group(1)).strip()
                link = urllib.parse.urljoin(url, match.group(2).strip())
                parsed = urllib.parse.urlparse(link)
                host = parsed.netloc.lower().removeprefix("www.")
                if allowed_hosts and host not in allowed_hosts:
                    continue
                if include_paths and not any(parsed.path.startswith(path) for path in include_paths):
                    continue
                canonical = _canonical_url(link)
                if canonical in seen or canonical == _canonical_url(url):
                    continue
                lowered_title = title.lower()
                if any(marker in lowered_title for marker in title_exclude):
                    continue
                seen.add(canonical)
                output.append(
                    make_signal(
                        platform="web",
                        source_path="official_update",
                        url=link,
                        title=title,
                        text=f"[{name}] {title}",
                        provenance={"index": url, "program": name},
                        evidence_role=str(definition.get("evidence_role") or "official_record"),
                        signal_group=str(definition.get("signal_group") or "") or None,
                    )
                )
                if len(seen) >= limit:
                    break
            _health(f"official_{str(name).lower()}", ok=True, count=len(seen))
        except Exception as exc:
            _health(f"official_{str(name).lower()}", ok=False, error=str(exc))
            print(f"  [official:{name}] ERR {exc}")
        time.sleep(0.8)
    return output


def collect_federal_register(now: dt.datetime, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    """US Federal Register: proposed rules, notices and final rules from science agencies.

    Structured conditions only. conditions[term] is a fuzzy OR match that, combined
    with order=newest, returns the same newest documents for every query.
    """
    base = str(cfg.get("base_url") or "https://www.federalregister.gov/api/v1/documents.json")
    per_page = int(cfg.get("per_page", 20))
    recent_days = int(cfg.get("recent_days", 10))
    agencies = [str(item) for item in (cfg.get("agencies") or [])]
    excludes = [str(item).lower() for item in (cfg.get("title_exclude") or [])]
    today = now.date()
    fields = [
        "title", "abstract", "agencies", "publication_date",
        "comments_close_on", "html_url", "type", "document_number",
    ]
    output: list[dict[str, Any]] = []
    for query in cfg.get("queries") or []:
        name = str(query.get("name") or "federal_register")
        params = [
            ("per_page", str(per_page)),
            ("order", "newest"),
            ("conditions[type][]", str(query.get("doc_type") or "PRORULE")),
        ]
        if query.get("open_comments_only"):
            params.append(("conditions[comment_date][gte]", today.isoformat()))
        else:
            params.append(
                ("conditions[publication_date][gte]", (today - dt.timedelta(days=recent_days)).isoformat())
            )
        params.extend(("conditions[agencies][]", agency) for agency in agencies)
        params.extend(("fields[]", field) for field in fields)
        url = base + "?" + urllib.parse.urlencode(params)
        try:
            request = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
            with urllib.request.urlopen(request, timeout=30) as response:
                payload = json.loads(response.read().decode(errors="replace"))
        except Exception as exc:
            _health(f"federal_register:{name}", ok=False, error=str(exc))
            continue
        results = payload.get("results") or []
        kept = 0
        for item in results:
            title = str(item.get("title") or "").strip()
            if not title:
                continue
            lowered = title.lower()
            if any(marker in lowered for marker in excludes):
                continue
            link = str(item.get("html_url") or "")
            abstract = str(item.get("abstract") or "")
            closes = item.get("comments_close_on")
            agency_names = ", ".join(
                str(agency.get("name") or "") for agency in (item.get("agencies") or [])
            )
            body = abstract or title
            if closes:
                body = f"{body}（公众意见截止 {closes}）"
            output.append(
                make_signal(
                    platform="web",
                    source_path="federal_register",
                    url=link,
                    title=title,
                    text=body,
                    published_at=item.get("publication_date"),
                    institution=True,
                    evidence_role=str(query.get("evidence_role") or "official_record"),
                    signal_group=str(query.get("signal_group") or "") or None,
                    provenance={
                        "feed": "Federal Register",
                        "query": name,
                        "doc_type": item.get("type"),
                        "document_number": item.get("document_number"),
                        "agencies": agency_names,
                        "comments_close_on": closes,
                    },
                )
            )
            kept += 1
        _health(f"federal_register:{name}", ok=True, count=kept)
        print(f"  [federal_register:{name}] {kept}/{len(results)} after title filter")
    return output


def collect_hackernews(now: dt.datetime, cfg: dict[str, Any]) -> list[dict[str, Any]]:
    try:
        query = str(cfg.get("query", "scientific research tools"))
        data = _get_json(
            "https://hn.algolia.com/api/v1/search?tags=front_page&query="
            + urllib.parse.quote(query)
            + "&hitsPerPage="
            + str(cfg.get("hits", 8))
        )
        output = []
        for row in data.get("hits") or []:
            object_id = str(row.get("objectID") or "")
            url = row.get("url") or f"https://news.ycombinator.com/item?id={object_id}"
            output.append(
                make_signal(
                    platform="web",
                    source_path="hackernews",
                    url=url,
                    external_id=object_id,
                    title=row.get("title") or "",
                    text=row.get("title") or "",
                    author={"handle": row.get("author"), "verified": False},
                    published_at=row.get("created_at"),
                    metrics={"score": row.get("points", 0), "num_comments": row.get("num_comments", 0)},
                    provenance={"query": query},
                )
            )
        _health("hackernews", ok=True, count=len(output))
        return output
    except Exception as exc:
        _health("hackernews", ok=False, error=str(exc))
        print(f"  [hackernews] ERR {exc}")
        return []


def collect_metaculus(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    url = str(cfg.get("url") or "")
    if not url:
        return []
    try:
        content = _jina_read(url, 10000)
        output = []
        pattern = r"####\s*\[([^\]]{15,})\]\((https://www\.metaculus\.com/questions/\d+/[^\)#]+)\)"
        for match in re.finditer(pattern, content):
            output.append(
                make_signal(
                    platform="web",
                    source_path="prediction_bank",
                    url=match.group(2).strip(),
                    text=match.group(1).strip(),
                    title=match.group(1).strip(),
                    provenance={"index": url},
                    evidence_role="primary_report",
                    signal_group="conditional_decision",
                )
            )
        _health("prediction_bank", ok=True, count=len(output))
        return output
    except Exception as exc:
        _health("prediction_bank", ok=False, error=str(exc))
        print(f"  [prediction_bank] ERR {exc}")
        return []


def collect_challenges(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    generic_titles = {
        "report a website issue", "directory of u.s. government agencies and departments",
        "the u.s. and its government", "government benefits", "explore all topics and services",
        "partner with usagov", "branches of government", "feature articles", "website usage data",
        "news + insights", "reports and publications", "join the movement", "board of directors",
        "meet the community", "all focus areas", "current challenges", "currently fundraising for",
    }

    def is_detail(name: str, title: str, link: str) -> bool:
        lowered_title = title.lower()
        parsed = urllib.parse.urlparse(link)
        path = parsed.path.rstrip("/")
        if lowered_title in generic_titles or not path:
            return False
        if name == "USAgov":
            return path.startswith("/challenges/") and path not in {"/challenges"}
        if name == "XPRIZE":
            return path.startswith("/competitions/") and path not in {"/competitions"}
        if name == "ARPA-H":
            return "/research-and-funding/" in path and path != "/research-and-funding"
        return False

    for name, url in (cfg.get("pages") or {}).items():
        try:
            content = _jina_read(str(url), 10000)
            for match in re.finditer(r"\[([^\]]{15,})\]\((https?://[^\)]+)\)", content):
                title, link = match.group(1).strip(), match.group(2).strip()
                if link.lower().endswith((".svg", ".png", ".jpg", ".jpeg")):
                    continue
                if not is_detail(str(name), title, link):
                    continue
                output.append(
                    make_signal(
                        platform="web",
                        source_path="official_challenge",
                        url=link,
                        title=title,
                        text=f"[{name}] {title}",
                        provenance={"index": url, "program": name},
                        evidence_role="official_record",
                        signal_group="decision_window",
                    )
                )
            _health(f"challenge_{str(name).lower()}", ok=True)
        except Exception as exc:
            _health(f"challenge_{str(name).lower()}", ok=False, error=str(exc))
            print(f"  [challenge:{name}] ERR {exc}")
        time.sleep(0.8)
    return output


def merge_signals(signals: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    merged: dict[str, dict[str, Any]] = {}
    for signal in signals:
        key = signal["candidate_id"]
        if key not in merged:
            merged[key] = signal
            continue
        current = merged[key]
        current["discovery_paths"] = sorted(set(current["discovery_paths"] + signal["discovery_paths"]))
        current["action_hints"] = sorted(set(current["action_hints"] + signal["action_hints"]))
        current["provenance"].extend(item for item in signal["provenance"] if item not in current["provenance"])
        if len(signal.get("text") or "") > len(current.get("text") or ""):
            current["text"] = signal["text"]
            current["title"] = signal.get("title") or current.get("title")
        if int(signal.get("engagement") or 0) > int(current.get("engagement") or 0):
            current["engagement"] = signal["engagement"]
            current["metrics"] = signal["metrics"]
        current["authority"]["verified"] = current["authority"].get("verified") or signal["authority"].get("verified")
        current["authority"]["known_watchlist"] = current["authority"].get("known_watchlist") or signal["authority"].get("known_watchlist")
    return list(merged.values()), len(signals) - len(merged)


_EVENT_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "by", "for", "from",
    "has", "have", "in", "into", "is", "it", "its", "new", "of", "on", "or",
    "says", "that", "the", "their", "this", "to", "was", "were", "will", "with",
}


def _event_tokens(signal: dict[str, Any]) -> set[str]:
    basis = str(signal.get("title") or "").strip()
    if not basis:
        basis = str(signal.get("text") or "").splitlines()[0][:260]
    return {
        token
        for token in re.findall(r"[a-z0-9][a-z0-9_-]+", basis.lower())
        if token not in _EVENT_STOPWORDS and len(token) > 2
    }


def _same_event(left: dict[str, Any], right: dict[str, Any]) -> bool:
    if left.get("canonical_url") and left.get("canonical_url") == right.get("canonical_url"):
        return True
    left_tokens, right_tokens = _event_tokens(left), _event_tokens(right)
    if min(len(left_tokens), len(right_tokens)) < 4:
        return False
    overlap = len(left_tokens & right_tokens) / len(left_tokens | right_tokens)
    left_time = _parse_datetime(left.get("published_at"))
    right_time = _parse_datetime(right.get("published_at"))
    close_in_time = not left_time or not right_time or abs((left_time - right_time).total_seconds()) <= 7 * 86400
    return close_in_time and overlap >= 0.55


def build_canonical_events(signals: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Cluster source mentions before editorial judging.

    This intentionally uses a conservative deterministic threshold. The Judge
    sees one event with all mentions, not several posts pretending to be
    separate story ideas.
    """
    clusters: list[list[dict[str, Any]]] = []
    for signal in signals:
        target = next(
            (cluster for cluster in clusters if any(_same_event(signal, item) for item in cluster)),
            None,
        )
        if target is None:
            clusters.append([signal])
        else:
            target.append(signal)
    events: list[dict[str, Any]] = []
    for cluster in clusters:
        role_rank = {
            "official_record": 8,
            "institutional_update": 7,
            "primary_report": 6,
            "expert_primary_link": 5,
            "reputable_news_lead": 3,
            "community_case_lead": 2,
            "paper_abstract_only": 1,
            "anonymous_opinion": 0,
            "discovery": 0,
        }
        primary = max(
            cluster,
            key=lambda item: (
                role_rank.get(str(item.get("source_role") or "discovery"), 0),
                bool((item.get("authority") or {}).get("institution")),
                bool((item.get("authority") or {}).get("verified")),
                len(str(item.get("text") or "")),
                int(item.get("engagement") or 0),
            ),
        )
        signatures = sorted(
            " ".join(sorted(_event_tokens(item))) or item["candidate_id"] for item in cluster
        )
        dates = sorted(
            str(item.get("published_at") or "")[:10]
            for item in cluster
            if item.get("published_at")
        )
        event_basis = f"{signatures[0]}:{dates[0] if dates else 'undated'}"
        event_id = "evt_" + hashlib.sha256(event_basis.encode()).hexdigest()[:16]
        event = dict(primary)
        event["primary_mention_id"] = primary["candidate_id"]
        event["candidate_id"] = event_id
        event["canonical_event_id"] = event_id
        event["story_key"] = event_id.removeprefix("evt_")
        event["object_type"] = "canonical_event"
        event["event_summary"] = primary.get("title") or str(primary.get("text") or "")[:280]
        event["mention_count"] = len(cluster)
        event["mentions"] = [
            {
                "mention_id": item["candidate_id"],
                "platform": item.get("platform"),
                "url": item.get("url"),
                "title": item.get("title"),
                "author": item.get("author"),
                "published_at": item.get("published_at"),
                "source_role": item.get("source_role"),
            }
            for item in cluster
        ]
        event["discovery_paths"] = sorted(
            {path for item in cluster for path in item.get("discovery_paths") or []}
        )
        event["provenance"] = [
            provenance
            for item in cluster
            for provenance in item.get("provenance") or []
            if provenance
        ]
        events.append(event)
    return events


def _evergreen_state(connection: sqlite3.Connection) -> None:
    connection.execute(
        """CREATE TABLE IF NOT EXISTS evergreen_usage (
             entry_id TEXT NOT NULL,
             intent_id TEXT NOT NULL,
             discipline TEXT NOT NULL,
             cell_key TEXT NOT NULL,
             used_date TEXT NOT NULL,
             outcome TEXT NOT NULL DEFAULT 'queued'
           )"""
    )
    connection.execute(
        "CREATE INDEX IF NOT EXISTS evergreen_entry_idx ON evergreen_usage(entry_id, used_date)"
    )
    connection.commit()


def _days_before(reference: str, days: int) -> str:
    return (dt.date.fromisoformat(reference) - dt.timedelta(days=days)).isoformat()


def _evergreen_entry_text(entry_id: str, entry: dict[str, Any], intent_id: str) -> tuple[str, str]:
    """Build the title and body a judge reads. Never emits internal codenames."""
    if intent_id == "EI6_missing_control":
        title = f"{entry.get('reported_metric') or entry_id}：可能没有排除的替代解释"
        body = (
            f"报告出来的指标：{entry.get('reported_metric')}。"
            f"可能的替代解释：{entry.get('confound')}。"
            f"公开标准要求的对照或报告项：{entry.get('control_or_report_item')}。"
        )
        return title, body
    if intent_id == "EI8_verification_role_gap":
        title = str(entry.get("title") or entry_id)
        body = (
            f"这一环的检查者：{entry.get('checker')}。"
            f"它系统性漏掉的：{entry.get('systematic_gap')}。"
            f"已公开记录的发现：{entry.get('documented_finding')}。"
        )
        if entry.get("cost_borne_by"):
            body += f"错了之后代价落在：{entry.get('cost_borne_by')}。"
        return title, body
    title = str(entry.get("title") or entry.get("name") or entry_id)
    body = str(entry.get("summary") or entry.get("isomorphism") or "")
    return title, body


def collect_evergreen(cfg: dict[str, Any], now: dt.datetime) -> list[dict[str, Any]]:
    """Curated cells that do not depend on any external event.

    EI1-EI5 are event driven and go silent in a week with no news. These entries
    come from local libraries; a headline can supply timing but never the topic.
    """
    today = now.date().isoformat()
    per_run = int(cfg.get("per_run", 1))
    if per_run <= 0:
        return []
    entry_cooldown = int(cfg.get("entry_cooldown_days", 90))
    cell_cooldown = int(cfg.get("cell_cooldown_days", 30))
    discipline_cap = int(cfg.get("discipline_per_week", 2))

    connection = sqlite3.connect(STATE_DB)
    connection.row_factory = sqlite3.Row
    try:
        _evergreen_state(connection)
        recent_entries = {
            str(row["entry_id"])
            for row in connection.execute(
                "SELECT DISTINCT entry_id FROM evergreen_usage WHERE used_date >= ?",
                (_days_before(today, entry_cooldown),),
            )
        }
        recent_cells = {
            str(row["cell_key"])
            for row in connection.execute(
                "SELECT DISTINCT cell_key FROM evergreen_usage WHERE used_date >= ?",
                (_days_before(today, cell_cooldown),),
            )
        }
        week_disciplines = Counter(
            str(row["discipline"])
            for row in connection.execute(
                "SELECT discipline FROM evergreen_usage WHERE used_date >= ?",
                (_days_before(today, 7),),
            )
        )
    finally:
        connection.close()

    pool: list[dict[str, Any]] = []
    for intent_id, library_cfg in (cfg.get("libraries") or {}).items():
        path = REPO / str(library_cfg.get("path") or "")
        if not path.exists():
            _health(f"evergreen:{intent_id}", ok=False, error=f"library missing: {path.name}")
            continue
        library = load_yaml(path) or {}
        entries = library.get(str(library_cfg.get("collection") or "")) or {}
        if not isinstance(entries, dict):
            continue
        for entry_id, entry in entries.items():
            if not isinstance(entry, dict):
                continue
            discipline = str(entry.get("discipline") or "unspecified")
            # A library may declare its own cell granularity. Locking the whole
            # discipline wastes the axis grid: AX3 x AX1 is 72 cells, but keying on
            # discipline alone collapses that to 6.
            cell_key = f"{intent_id}:{entry.get('cell') or discipline}"
            if entry_id in recent_entries or cell_key in recent_cells:
                continue
            if week_disciplines.get(discipline, 0) >= discipline_cap:
                continue
            # No reachable anchor means the hard gate is unmet: the entry cannot be
            # written without inventing the source. Skip rather than emit a cell
            # whose evidence the judge has no way to check.
            anchor = entry.get("standard_source") or entry.get("evidence_anchor") or {}
            if not (anchor.get("locator") or anchor.get("url")):
                continue
            if str(entry.get("verification_status") or "") == "source_incomplete":
                continue
            pool.append(
                {
                    "entry_id": entry_id,
                    "entry": entry,
                    "intent_id": intent_id,
                    "library_cfg": library_cfg,
                    "discipline": discipline,
                    "cell_key": cell_key,
                }
            )
        _health(f"evergreen:{intent_id}", ok=True, count=len(entries))

    if not pool:
        return []
    # Deterministic rotation: the day's ordinal walks the pool so runs do not
    # re-pick the same head entry, and a same-day rerun stays reproducible.
    # Cooldowns only look at previous days, so the within-batch spread is enforced
    # here — otherwise one run happily emits two cells from the same grid square.
    start = now.date().toordinal() % len(pool)
    selected: list[dict[str, Any]] = []
    batch_cells: set[str] = set()
    batch_disciplines = Counter(week_disciplines)
    for offset in range(len(pool)):
        if len(selected) >= per_run:
            break
        item = pool[(start + offset) % len(pool)]
        if item["cell_key"] in batch_cells:
            continue
        if batch_disciplines.get(item["discipline"], 0) >= discipline_cap:
            continue
        selected.append(item)
        batch_cells.add(item["cell_key"])
        batch_disciplines[item["discipline"]] += 1

    output: list[dict[str, Any]] = []
    for item in selected:
        entry, library_cfg = item["entry"], item["library_cfg"]
        # An entry may override the library defaults: within one library the problem
        # shape and thesis can differ per entry (citation collapse is not the same
        # shape as a missing reviewer check), so the entry wins when it declares one.
        def pick(field: str, fallback: str = "") -> str:
            return str(entry.get(field) or library_cfg.get(field) or fallback)

        anchor = entry.get("standard_source") or entry.get("evidence_anchor") or {}
        locator = str(anchor.get("locator") or "")
        url = str(anchor.get("url") or "")
        if not url and locator.startswith("10."):
            url = f"https://doi.org/{locator.split('；')[0].split(';')[0].strip()}"
        title, body = _evergreen_entry_text(item["entry_id"], entry, item["intent_id"])
        candidate_id = "cell_" + hashlib.sha256(item["entry_id"].encode()).hexdigest()[:16]
        output.append(
            {
                "candidate_id": candidate_id,
                "external_id": "",
                "platform": "curated",
                "source": "curated",
                "source_role": "primary_report",
                "source_path": f"evergreen_{item['intent_id'].split('_')[0].lower()}",
                "discovery_paths": ["evergreen"],
                "source_mode": "curated",
                "url": url,
                "canonical_url": url,
                "title": title,
                "text": body,
                "content_hash": hashlib.sha256(item["entry_id"].encode()).hexdigest(),
                "story_key": candidate_id.removeprefix("cell_"),
                "lang": "zh",
                "published_at": None,
                "age_h": None,
                "author": {},
                "authority": {"verified": False, "known_watchlist": False, "institution": True},
                "metrics": {},
                "engagement": 0,
                "provenance": [{"library": str(library_cfg.get("path")), "entry_id": item["entry_id"]}],
                "context": {
                    "evidence_role": "primary_report",
                    "signal_group": "evergreen",
                    "lead_only": False,
                },
                "problem_shape_hints": [pick("problem_shape_id")],
                "thesis_hints": [pick("thesis_id")],
                "action_hints": ["original_post"],
                "primary_mention_id": candidate_id,
                "canonical_event_id": candidate_id,
                "object_type": "curated_cell",
                "event_summary": title,
                "mention_count": 1,
                "mentions": [],
                "curated": {
                    "entry_id": item["entry_id"],
                    "intent_id": item["intent_id"],
                    "discipline": item["discipline"],
                    "cell_key": item["cell_key"],
                    "anchor": anchor,
                    "anchor_type": entry.get("anchor_type") or "standard",
                    "measured_window": entry.get("measured_window"),
                    "verification_status": entry.get("verification_status"),
                    "numbers_to_verify": entry.get("numbers_to_verify") or [],
                    "operator_fit": entry.get("operator_fit") or [],
                    "suggested_axis": pick("axis"),
                    "decision": {
                        "decision": "keep_for_enrichment",
                        "editorial_intent_id": item["intent_id"],
                        "event_match_reason": (
                            "常青条目：选题来自本地母表，锚定一份已公开发表的标准或记录，"
                            "不依赖当日外部事件。"
                        ),
                        "problem_shape_id": pick("problem_shape_id"),
                        "thesis_id": pick("thesis_id"),
                        "likely_column": pick("likely_column", "C1"),
                        "primary_action": "original_post",
                        "signal_role": pick("signal_role", "real_case"),
                        "capability_backing": pick("capability_backing", "technical_report"),
                        "reason": "常青产线保底条目，绕过 Recall 事件匹配，仍需终审核实证据与写法。",
                        "questions_for_enrichment": list(entry.get("numbers_to_verify") or []),
                    },
                },
            }
        )
    return output


def collect(args: argparse.Namespace) -> dict[str, Any]:
    now = dt.datetime.now(dt.timezone.utc)
    sources = load_yaml(REPO / "config" / "sources.yml")
    watch = load_yaml(REPO / "config" / "watchlist.yml")
    enabled = lambda name: (sources.get(name) or {}).get("status") == "auto"
    signals: list[dict[str, Any]] = []

    if not args.no_x:
        try:
            print("collect: X identity preflight ...")
            identity = _run_safe_json([SAFE_SOCIAL, "x", "whoami", "--json"], "x_identity")
            username = str(((identity or {}).get("user") or {}).get("username") or "")
            if username.lower() != "kw90qk":
                _health("x_identity", ok=False, error=f"unexpected username: {username or 'missing'}")
                raise XSourceHalt("authentication_failed")
            if enabled("x_watchlist"):
                print("collect: X path 1/4 watchlist ...")
                signals.extend(collect_x_watchlist(watch, now, sources["x_watchlist"]))
            if enabled("x_keyword_search"):
                print("collect: X path 2/4 editorial-intent event queries (Top + Latest) ...")
                signals.extend(collect_x_search(now, sources["x_keyword_search"]))
            if enabled("x_interaction_pool"):
                print("collect: X interaction pool (KOL stance posts, C8 only) ...")
                signals.extend(collect_x_interaction_pool(watch, now, sources["x_interaction_pool"]))
            if enabled("x_conversation_graph"):
                print("collect: X path 3/4 conversation graph ...")
                signals.extend(collect_x_conversation(watch, now, sources["x_conversation_graph"]))
            if enabled("x_dynamic_watch"):
                print("collect: X path 4/4 dynamic watch ...")
                signals.extend(collect_x_dynamic(now, sources["x_dynamic_watch"], sources.get("state") or {}))
        except XSourceHalt as exc:
            _health("x_collection", ok=False, error=str(exc))
            print(f"collect: X halted ({exc}); continuing non-X sources")
    if enabled("reddit_discovery") and not args.no_reddit and not args.only_x:
        print("collect: Reddit Top + New ...")
        signals.extend(collect_reddit(now, sources["reddit_discovery"]))
    if enabled("science_rss") and not args.only_x:
        signals.extend(collect_rss(now, sources["science_rss"]))
    if enabled("signal_news") and not args.only_x:
        signals.extend(collect_google_news(now, sources["signal_news"]))
    if enabled("official_indexes") and not args.only_x:
        signals.extend(collect_official_indexes(sources["official_indexes"]))
    if enabled("official_feeds") and not args.only_x:
        signals.extend(collect_rss(now, sources["official_feeds"]))
    if enabled("prediction_banks") and not args.only_x:
        signals.extend(collect_metaculus(sources["prediction_banks"]))
    if enabled("official_challenges") and not args.only_x:
        signals.extend(collect_challenges(sources["official_challenges"]))
    if enabled("federal_register") and not args.only_x:
        print("collect: Federal Register (open windows + final rules) ...")
        signals.extend(collect_federal_register(now, sources["federal_register"]))
    if enabled("hackernews") and not args.only_x:
        signals.extend(collect_hackernews(now, sources["hackernews"]))

    if args.merge_base:
        with open(args.merge_base, encoding="utf-8") as handle:
            base = json.load(handle)
        current_health = dict(SOURCE_HEALTH)
        signals = [
            candidate for candidate in base.get("candidates") or []
            if candidate.get("platform") != "x"
        ] + signals
        SOURCE_HEALTH.clear()
        SOURCE_HEALTH.update(base.get("source_health") or {})
        SOURCE_HEALTH.update(current_health)

    mentions, dropped = merge_signals(signals)
    candidates = build_canonical_events(mentions)
    # Curated cells join after clustering: they are already one unit each and must
    # never be merged into an external event.
    evergreen: list[dict[str, Any]] = []
    if enabled("evergreen") and not args.only_x and not args.no_evergreen:
        print("collect: evergreen curated cells ...")
        evergreen = collect_evergreen(sources["evergreen"], now)
        print(f"  evergreen -> {len(evergreen)} curated cell(s)")
        candidates = candidates + evergreen
    platform_counts = Counter(item["platform"] for item in candidates)
    path_counts = Counter(path for item in candidates for path in item["discovery_paths"])
    payload = {
        "schema_version": 4,
        "object_type": "canonical_event_collection",
        "collected_at": now.isoformat(),
        "count": len(candidates),
        "deduped_in_run": dropped,
        "mentions_collected": len(mentions),
        "events_built": len(candidates) - len(evergreen),
        "evergreen_cells": len(evergreen),
        "platform_counts": dict(platform_counts),
        "path_counts": dict(path_counts),
        "source_health": SOURCE_HEALTH,
        "candidates": candidates,
    }
    if args.out:
        with open(args.out, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
        print(f"wrote {len(candidates)} candidates -> {args.out}")
    else:
        print(json.dumps(payload, ensure_ascii=False, indent=2)[:4000])
    return payload


def enrich(args: argparse.Namespace) -> dict[str, Any]:
    if not args.candidates or not args.out:
        raise SystemExit("--enrich requires --candidates and --out")
    with open(args.enrich, encoding="utf-8") as handle:
        queue = json.load(handle)
    with open(args.candidates, encoding="utf-8") as handle:
        source_payload = json.load(handle)
    selected = queue.get("candidate_ids") or []
    decisions = {row["candidate_id"]: row for row in queue.get("decisions") or []}
    candidate_map = {row["candidate_id"]: row for row in source_payload.get("candidates") or []}
    cfg = load_yaml(REPO / "config" / "sources.yml").get("enrichment") or {}
    max_chars = int(cfg.get("max_chars", 12000))
    enriched: list[dict[str, Any]] = []

    for candidate_id in selected:
        candidate = dict(candidate_map[candidate_id])
        method, content, status, error = "none", "", "unavailable", None
        try:
            if candidate.get("object_type") == "curated_cell":
                content, method, issue = verify_curated_anchor(candidate)
                if issue:
                    error = issue
            elif candidate["platform"] == "x" and candidate.get("external_id"):
                method = "safe-social x tweet"
                data = _run_safe_json(
                    [SAFE_SOCIAL, "x", "tweet", candidate["external_id"], "--json"],
                    "enrich_x",
                    timeout=120,
                )
                content = json.dumps(data, ensure_ascii=False)[:max_chars]
            elif candidate["platform"] == "reddit" and candidate.get("external_id"):
                method = "safe-social reddit read"
                data = _run_safe_json(
                    [SAFE_SOCIAL, "reddit", "read", candidate["external_id"], "--json"],
                    "enrich_reddit",
                    timeout=120,
                )
                content = json.dumps(data, ensure_ascii=False)[:max_chars]
            elif candidate.get("url"):
                content, method, issue = _read_web(candidate["url"], max_chars)
                if issue:
                    error = issue
            if error:
                status = "blocked"
            else:
                status = "ok" if content else "empty"
        except Exception as exc:
            error = str(exc)[:500]
            status = "error"
        candidate["recall_decision"] = decisions.get(candidate_id)
        candidate["enrichment"] = {
            "status": status,
            "method": method,
            "content": content,
            "error": error,
        }
        enriched.append(candidate)

    payload = {
        "schema_version": 4,
        "run_id": queue.get("run_id"),
        "enriched_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "count": len(enriched),
        "source_health": SOURCE_HEALTH,
        "source_audit": queue.get("source_audit") or {},
        "candidates": enriched,
    }
    with open(args.out, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    print(f"enriched {len(enriched)} candidates -> {args.out}")
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out")
    parser.add_argument("--no-x", action="store_true")
    parser.add_argument("--no-reddit", action="store_true")
    parser.add_argument("--no-evergreen", action="store_true")
    parser.add_argument("--only-x", action="store_true")
    parser.add_argument("--merge-base")
    parser.add_argument("--enrich", metavar="QUEUE_JSON")
    parser.add_argument("--candidates", metavar="CANDIDATES_JSON")
    args = parser.parse_args()
    if args.only_x and args.no_x:
        parser.error("--only-x cannot be combined with --no-x")
    if args.enrich:
        enrich(args)
    else:
        collect(args)


if __name__ == "__main__":
    main()
