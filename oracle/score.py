#!/usr/bin/env python3
"""State, validation and rendering for the two-stage editorial pipeline.

The model is allowed to make editorial judgments. This module is deliberately
boring: it applies hard exclusions, keeps cross-day state, validates that every
candidate received exactly one decision, and renders the validated result.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any


HERE = Path(__file__).resolve().parent
REPO = HERE.parent
STATE_DB = Path(
    os.environ.get(
        "APODEX_CONTENT_STATE_DB",
        str(Path.home() / "Library" / "Application Support" / "Apodex Content Machine" / "oracle.sqlite3"),
    )
).expanduser()
RECALL_STATES = {"keep_for_enrichment", "interaction_only", "watch_only", "reject"}
EVIDENCE_STATES = {"keep", "interaction", "watch", "reject"}


def load_yaml(path: Path) -> dict[str, Any]:
    import yaml

    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def load_json(path: str | Path) -> dict[str, Any]:
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def load_json_loose(path: str | Path) -> dict[str, Any]:
    raw = Path(path).read_text(encoding="utf-8").strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\s*", "", raw)
        raw = re.sub(r"\s*```$", "", raw)
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            raise SystemExit(f"Judge output is not JSON: {path}")
        try:
            value = json.loads(raw[start : end + 1])
        except json.JSONDecodeError as exc:
            raise SystemExit(f"Judge output contains invalid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise SystemExit("Judge output must be one JSON object")
    return value


def state_connection() -> sqlite3.Connection:
    STATE_DB.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(STATE_DB)
    connection.row_factory = sqlite3.Row
    connection.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS candidates (
            candidate_id TEXT PRIMARY KEY,
            canonical_url TEXT,
            content_hash TEXT,
            story_key TEXT,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            last_platform TEXT,
            last_source TEXT,
            author_handle TEXT
        );
        CREATE INDEX IF NOT EXISTS candidates_url_idx ON candidates(canonical_url);
        CREATE INDEX IF NOT EXISTS candidates_hash_idx ON candidates(content_hash);
        CREATE TABLE IF NOT EXISTS occurrences (
            candidate_id TEXT NOT NULL,
            run_id TEXT NOT NULL,
            seen_date TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            PRIMARY KEY(candidate_id, run_id)
        );
        CREATE TABLE IF NOT EXISTS stories (
            story_key TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            last_candidate_id TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS accounts (
            platform TEXT NOT NULL,
            handle TEXT NOT NULL,
            first_seen TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            seen_days INTEGER NOT NULL DEFAULT 0,
            signal_count INTEGER NOT NULL DEFAULT 0,
            verified INTEGER NOT NULL DEFAULT 0,
            known_watchlist INTEGER NOT NULL DEFAULT 0,
            institution INTEGER NOT NULL DEFAULT 0,
            recall_keep_count INTEGER NOT NULL DEFAULT 0,
            evidence_keep_count INTEGER NOT NULL DEFAULT 0,
            reject_count INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'observed',
            PRIMARY KEY(platform, handle)
        );
        CREATE TABLE IF NOT EXISTS account_days (
            platform TEXT NOT NULL,
            handle TEXT NOT NULL,
            seen_date TEXT NOT NULL,
            PRIMARY KEY(platform, handle, seen_date)
        );
        CREATE TABLE IF NOT EXISTS outcomes (
            stage TEXT NOT NULL,
            run_id TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            decision TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY(stage, run_id, candidate_id)
        );
        """
    )
    return connection


def _days_ago(day: str, days: int) -> str:
    return (dt.date.fromisoformat(day) - dt.timedelta(days=days)).isoformat()


def _flatten_handles(value: Any) -> set[str]:
    if isinstance(value, dict):
        output: set[str] = set()
        for child in value.values():
            output.update(_flatten_handles(child))
        return output
    if isinstance(value, list):
        return {str(item).lstrip("@").lower() for item in value if isinstance(item, (str, int))}
    return set()


def competitor_handles() -> set[str]:
    cfg = load_yaml(REPO / "config" / "watchlist.yml")
    return _flatten_handles(cfg.get("competitors") or {})


def _account_handle(candidate: dict[str, Any]) -> str:
    return str((candidate.get("author") or {}).get("handle") or "").lstrip("@").lower()


def _account_status(row: sqlite3.Row, competitors: set[str]) -> str:
    if row["handle"] in competitors:
        return "blocked"
    if row["known_watchlist"]:
        return "seed"
    if row["evidence_keep_count"] >= 2 and row["seen_days"] >= 2:
        return "dynamic_watch"
    if row["seen_days"] >= 2 or (row["verified"] and row["recall_keep_count"] >= 1):
        return "candidate"
    return "observed"


def refresh_account_status(connection: sqlite3.Connection, platform: str, handle: str, competitors: set[str]) -> None:
    row = connection.execute(
        "SELECT * FROM accounts WHERE platform=? AND handle=?", (platform, handle)
    ).fetchone()
    if not row:
        return
    connection.execute(
        "UPDATE accounts SET status=? WHERE platform=? AND handle=?",
        (_account_status(row, competitors), platform, handle),
    )


def observe_candidate(
    connection: sqlite3.Connection,
    candidate: dict[str, Any],
    run_id: str,
    observed_at: str,
    seen_date: str,
    competitors: set[str],
) -> None:
    handle = _account_handle(candidate)
    platform = str(candidate.get("platform") or "web")
    occurrence = connection.execute(
        "INSERT OR IGNORE INTO occurrences(candidate_id, run_id, seen_date, observed_at) VALUES(?,?,?,?)",
        (candidate["candidate_id"], run_id, seen_date, observed_at),
    )
    connection.execute(
        """INSERT INTO candidates(candidate_id, canonical_url, content_hash, story_key, first_seen, last_seen,
                                  last_platform, last_source, author_handle)
           VALUES(?,?,?,?,?,?,?,?,?)
           ON CONFLICT(candidate_id) DO UPDATE SET
             canonical_url=excluded.canonical_url, content_hash=excluded.content_hash,
             story_key=excluded.story_key, last_seen=excluded.last_seen,
             last_platform=excluded.last_platform, last_source=excluded.last_source,
             author_handle=excluded.author_handle""",
        (
            candidate["candidate_id"], candidate.get("canonical_url"), candidate.get("content_hash"),
            candidate.get("story_key"), observed_at, observed_at, platform,
            candidate.get("source_path"), handle,
        ),
    )
    if candidate.get("story_key"):
        connection.execute(
            """INSERT INTO stories(story_key, first_seen, last_seen, last_candidate_id) VALUES(?,?,?,?)
               ON CONFLICT(story_key) DO UPDATE SET last_seen=excluded.last_seen,
                 last_candidate_id=excluded.last_candidate_id""",
            (candidate["story_key"], observed_at, observed_at, candidate["candidate_id"]),
        )
    if not handle or occurrence.rowcount == 0:
        return
    authority = candidate.get("authority") or {}
    connection.execute(
        """INSERT INTO accounts(platform, handle, first_seen, last_seen, verified, known_watchlist,
                                institution, signal_count)
           VALUES(?,?,?,?,?,?,?,1)
           ON CONFLICT(platform, handle) DO UPDATE SET
             last_seen=excluded.last_seen,
             verified=MAX(accounts.verified, excluded.verified),
             known_watchlist=MAX(accounts.known_watchlist, excluded.known_watchlist),
             institution=MAX(accounts.institution, excluded.institution),
             signal_count=accounts.signal_count+1""",
        (
            platform, handle, observed_at, observed_at, int(bool(authority.get("verified"))),
            int(bool(authority.get("known_watchlist"))), int(bool(authority.get("institution"))),
        ),
    )
    inserted_day = connection.execute(
        "INSERT OR IGNORE INTO account_days(platform, handle, seen_date) VALUES(?,?,?)",
        (platform, handle, seen_date),
    )
    if inserted_day.rowcount:
        connection.execute(
            "UPDATE accounts SET seen_days=seen_days+1 WHERE platform=? AND handle=?", (platform, handle)
        )
    refresh_account_status(connection, platform, handle, competitors)


def previous_duplicate(
    connection: sqlite3.Connection,
    candidate: dict[str, Any],
    seen_date: str,
    exact_days: int,
    story_days: int,
) -> str | None:
    exact_cutoff = _days_ago(seen_date, exact_days)
    prior = connection.execute(
        """SELECT candidate_id, last_seen FROM candidates
           WHERE (candidate_id=? OR (canonical_url<>'' AND canonical_url=?) OR content_hash=?)
             AND substr(last_seen,1,10) < ? AND substr(last_seen,1,10) >= ?
           ORDER BY last_seen DESC LIMIT 1""",
        (
            candidate["candidate_id"], candidate.get("canonical_url") or "",
            candidate.get("content_hash") or "", seen_date, exact_cutoff,
        ),
    ).fetchone()
    if prior:
        return f"cross_day_exact:{prior['candidate_id']}"
    if candidate.get("story_key"):
        story_cutoff = _days_ago(seen_date, story_days)
        prior_story = connection.execute(
            """SELECT last_candidate_id, last_seen FROM stories
               WHERE story_key=? AND substr(last_seen,1,10) < ? AND substr(last_seen,1,10) >= ?""",
            (candidate["story_key"], seen_date, story_cutoff),
        ).fetchone()
        if prior_story:
            return f"cross_day_story:{prior_story['last_candidate_id']}"
    return None


def hard_excluded(candidate: dict[str, Any], cfg: dict[str, Any]) -> str | None:
    text = " ".join(
        str(value or "") for value in (candidate.get("title"), candidate.get("text"))
    ).lower()
    lang = str(candidate.get("lang") or "").lower()
    for rule in cfg.get("exclude") or []:
        if str(rule.get("rule") or "").startswith("lang") and lang and lang != "en":
            return str(rule.get("name") or "language")
        if any(str(keyword).lower() in text for keyword in rule.get("keywords") or []):
            return str(rule.get("name") or "hard exclusion")
    return None


def keyword_hints(text: str, groups: dict[str, Any]) -> list[str]:
    lowered = text.lower()
    scored: list[tuple[int, str]] = []
    for key, definition in groups.items():
        hits = sum(1 for keyword in definition.get("keywords") or [] if str(keyword).lower() in lowered)
        if hits:
            scored.append((hits, str(key)))
    return [key for _, key in sorted(scored, reverse=True)]


def editorial_intent_hints(candidate: dict[str, Any], intents: dict[str, Any]) -> list[str]:
    text = " ".join(
        str(value or "") for value in (
            candidate.get("event_summary"), candidate.get("title"), candidate.get("text")
        )
    ).lower()
    explicit = {
        str(item.get("editorial_intent_id"))
        for item in candidate.get("provenance") or []
        if item.get("editorial_intent_id")
    }
    scored: list[tuple[int, str]] = []
    for intent_id, definition in (intents.get("intents") or {}).items():
        if definition.get("status") != "active":
            continue
        hits = sum(
            1 for trigger in definition.get("event_triggers") or []
            if str(trigger).lower() in text
        )
        if intent_id in explicit:
            hits += 2
        if hits:
            scored.append((hits, str(intent_id)))
    return [intent_id for _, intent_id in sorted(scored, reverse=True)]


def evidence_role(candidate: dict[str, Any], sources: dict[str, Any]) -> str:
    context = candidate.get("context") or {}
    explicit = str(context.get("evidence_role") or candidate.get("source_role") or "")
    if explicit and explicit != "discovery":
        return explicit
    path = str(candidate.get("source_path") or "")
    rules = sources.get("source_role_rules") or {}
    if path in rules:
        return str(rules[path])
    if path.startswith("official_"):
        return "official_record"
    authority = candidate.get("authority") or {}
    if candidate.get("platform") == "x" and (
        authority.get("institution")
        or authority.get("verified")
        or authority.get("known_watchlist")
    ):
        return "expert_primary_link"
    if candidate.get("platform") == "reddit":
        return "community_case_lead"
    return "anonymous_opinion"


def priority_score(candidate: dict[str, Any], sources: dict[str, Any]) -> float:
    role = evidence_role(candidate, sources)
    score = float(
        ((sources.get("recall") or {}).get("source_role_priority") or {}).get(role, 0)
    )
    authority = candidate.get("authority") or {}
    score += 2 if authority.get("known_watchlist") else 0
    score += 1 if authority.get("verified") else 0
    score += 2 if authority.get("institution") else 0
    score += min(2.0, math.log1p(max(0, int(candidate.get("engagement") or 0))) / 3)
    age = candidate.get("age_h")
    if isinstance(age, (int, float)):
        score += 2 if age <= 24 else 1 if age <= 96 else 0
    text = str(candidate.get("text") or "").lower()
    if re.search(
        r"\b(updated guidance|label change|conflicting evidence|no consensus|"
        r"call for evidence|public consultation|proposed rule|official definition|"
        r"eligibility criteria|revised estimate|updated assessment)\b",
        text,
    ):
        score += 3
    if (candidate.get("context") or {}).get("lead_only"):
        score -= 1
    if "?" in text and role not in {"paper_abstract_only", "anonymous_opinion"}:
        score += 1
    return round(score, 2)


def prefilter(args: argparse.Namespace) -> None:
    raw = load_json(args.raw)
    if raw.get("schema_version") != 4:
        raise SystemExit("Expected collect schema_version=4 canonical events")
    pillars = load_yaml(REPO / "config" / "pillars.yml")
    intents = load_yaml(REPO / "config" / "editorial_intents.yml")
    sources = load_yaml(REPO / "config" / "sources.yml")
    state_cfg = sources.get("state") or {}
    run_id = str(raw.get("run_id") or raw.get("collected_at") or dt.datetime.now(dt.timezone.utc).isoformat())
    observed_at = str(raw.get("collected_at") or dt.datetime.now(dt.timezone.utc).isoformat())
    seen_date = observed_at[:10]
    competitors = competitor_handles()
    survivors: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    connection = state_connection()
    try:
        for original in raw.get("candidates") or []:
            candidate = dict(original)
            if not candidate.get("candidate_id"):
                raise SystemExit("Every candidate must have candidate_id")
            duplicate = previous_duplicate(
                connection, candidate, seen_date,
                int(state_cfg.get("cross_day_dedupe_days", 45)),
                int(state_cfg.get("story_dedupe_days", 14)),
            )
            reason = hard_excluded(candidate, pillars) or duplicate
            text = " ".join(str(value or "") for value in (candidate.get("title"), candidate.get("text")))
            candidate["problem_shape_hints"] = keyword_hints(
                text, pillars.get("problem_shapes") or {}
            )
            candidate["thesis_hints"] = [
                thesis_id
                for thesis_id, definition in (pillars.get("theses") or {}).items()
                if set(candidate["problem_shape_hints"]).intersection(
                    definition.get("problem_shapes") or []
                )
            ]
            candidate["editorial_intent_hints"] = editorial_intent_hints(candidate, intents)
            candidate["evidence_role"] = evidence_role(candidate, sources)
            candidate["priority_score"] = priority_score(candidate, sources)
            observe_candidate(connection, candidate, run_id, observed_at, seen_date, competitors)
            if reason:
                candidate["prefilter_decision"] = "reject"
                candidate["prefilter_reason"] = reason
                excluded.append(candidate)
            else:
                candidate["prefilter_decision"] = "send_to_recall_judge"
                survivors.append(candidate)
        connection.commit()
    finally:
        connection.close()
    survivors.sort(key=lambda item: (-float(item.get("priority_score") or 0), item["candidate_id"]))
    payload = {
        "schema_version": 4,
        "object_type": "canonical_event_recall_input",
        "stage": "recall_input",
        "run_id": run_id,
        "collected_at": observed_at,
        "source_health": raw.get("source_health") or {},
        "counts": {"collected": len(raw.get("candidates") or []), "sent_to_recall": len(survivors), "excluded": len(excluded)},
        "candidates": survivors,
        "prefilter_excluded": excluded,
    }
    Path(args.json_out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    lines = [
        f"# Recall Judge input｜{seen_date}",
        "",
        f"> collected {payload['counts']['collected']} → hard/dedupe excluded {len(excluded)} → Recall Judge {len(survivors)}",
        "",
        "## Candidates",
        "",
    ]
    for item in survivors:
        label = item.get("title") or str(item.get("text") or "")[:120]
        lines.append(
            f"- `{item['candidate_id']}` · `{item.get('platform')}` · p{item['priority_score']} · "
            f"[{label}]({item.get('url')})"
        )
        lines.append(f"  - {str(item.get('text') or '')[:500].replace(chr(10), ' ')}")
    lines.extend(["", "## Deterministic exclusions", ""])
    for item in excluded:
        lines.append(f"- `{item['candidate_id']}` · {item['prefilter_reason']} · {item.get('url')}")
    Path(args.out).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"prefilter -> recall {len(survivors)} / excluded {len(excluded)}; state {STATE_DB}")


def decision_map(
    judged: dict[str, Any], expected_ids: set[str], allowed_states: set[str], stage: str
) -> dict[str, dict[str, Any]]:
    rows = judged.get("decisions")
    if not isinstance(rows, list):
        raise SystemExit(f"{stage} Judge JSON must contain decisions[]")
    output: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            raise SystemExit(f"{stage} decision rows must be objects")
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id in output:
            duplicates.append(candidate_id)
        output[candidate_id] = row
        if row.get("decision") not in allowed_states:
            raise SystemExit(f"{stage} invalid decision for {candidate_id}: {row.get('decision')}")
    actual_ids = set(output)
    missing, unknown = sorted(expected_ids - actual_ids), sorted(actual_ids - expected_ids)
    if duplicates or missing or unknown:
        raise SystemExit(
            f"{stage} candidate accounting failed; duplicate={duplicates}, missing={missing}, unknown={unknown}"
        )
    return output


def recall_chunks(args: argparse.Namespace) -> None:
    source = load_json(args.candidates)
    output_dir = Path(args.out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("input-*.json", "output-*.json"):
        for old in output_dir.glob(pattern):
            old.unlink()
    candidates = source.get("candidates") or []
    size = max(1, int(args.size))
    chunks = [candidates[index : index + size] for index in range(0, len(candidates), size)] or [[]]
    for index, rows in enumerate(chunks, start=1):
        payload = {
            "schema_version": 4,
            "stage": "recall_input_chunk",
            "run_id": source.get("run_id"),
            "chunk_index": index,
            "chunk_count": len(chunks),
            "candidates": rows,
        }
        path = output_dir / f"input-{index:03d}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"recall chunks -> {len(chunks)} x <= {size}")


def merge_recall(args: argparse.Namespace) -> None:
    decisions: list[dict[str, Any]] = []
    for path in args.judged:
        payload = load_json_loose(path)
        rows = payload.get("decisions")
        if not isinstance(rows, list):
            raise SystemExit(f"Recall chunk lacks decisions[]: {path}")
        decisions.extend(rows)
    merged = {"stage": "recall", "decisions": decisions}
    Path(args.out).write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"merged Recall decisions -> {len(decisions)}")


def allowed_actions(candidate: dict[str, Any], actions: Any) -> list[str]:
    platform = str(candidate.get("platform") or "web")
    pillars = load_yaml(REPO / "config" / "pillars.yml")
    valid: list[str] = []
    for action in actions if isinstance(actions, list) else []:
        definition = (pillars.get("actions") or {}).get(str(action))
        if definition and platform in (definition.get("platforms") or []):
            valid.append(str(action))
    return sorted(set(valid))


def allowed_primary_action(candidate: dict[str, Any], action: Any) -> str:
    valid = allowed_actions(candidate, [action])
    if len(valid) != 1:
        raise SystemExit(
            f"Judge returned invalid primary_action for {candidate['candidate_id']}: {action}"
        )
    return valid[0]


def validate_theme_match(
    row: dict[str, Any], pillars: dict[str, Any], candidate_id: str, column: str | None
) -> tuple[str, str]:
    problem_shape_id = str(row.get("problem_shape_id") or "")
    thesis_id = str(row.get("thesis_id") or "")
    shapes = pillars.get("problem_shapes") or {}
    theses = pillars.get("theses") or {}
    if problem_shape_id not in shapes:
        raise SystemExit(f"Invalid/missing problem_shape_id for {candidate_id}: {problem_shape_id}")
    if thesis_id not in theses:
        raise SystemExit(f"Invalid/missing thesis_id for {candidate_id}: {thesis_id}")
    thesis = theses[thesis_id]
    if problem_shape_id not in (thesis.get("problem_shapes") or []):
        raise SystemExit(
            f"Problem shape {problem_shape_id} is not backed by thesis {thesis_id}: {candidate_id}"
        )
    if column and column not in (thesis.get("allowed_columns") or []):
        raise SystemExit(f"Thesis {thesis_id} cannot route to {column}: {candidate_id}")
    return problem_shape_id, thesis_id


def validate_editorial_intent(
    row: dict[str, Any],
    candidate_id: str,
    problem_shape_id: str,
    thesis_id: str,
    column: str,
    backing: str,
) -> str:
    configured = load_yaml(REPO / "config" / "editorial_intents.yml")
    intent_id = str(row.get("editorial_intent_id") or "")
    intent = (configured.get("intents") or {}).get(intent_id)
    if not intent or intent.get("status") != "active":
        raise SystemExit(f"Invalid/missing editorial_intent_id for {candidate_id}: {intent_id}")
    if problem_shape_id not in (intent.get("problem_shapes") or []):
        raise SystemExit(f"Intent {intent_id} does not allow {problem_shape_id}: {candidate_id}")
    if thesis_id not in (intent.get("theses") or []):
        raise SystemExit(f"Intent {intent_id} does not allow {thesis_id}: {candidate_id}")
    if column not in (intent.get("columns") or []):
        raise SystemExit(f"Intent {intent_id} does not route to {column}: {candidate_id}")
    if backing not in (intent.get("product_backing") or []):
        raise SystemExit(f"Intent {intent_id} is not backed by {backing}: {candidate_id}")
    if not str(row.get("event_match_reason") or "").strip():
        raise SystemExit(f"Intent match requires event_match_reason: {candidate_id}")
    return intent_id


def record_outcomes(
    connection: sqlite3.Connection,
    stage: str,
    run_id: str,
    candidates: dict[str, dict[str, Any]],
    decisions: dict[str, dict[str, Any]],
) -> None:
    competitors = competitor_handles()
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for candidate_id, decision in decisions.items():
        inserted = connection.execute(
            "INSERT OR IGNORE INTO outcomes(stage, run_id, candidate_id, decision, created_at) VALUES(?,?,?,?,?)",
            (stage, run_id, candidate_id, decision["decision"], now),
        )
        if inserted.rowcount == 0:
            continue
        candidate = candidates[candidate_id]
        handle = _account_handle(candidate)
        platform = str(candidate.get("platform") or "web")
        if not handle:
            continue
        if stage == "recall" and decision["decision"] in {"keep_for_enrichment", "interaction_only"}:
            connection.execute(
                "UPDATE accounts SET recall_keep_count=recall_keep_count+1 WHERE platform=? AND handle=?",
                (platform, handle),
            )
        elif stage == "evidence" and decision["decision"] in {"keep", "interaction"}:
            connection.execute(
                "UPDATE accounts SET evidence_keep_count=evidence_keep_count+1 WHERE platform=? AND handle=?",
                (platform, handle),
            )
        elif decision["decision"] == "reject":
            connection.execute(
                "UPDATE accounts SET reject_count=reject_count+1 WHERE platform=? AND handle=?",
                (platform, handle),
            )
        refresh_account_status(connection, platform, handle, competitors)


def validate_recall(args: argparse.Namespace) -> None:
    source = load_json(args.candidates)
    judged = load_json_loose(args.judged)
    candidates = {item["candidate_id"]: item for item in source.get("candidates") or []}
    decisions = decision_map(judged, set(candidates), RECALL_STATES, "Recall")
    pillars = load_yaml(REPO / "config" / "pillars.yml")
    valid_columns = {
        column_id
        for column_id, definition in (pillars.get("columns") or {}).items()
        if definition.get("scrapable")
    }
    normalized: list[dict[str, Any]] = []
    for candidate_id, row in decisions.items():
        candidate = candidates[candidate_id]
        cleaned = dict(row)
        cleaned["candidate_id"] = candidate_id
        primary_action = row.get("primary_action")
        if cleaned["decision"] in {"keep_for_enrichment", "interaction_only"}:
            cleaned["primary_action"] = allowed_primary_action(candidate, primary_action)
        else:
            cleaned["primary_action"] = "watch"
        if cleaned["decision"] == "interaction_only" and cleaned["primary_action"] not in {
            "x_reply", "x_quote", "reddit_reply"
        }:
            raise SystemExit(
                f"Recall interaction_only requires one platform-valid interaction action: {candidate_id}"
            )
        if cleaned["decision"] == "keep_for_enrichment":
            if cleaned["primary_action"] != "original_post":
                raise SystemExit(f"Recall original candidate must choose original_post: {candidate_id}")
            likely_column = str(row.get("likely_column") or "")
            if likely_column not in valid_columns:
                raise SystemExit(f"Recall invalid likely_column for {candidate_id}: {likely_column}")
            problem_shape_id, thesis_id = validate_theme_match(
                row, pillars, candidate_id, likely_column
            )
            cleaned["likely_column"] = likely_column
            cleaned["problem_shape_id"] = problem_shape_id
            cleaned["thesis_id"] = thesis_id
            backing = str(row.get("capability_backing") or "")
            if backing not in set(pillars.get("capability_backing") or []):
                raise SystemExit(f"Recall missing capability_backing for {candidate_id}")
            cleaned["capability_backing"] = backing
            cleaned["editorial_intent_id"] = validate_editorial_intent(
                row, candidate_id, problem_shape_id, thesis_id, likely_column, backing
            )
            cleaned["event_match_reason"] = str(row.get("event_match_reason") or "")
        normalized.append(cleaned)
    normalized.sort(key=lambda row: (-float(candidates[row["candidate_id"]].get("priority_score") or 0), row["candidate_id"]))
    sources_cfg = load_yaml(REPO / "config" / "sources.yml")
    recall_cfg = sources_cfg.get("recall") or {}
    maximum = int(recall_cfg.get("max_enrichment_candidates", 32))
    eligible = [row for row in normalized if row["decision"] in {"keep_for_enrichment", "interaction_only"}]

    def source_bucket(candidate: dict[str, Any]) -> str:
        platform = str(candidate.get("platform") or "")
        path = str(candidate.get("source_path") or "")
        role = evidence_role(candidate, sources_cfg)
        if platform == "x":
            return "x"
        if platform == "reddit":
            return "community"
        if role in {"official_record", "institutional_update"} and path not in {
            "official_challenge"
        }:
            return "official_update"
        if path.startswith("rss_") or path == "google_news_signal":
            return "rss_news"
        if path in {"official_challenge", "prediction_bank"}:
            return "decision_window"
        if path == "hackernews":
            return "community"
        return "rss_news"

    budgets = {str(key): int(value) for key, value in (recall_cfg.get("enrichment_budgets") or {}).items()}
    selected: list[dict[str, Any]] = []
    selected_set: set[str] = set()
    for bucket, budget in budgets.items():
        matches = [row for row in eligible if source_bucket(candidates[row["candidate_id"]]) == bucket]
        for row in matches[: max(0, budget)]:
            selected.append(row)
            selected_set.add(row["candidate_id"])
    for row in eligible:
        if len(selected) >= maximum:
            break
        if row["candidate_id"] not in selected_set:
            selected.append(row)
            selected_set.add(row["candidate_id"])
    selected = selected[:maximum]
    selected_ids = [row["candidate_id"] for row in selected]
    selected_set = set(selected_ids)
    for row in normalized:
        row["queued_for_enrichment"] = row["candidate_id"] in selected_set
    run_id = str(source.get("run_id") or "unknown")
    clean = {
        "schema_version": 4,
        "stage": "recall_decisions",
        "run_id": run_id,
        "counts": {
            "input": len(candidates), "eligible": len(eligible), "queued": len(selected),
            "watch": sum(row["decision"] == "watch_only" for row in normalized),
            "rejected": sum(row["decision"] == "reject" for row in normalized),
        },
        "decisions": normalized,
    }
    queue = {
        "schema_version": 4,
        "stage": "enrichment_queue",
        "run_id": run_id,
        "candidate_ids": selected_ids,
        "decisions": selected,
        "source_audit": {
            bucket: {
                "eligible": sum(source_bucket(candidates[row["candidate_id"]]) == bucket for row in eligible),
                "queued": sum(source_bucket(candidates[row["candidate_id"]]) == bucket for row in selected),
            }
            for bucket in sorted(set(budgets) | {"other"})
        },
    }
    Path(args.out).write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.queue_out).write_text(json.dumps(queue, ensure_ascii=False, indent=2), encoding="utf-8")
    connection = state_connection()
    try:
        record_outcomes(connection, "recall", run_id, candidates, decisions)
        connection.commit()
    finally:
        connection.close()
    print(f"recall validated -> queued {len(selected)} / {len(candidates)}")


def normalize_evidence_decision(
    candidate: dict[str, Any], row: dict[str, Any], pillars: dict[str, Any]
) -> dict[str, Any]:
    cleaned = dict(row)
    cleaned["candidate_id"] = candidate["candidate_id"]
    valid_columns = {
        column_id
        for column_id, definition in (pillars.get("columns") or {}).items()
        if definition.get("scrapable")
    }
    primary_destination = str(row.get("primary_destination") or "")
    primary_action = row.get("primary_action")
    if cleaned["decision"] == "keep":
        if primary_destination not in valid_columns:
            raise SystemExit(
                f"Evidence keep requires one scrapable primary_destination: {candidate['candidate_id']}"
            )
        cleaned["primary_action"] = allowed_primary_action(candidate, primary_action)
        if cleaned["primary_action"] != "original_post":
            raise SystemExit(
                f"Evidence keep must choose original_post only: {candidate['candidate_id']}"
            )
        problem_shape_id, thesis_id = validate_theme_match(
            row, pillars, candidate["candidate_id"], primary_destination
        )
        cleaned["problem_shape_id"] = problem_shape_id
        cleaned["thesis_id"] = thesis_id
        backing = str(row.get("capability_backing") or "")
        if backing not in set(pillars.get("capability_backing") or []):
            raise SystemExit(
                f"Evidence keep requires capability_backing: {candidate['candidate_id']}"
            )
        cleaned["capability_backing"] = backing
        cleaned["editorial_intent_id"] = validate_editorial_intent(
            row, candidate["candidate_id"], problem_shape_id, thesis_id,
            primary_destination, backing,
        )
        cleaned["event_match_reason"] = str(row.get("event_match_reason") or "")
        signal_role = str(row.get("signal_role") or "")
        if signal_role not in (pillars.get("signal_roles") or {}):
            raise SystemExit(f"Evidence keep requires valid signal_role: {candidate['candidate_id']}")
        if primary_destination == "C3" and signal_role != "decision_window":
            raise SystemExit(
                f"C3 requires signal_role=decision_window: {candidate['candidate_id']}"
            )
        cleaned["signal_role"] = signal_role
    elif cleaned["decision"] == "interaction":
        cleaned["primary_destination"] = "C8"
        cleaned["primary_action"] = allowed_primary_action(candidate, primary_action)
        if cleaned["primary_action"] not in {"x_reply", "x_quote", "reddit_reply"}:
            raise SystemExit(
                f"Evidence interaction requires one interaction action: {candidate['candidate_id']}"
            )
    else:
        cleaned["primary_destination"] = "watch"
        cleaned["primary_action"] = "watch"
    if cleaned["decision"] == "keep":
        cleaned["primary_destination"] = primary_destination
    for field in (
        "source_says", "why_now", "why_apodex", "possible_angle", "inference_boundary",
        "needs_verification", "reason", "secondary_note",
    ):
        cleaned[field] = row.get(field) if row.get(field) not in (None, "") else "—"
    cleaned["maturity"] = "Idea"
    return cleaned


def _cell(value: Any, limit: int = 420) -> str:
    if isinstance(value, list):
        value = "; ".join(str(item) for item in value)
    return str(value or "—").replace("|", "\\|").replace("\n", " ")[:limit]


def _source(candidate: dict[str, Any]) -> str:
    label = candidate.get("title") or str(candidate.get("text") or "")[:90] or candidate["candidate_id"]
    handle = _account_handle(candidate)
    prefix = f"@{handle}: " if handle else ""
    return f"[{_cell(prefix + str(label), 120)}]({candidate.get('url')})"


def render_report(
    source: dict[str, Any], candidates: dict[str, dict[str, Any]], rows: list[dict[str, Any]]
) -> str:
    pillars = load_yaml(REPO / "config" / "pillars.yml")
    columns = pillars.get("columns") or {}
    day = str(source.get("enriched_at") or dt.date.today().isoformat())[:10]
    try:
        date_label = f"{int(day[5:7])}月{int(day[8:10])}日"
    except (ValueError, IndexError):
        date_label = day
    lines = [f"# {date_label}", ""]
    seen_sources: dict[str, str] = {}
    for row in rows:
        if row["decision"] not in {"keep", "interaction"}:
            continue
        candidate = candidates[row["candidate_id"]]
        source_key = str(candidate.get("canonical_event_id") or candidate["candidate_id"])
        previous = seen_sources.get(source_key)
        if previous:
            raise SystemExit(
                f"One canonical event may appear only once in the report: {source_key} "
                f"({previous}, {row['candidate_id']})"
            )
        seen_sources[source_key] = row["candidate_id"]
    original_rows = [
        row for row in rows
        if row["decision"] == "keep" and row["primary_action"] == "original_post"
    ]
    for column_id, definition in columns.items():
        grouped = [row for row in original_rows if row["primary_destination"] == column_id]
        if not grouped:
            continue
        lines.extend(
            [
                f"## {definition['name']}", "",
                "| 来源 | 来源明确说了什么 | 为什么现在值得看 | 为什么适合 Apodex | 可写角度 | 推断边界 | 发布前需核验 | 状态 |",
                "|---|---|---|---|---|---|---|---|",
            ]
        )
        for row in grouped:
            candidate = candidates[row["candidate_id"]]
            lines.append(
                "| " + " | ".join(
                    [
                        _source(candidate), _cell(row["source_says"]), _cell(row["why_now"]),
                        _cell(row["why_apodex"]), _cell(row["possible_angle"]),
                        _cell(row["inference_boundary"]), _cell(row["needs_verification"]), "Idea",
                    ]
                ) + " |"
            )
        lines.append("")
    for platform, title, valid_actions in (
        ("x", "X 互动池", {"x_reply", "x_quote"}),
        ("reddit", "Reddit 互动池", {"reddit_reply"}),
    ):
        grouped = [
            row for row in rows
            if candidates[row["candidate_id"]].get("platform") == platform
            and row["decision"] == "interaction"
            and row["primary_action"] in valid_actions
        ]
        if not grouped:
            continue
        lines.extend([f"## {title}", "", "| 来源 | 动作 | 可互动方向 | Apodex 观点 | 边界 | 状态 |", "|---|---|---|---|---|---|"])
        for row in grouped:
            candidate = candidates[row["candidate_id"]]
            lines.append(
                "| " + " | ".join(
                    [
                        _source(candidate), _cell(row["primary_action"]), _cell(row["possible_angle"]),
                        _cell(row["why_apodex"]), _cell(row["inference_boundary"]), "Idea",
                    ]
                ) + " |"
            )
        lines.append("")
    watch = [row for row in rows if row["decision"] == "watch"]
    rejected = [row for row in rows if row["decision"] == "reject"]
    if watch:
        lines.extend(["## 观察", "", "| 来源 | 原因 |", "|---|---|"])
        for row in watch:
            lines.append(f"| {_source(candidates[row['candidate_id']])} | {_cell(row['reason'])} |")
        lines.append("")
    audit = source.get("source_audit") or {}
    if audit:
        labels = {
            "x": "X",
            "community": "Community leads",
            "official_update": "Official updates",
            "rss_news": "RSS / News",
            "decision_window": "Decision windows",
        }
        lines.extend(["## 来源覆盖", "", "| 来源组 | Recall 可 enrichment | 实际进入终审 |", "|---|---:|---:|"])
        for bucket, counts in audit.items():
            lines.append(f"| {labels.get(bucket, bucket)} | {counts.get('eligible', 0)} | {counts.get('queued', 0)} |")
        lines.append("")
    lines.extend(["## 终审淘汰", "", "| 来源 | 原因 |", "|---|---|"])
    for row in rejected:
        lines.append(f"| {_source(candidates[row['candidate_id']])} | {_cell(row['reason'])} |")
    lines.append("")
    return "\n".join(lines)


def render(args: argparse.Namespace) -> None:
    source = load_json(args.candidates)
    judged = load_json_loose(args.judged)
    candidates = {item["candidate_id"]: item for item in source.get("candidates") or []}
    decisions = decision_map(judged, set(candidates), EVIDENCE_STATES, "Evidence")
    pillars = load_yaml(REPO / "config" / "pillars.yml")
    normalized = [
        normalize_evidence_decision(candidates[candidate_id], row, pillars)
        for candidate_id, row in decisions.items()
    ]
    normalized.sort(key=lambda row: (-float(candidates[row["candidate_id"]].get("priority_score") or 0), row["candidate_id"]))
    run_id = str(source.get("run_id") or "unknown")
    clean = {
        "schema_version": 4,
        "stage": "evidence_decisions",
        "run_id": run_id,
        "counts": {state: sum(row["decision"] == state for row in normalized) for state in EVIDENCE_STATES},
        "decisions": normalized,
    }
    Path(args.clean_out).write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(args.out).write_text(render_report(source, candidates, normalized), encoding="utf-8")
    connection = state_connection()
    try:
        record_outcomes(connection, "evidence", run_id, candidates, decisions)
        connection.commit()
    finally:
        connection.close()
    print(f"evidence validated -> report {args.out}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    first = sub.add_parser("prefilter")
    first.add_argument("raw")
    first.add_argument("--out", required=True)
    first.add_argument("--json-out", required=True)
    chunker = sub.add_parser("recall-chunks")
    chunker.add_argument("--candidates", required=True)
    chunker.add_argument("--out-dir", required=True)
    chunker.add_argument("--size", type=int, default=60)
    merger = sub.add_parser("merge-recall")
    merger.add_argument("judged", nargs="+")
    merger.add_argument("--out", required=True)
    recall = sub.add_parser("validate-recall")
    recall.add_argument("judged")
    recall.add_argument("--candidates", required=True)
    recall.add_argument("--out", required=True)
    recall.add_argument("--queue-out", required=True)
    evidence = sub.add_parser("render")
    evidence.add_argument("judged")
    evidence.add_argument("--candidates", required=True)
    evidence.add_argument("--out", required=True)
    evidence.add_argument("--clean-out", required=True)
    return parser


def main() -> None:
    argv = sys.argv[1:]
    commands = {"prefilter", "recall-chunks", "merge-recall", "validate-recall", "render"}
    if argv and argv[0] not in commands and not argv[0].startswith("-"):
        argv.insert(0, "prefilter")
    args = build_parser().parse_args(argv)
    if args.command == "prefilter":
        prefilter(args)
    elif args.command == "recall-chunks":
        recall_chunks(args)
    elif args.command == "merge-recall":
        merge_recall(args)
    elif args.command == "validate-recall":
        validate_recall(args)
    else:
        render(args)


if __name__ == "__main__":
    main()
