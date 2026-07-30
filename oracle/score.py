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
        CREATE TABLE IF NOT EXISTS report_forms (
            report_date TEXT NOT NULL,
            candidate_id TEXT NOT NULL,
            form TEXT NOT NULL,
            axis TEXT NOT NULL DEFAULT '',
            PRIMARY KEY(report_date, candidate_id)
        );
        CREATE INDEX IF NOT EXISTS report_forms_date_idx ON report_forms(report_date);
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
    scored: list[tuple[int, str]] = []
    for intent_id, definition in (intents.get("intents") or {}).items():
        if definition.get("status") != "active":
            continue
        hits = sum(
            1 for trigger in definition.get("event_triggers") or []
            if str(trigger).lower() in text
        )
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
            # Curated cells carry their own cooldown state (evergreen_usage) and are
            # not external signals, so neither the topic gate nor cross-day dedupe applies.
            if candidate.get("object_type") == "curated_cell":
                reason = None
            else:
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
            if candidate.get("object_type") != "curated_cell":
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


def decision_accounting(
    judged: dict[str, Any], expected_ids: set[str], allowed_states: set[str], stage: str
) -> tuple[dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Index judge rows by candidate id and report what is wrong, without raising.

    Returns (usable_decisions, problems). Rows whose state is not a legal decision are
    left out of the usable set — keeping them would let a later gate treat garbage as
    truth. Everything a repair pass could fix lands in `problems`.
    """
    rows = judged.get("decisions")
    if not isinstance(rows, list):
        raise SystemExit(f"{stage} Judge JSON must contain decisions[]")
    output: dict[str, dict[str, Any]] = {}
    duplicates: list[str] = []
    bad_state: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        candidate_id = str(row.get("candidate_id") or "")
        if candidate_id in output:
            duplicates.append(candidate_id)
        if row.get("decision") not in allowed_states:
            bad_state.append(candidate_id)
            continue
        output[candidate_id] = row
    actual = set(output)
    return output, {
        "missing": sorted(expected_ids - actual),
        "unknown": sorted(actual - expected_ids),
        "duplicate": sorted(set(duplicates)),
        "bad_state": sorted(set(bad_state) - actual),
    }


def decision_map(
    judged: dict[str, Any], expected_ids: set[str], allowed_states: set[str], stage: str
) -> dict[str, dict[str, Any]]:
    """Strict indexing: the final authority, fails closed on any accounting problem."""
    output, problems = decision_accounting(judged, expected_ids, allowed_states, stage)
    for candidate_id in problems["bad_state"]:
        row = next(
            (r for r in judged["decisions"] if str(r.get("candidate_id")) == candidate_id), {}
        )
        raise SystemExit(f"{stage} invalid decision for {candidate_id}: {row.get('decision')}")
    if problems["duplicate"] or problems["missing"] or problems["unknown"]:
        raise SystemExit(
            f"{stage} candidate accounting failed; duplicate={problems['duplicate']}, "
            f"missing={problems['missing']}, unknown={problems['unknown']}"
        )
    return output


def recall_chunks(args: argparse.Namespace) -> None:
    source = load_json(args.candidates)
    output_dir = Path(args.out_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for pattern in ("input-*.json", "output-*.json"):
        for old in output_dir.glob(pattern):
            old.unlink()
    # Curated cells skip Stage 1: their intent, shape and thesis come from the
    # library, not from matching an external event. They still face Stage 2.
    candidates = [
        item for item in (source.get("candidates") or [])
        if item.get("object_type") != "curated_cell"
    ]
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


WRITABLE_OPERATOR_STATES = {"active", "always_on"}

# 内部代号绝不允许出现在给人读的散文字段里。它们是管线的坐标，不是文案。
# 一旦泄进 draft，写作就会退化成按代号填空。
INTERNAL_CODENAME_RE = re.compile(
    r"\b(?:"
    r"(?:PS|EI|OP|AX|CF|RQ|T)\d*_[A-Za-z_]+"   # PS10_missing_control / T9_cross_domain_synthesis
    r"|(?:PS|EI|OP|AX)\d+"                      # 裸 PS10 / EI6 / OP8 / AX3
    r"|E[12]"                                   # 引擎代号 E1 / E2
    r")\b"
)

# 这些散文字段最终会被人抄进文案，必须干净。
PROSE_FIELDS = (
    "source_says", "why_now", "why_apodex", "possible_angle",
    "inference_boundary", "secondary_note",
    # 互动这两条会被直接抄进 quote 正文，泄了代号就是发出去
    "stance_read", "verification_hook",
)


def assert_no_internal_codenames(row: dict[str, Any], candidate_id: str) -> None:
    for field in PROSE_FIELDS:
        value = row.get(field)
        if isinstance(value, list):
            value = " ".join(str(item) for item in value)
        found = INTERNAL_CODENAME_RE.findall(str(value or ""))
        if found:
            raise SystemExit(
                f"Internal codename leaked into prose field '{field}' for {candidate_id}: "
                f"{sorted(set(found))}. Codenames are pipeline coordinates, not copy — "
                f"restate the idea in plain language."
            )


def validate_operator(
    row: dict[str, Any], candidate_id: str, problem_shape_id: str, pillars: dict[str, Any]
) -> str:
    configured = load_yaml(REPO / "config" / "operators.yml")
    operator_id = str(row.get("operator_id") or "")
    operator = (configured.get("operators") or {}).get(operator_id)
    if not operator:
        raise SystemExit(
            f"Invalid/missing operator_id for {candidate_id}: {operator_id!r}. "
            f"A candidate without an operator is admissible but not writable."
        )
    status = str(operator.get("status") or "")
    if status not in WRITABLE_OPERATOR_STATES:
        raise SystemExit(
            f"Operator {operator_id} is not writable (status={status}): {candidate_id}"
        )
    shape = (pillars.get("problem_shapes") or {}).get(problem_shape_id) or {}
    permitted = shape.get("operator_fit")
    if permitted and operator_id not in permitted:
        raise SystemExit(
            f"Operator {operator_id} is not fit for {problem_shape_id}: {candidate_id}"
        )
    return operator_id


def validate_form(row: dict[str, Any], candidate_id: str) -> str:
    configured = load_yaml(REPO / "config" / "forms.yml")
    form_id = str(row.get("form") or "")
    form = (configured.get("forms") or {}).get(form_id)
    if not form or str(form.get("status")) != "active":
        raise SystemExit(f"Invalid/missing form for {candidate_id}: {form_id!r}")
    return form_id


def validate_axis(row: dict[str, Any], candidate_id: str) -> str:
    configured = load_yaml(REPO / "config" / "axes.yml")
    axis_id = str(row.get("axis") or "")
    if axis_id not in (configured.get("axes") or {}):
        raise SystemExit(f"Invalid/missing axis for {candidate_id}: {axis_id!r}")
    return axis_id


def enforce_form_quota(rows: list[dict[str, Any]], day: str) -> dict[str, dict[str, int]]:
    """Count form usage and flag over-cap forms as warnings, not hard failures.

    Form choice is editorial style, not correctness — the same philosophy docs/03
    already applies to axis rotation ("轮换是编辑判断，不是机械规则"). Hard-failing the
    whole render over one extra mid_post threw away otherwise-legal reports and forced
    a manual form rebalance (seen 2026-07-29). Over-cap forms now surface loudly in the
    rotation self-check and on stderr; nothing auto-publishes, so Selene rebalances when
    writing. Each usage row carries its caps and an `over` flag for the report.
    """
    configured = load_yaml(REPO / "config" / "forms.yml")
    forms = configured.get("forms") or {}
    per_report: dict[str, int] = {}
    for row in rows:
        form_id = str(row.get("form") or "")
        if form_id:
            per_report[form_id] = per_report.get(form_id, 0) + 1
    connection = state_connection()
    try:

        def prior_window(days: int) -> dict[str, int]:
            return {
                str(record["form"]): int(record["total"])
                for record in connection.execute(
                    "SELECT form, COUNT(*) AS total FROM report_forms "
                    "WHERE report_date >= ? AND report_date < ? GROUP BY form",
                    (_days_ago(day, days), day),
                )
            }

        prior = prior_window(7)
        prior_month = prior_window(30)
    finally:
        connection.close()
    usage: dict[str, dict[str, int]] = {}
    for form_id, count in sorted(per_report.items()):
        definition = forms.get(form_id) or {}
        report_cap = definition.get("max_per_report")
        week_cap = definition.get("max_per_week")
        # 有些形态的节奏是按月的，不是按周。成绩单类（chart_post）是典型：
        # 它是账号的"信用证"，发一次立信，发多了就把信用证稀释成噪音。
        month_cap = definition.get("max_per_month")
        week_total = prior.get(form_id, 0) + count
        month_total = prior_month.get(form_id, 0) + count
        over: list[str] = []
        if report_cap is not None and count > int(report_cap):
            over.append(f"本期 {count} 条超过每期上限 {report_cap}")
        if week_cap is not None and week_total > int(week_cap):
            over.append(f"近 7 天 {week_total} 条超过每周上限 {week_cap}")
        if month_cap is not None and month_total > int(month_cap):
            over.append(f"近 30 天 {month_total} 条超过每月上限 {month_cap}")
        if over:
            print(
                f"WARN form quota {form_id}: {'; '.join(over)}（不阻断，请写稿时换形态）",
                file=sys.stderr,
            )
        usage[form_id] = {
            "report": count,
            "week": week_total,
            "month": month_total,
            "report_cap": report_cap,
            "week_cap": week_cap,
            "month_cap": month_cap,
            "over": over,
        }
    return usage


def record_report_forms(day: str, rows: list[dict[str, Any]]) -> None:
    connection = state_connection()
    try:
        connection.execute("DELETE FROM report_forms WHERE report_date = ?", (day,))
        connection.executemany(
            "INSERT OR REPLACE INTO report_forms(report_date, candidate_id, form, axis) "
            "VALUES(?, ?, ?, ?)",
            [
                (day, str(row["candidate_id"]), str(row.get("form") or ""), str(row.get("axis") or ""))
                for row in rows
                if row.get("form")
            ],
        )
        connection.commit()
    finally:
        connection.close()


def record_evergreen_usage(
    day: str, candidates: dict[str, dict[str, Any]], rows: list[dict[str, Any]]
) -> None:
    """Burn the cooldown for curated cells that actually reached the report.

    Only a published original counts. A cell that was watched or rejected stays
    available, otherwise a bad news day would silently consume the library.
    """
    used = [
        (candidates[row["candidate_id"]].get("curated") or {}, row)
        for row in rows
        if row["decision"] == "keep"
        and row.get("primary_action") == "original_post"
        and candidates.get(row["candidate_id"], {}).get("object_type") == "curated_cell"
    ]
    if not used:
        return
    connection = state_connection()
    try:
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
        connection.executemany(
            "INSERT INTO evergreen_usage(entry_id, intent_id, discipline, cell_key, used_date, outcome) "
            "VALUES(?, ?, ?, ?, ?, 'published')",
            [
                (
                    str(curated.get("entry_id") or ""),
                    str(curated.get("intent_id") or ""),
                    str(curated.get("discipline") or ""),
                    str(curated.get("cell_key") or ""),
                    day,
                )
                for curated, _row in used
                if curated.get("entry_id")
            ],
        )
        connection.commit()
    finally:
        connection.close()


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


def scrapable_columns(pillars: dict[str, Any]) -> set[str]:
    return {
        column_id
        for column_id, definition in (pillars.get("columns") or {}).items()
        if definition.get("scrapable")
    }


def recall_decisions_with_curated(
    source: dict[str, Any], judged: dict[str, Any], strict: bool = True
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]], dict[str, list[str]]]:
    """Return (candidates_by_id, decisions_by_id, problems) with curated cells spliced in.

    Curated cells skip Stage 1, so their library decision is injected here rather
    than judged. Shared by both validate-recall and scan-recall so the two can
    never disagree on what the decision set is. strict=False lets the scanner see
    an incomplete judge output instead of dying on it.
    """
    candidates = {item["candidate_id"]: item for item in source.get("candidates") or []}
    curated = {
        candidate_id: item
        for candidate_id, item in candidates.items()
        if item.get("object_type") == "curated_cell"
    }
    judgeable = set(candidates) - set(curated)
    if strict:
        decisions = decision_map(judged, judgeable, RECALL_STATES, "Recall")
        problems: dict[str, list[str]] = {}
    else:
        decisions, problems = decision_accounting(judged, judgeable, RECALL_STATES, "Recall")
    for candidate_id, item in curated.items():
        decision = ((item.get("curated") or {}).get("decision")) or {}
        if not decision:
            raise SystemExit(f"Curated cell lacks a library decision: {candidate_id}")
        decisions[candidate_id] = {**decision, "candidate_id": candidate_id}
    return candidates, decisions, problems


def normalize_recall_row(
    candidate: dict[str, Any],
    row: dict[str, Any],
    pillars: dict[str, Any],
    valid_columns: set[str],
) -> dict[str, Any]:
    """Apply every recall gate to one decision, raising SystemExit on any violation.

    This is the single source of truth for what makes a recall row legal. Both the
    fail-closed validator and the non-raising scanner call it, so they cannot drift.
    """
    candidate_id = candidate["candidate_id"]
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
    return cleaned


def scan_recall(args: argparse.Namespace) -> None:
    """Collect every recall violation without dying on the first one.

    Curated cells are skipped: their decision is deterministic, so a violation there
    is a config bug, not a judge miss, and must not be sent back for re-judging.
    Writes {"illegal": [{candidate_id, reason}], "ok": bool} for the repair loop.
    """
    source = load_json(args.candidates)
    judged = load_json_loose(args.judged)
    candidates, decisions, problems = recall_decisions_with_curated(source, judged, strict=False)
    pillars = load_yaml(REPO / "config" / "pillars.yml")
    valid_columns = scrapable_columns(pillars)
    illegal: list[dict[str, str]] = []
    for candidate_id, row in decisions.items():
        if candidates[candidate_id].get("object_type") == "curated_cell":
            continue
        try:
            normalize_recall_row(candidates[candidate_id], row, pillars, valid_columns)
        except SystemExit as exc:
            illegal.append({"candidate_id": candidate_id, "reason": str(exc)})
    # 判官漏答和判错一样要重判。漏答的行在 decisions 里根本不存在，所以逐行扫描
    # 永远看不见它 —— 这是 2026-07-30 那次 6 条漏答、修复循环一次没跑就整轮报废的原因。
    for candidate_id in problems.get("missing") or []:
        illegal.append({"candidate_id": candidate_id, "reason": "判官没有返回这条候选的决策（漏答）"})
    for candidate_id in problems.get("bad_state") or []:
        illegal.append({"candidate_id": candidate_id, "reason": "判官返回了非法 decision 状态"})
    for candidate_id in problems.get("duplicate") or []:
        illegal.append({"candidate_id": candidate_id, "reason": "判官对同一条候选返回了多行"})
    payload = {"ok": not illegal, "illegal": illegal, "accounting": problems}
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


def splice_recall(args: argparse.Namespace) -> None:
    """Overlay re-judged rows onto a base recall file, keeping one row per candidate."""
    base = load_json_loose(args.base)
    patch_rows: dict[str, dict[str, Any]] = {}
    for path in args.patch:
        for row in load_json_loose(path).get("decisions") or []:
            patch_rows[str(row.get("candidate_id"))] = row
    merged = [patch_rows.get(str(r.get("candidate_id")), r) for r in base.get("decisions") or []]
    # 漏答的候选在 base 里没有行，只做覆盖会把重判结果静默丢掉 —— 必须追加。
    seen = {str(r.get("candidate_id")) for r in merged}
    added = [row for candidate_id, row in patch_rows.items() if candidate_id not in seen]
    merged.extend(added)
    applied = sum(1 for r in merged if str(r.get("candidate_id")) in patch_rows)
    out = {"stage": base.get("stage", "recall"), "decisions": merged}
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        f"spliced {applied} repaired row(s) ({len(added)} newly added) "
        f"over {len(merged)} decisions -> {args.out}"
    )


def validate_recall(args: argparse.Namespace) -> None:
    source = load_json(args.candidates)
    judged = load_json_loose(args.judged)
    candidates, decisions, _ = recall_decisions_with_curated(source, judged)
    pillars = load_yaml(REPO / "config" / "pillars.yml")
    valid_columns = scrapable_columns(pillars)
    normalized: list[dict[str, Any]] = [
        normalize_recall_row(candidates[candidate_id], row, pillars, valid_columns)
        for candidate_id, row in decisions.items()
    ]
    normalized.sort(key=lambda row: (-float(candidates[row["candidate_id"]].get("priority_score") or 0), row["candidate_id"]))
    sources_cfg = load_yaml(REPO / "config" / "sources.yml")
    recall_cfg = sources_cfg.get("recall") or {}
    maximum = int(recall_cfg.get("max_enrichment_candidates", 32))
    eligible = [row for row in normalized if row["decision"] in {"keep_for_enrichment", "interaction_only"}]

    def source_bucket(candidate: dict[str, Any]) -> str:
        platform = str(candidate.get("platform") or "")
        path = str(candidate.get("source_path") or "")
        role = evidence_role(candidate, sources_cfg)
        if candidate.get("object_type") == "curated_cell":
            return "evergreen"
        # 互动候选必须自己一个桶。原来它掉进 "x"（预算 8），和关键词搜索、会话图、
        # 动态关注抢同一批名额 —— 结果 124 人的互动池一条 quote 都没发出去过。
        if path == "x_interaction_pool":
            return "interaction"
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
    # Evergreen is the floor of the day's output. It is seated before any budget
    # split so a heavy news day can never crowd out the one guaranteed item.
    for row in eligible:
        if source_bucket(candidates[row["candidate_id"]]) == "evergreen":
            selected.append(row)
            selected_set.add(row["candidate_id"])
    # Interaction gets reserved seats for the same reason, and it needs them more: a
    # quote costs no original writing but is the only lever on account weight, and it
    # is the line that lost every seat to keyword search.
    reserved_interaction = int(recall_cfg.get("reserved_interaction", 0))
    if reserved_interaction > 0:
        seated = 0
        for row in eligible:
            if seated >= reserved_interaction:
                break
            if row["candidate_id"] in selected_set:
                continue
            if source_bucket(candidates[row["candidate_id"]]) == "interaction":
                selected.append(row)
                selected_set.add(row["candidate_id"])
                seated += 1
    for bucket, budget in budgets.items():
        # 必须排掉已入座的：常青和互动是在预算切分之前入座的，不去重就会被这里再塞一遍，
        # 重复行占掉 max_enrichment_candidates 的名额，审计里表现为 queued > eligible。
        matches = [
            row
            for row in eligible
            if source_bucket(candidates[row["candidate_id"]]) == bucket
            and row["candidate_id"] not in selected_set
        ]
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


LEAD_ONLY_ROLES = frozenset(
    {"reputable_news_lead", "paper_abstract_only", "community_case_lead", "anonymous_opinion"}
)


def lead_only_without_primary(candidate: dict[str, Any]) -> str | None:
    """Return a reason when a lead_only candidate never reached primary evidence.

    docs/03: a lead_only source may surface a problem but cannot be upgraded before
    the original record is in hand. The judge reads prose and can be talked past an
    interstitial; this check cannot.
    """
    context = candidate.get("context") or {}
    role = str(candidate.get("evidence_role") or candidate.get("source_role") or "")
    if not (bool(context.get("lead_only")) or role in LEAD_ONLY_ROLES):
        return None
    enrichment = candidate.get("enrichment") or {}
    status = str(enrichment.get("status") or "unavailable")
    if status == "ok":
        return None
    return str(enrichment.get("error") or status)


def normalize_evidence_decision(
    candidate: dict[str, Any], row: dict[str, Any], pillars: dict[str, Any]
) -> dict[str, Any]:
    cleaned = dict(row)
    cleaned["candidate_id"] = candidate["candidate_id"]
    if cleaned.get("decision") == "keep":
        missing = lead_only_without_primary(candidate)
        if missing:
            cleaned["decision"] = "watch"
            note = f"回源未取得一手证据（{missing}），线索型来源不得升级为原创"
            prior = str(row.get("reason") or "").strip()
            cleaned["reason"] = f"{note}；判官原判：{prior}" if prior else note
            cleaned["downgraded_from"] = "keep"
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
        cleaned["operator_id"] = validate_operator(
            row, candidate["candidate_id"], problem_shape_id, pillars
        )
        cleaned["form"] = validate_form(row, candidate["candidate_id"])
        cleaned["axis"] = validate_axis(row, candidate["candidate_id"])
        assert_no_internal_codenames(row, candidate["candidate_id"])
    elif cleaned["decision"] == "interaction":
        cleaned["primary_destination"] = "C8"
        cleaned["primary_action"] = allowed_primary_action(candidate, primary_action)
        if cleaned["primary_action"] not in {"x_reply", "x_quote", "reddit_reply"}:
            raise SystemExit(
                f"Evidence interaction requires one interaction action: {candidate['candidate_id']}"
            )
        context = candidate.get("context") or {}
        collected_route = str(context.get("account_route") or "")
        judged_route = str(row.get("account_route") or "")
        if judged_route not in {"official", "founder", ""}:
            raise SystemExit(
                f"Interaction account_route must be official or founder: {candidate['candidate_id']}"
            )
        # 采集期已经按层级 + 内容类型判了一次。判官可以把 official 降成 founder
        # （它读到了词表看不出的东西），但绝不许把 founder 升成 official —— 否则
        # 段位闸门就变成一句建议。与"线索源不得升级为原创"同一个形状。
        route = judged_route or collected_route or "founder"
        if collected_route == "founder" and route == "official":
            route = "founder"
            cleaned["route_downgrade_note"] = (
                "判官想让官号出手，但采集期的层级/内容类型闸门已判为老板个人号——不许上调。"
            )
        cleaned["account_route"] = route
        cleaned["account_tier"] = str(context.get("account_tier") or "未收录")
        cleaned["account_route_reason"] = str(
            context.get("account_route_reason") or "—"
        )
        # 互动是唯一不产出原创的动作，最容易退化成"Great point!"。这两个字段是
        # 强制想清楚：对方到底在主张什么，以及这条怎么挂回我们唯一那条叙事（验证）。
        # 折进现有第二轮判官，不新开第三道关。
        for field, label in (
            ("stance_read", "对方在主张什么"),
            ("verification_hook", "这条怎么挂回验证叙事"),
        ):
            value = str(row.get(field) or "").strip()
            if len(value) < 8:
                raise SystemExit(
                    f"Interaction requires {field}（{label}）: {candidate['candidate_id']}"
                )
            cleaned[field] = value
        assert_no_internal_codenames(row, candidate["candidate_id"])
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


_DOI_IN_TEXT = re.compile(r"10\.\d{4,9}/[^\s;；,，、（）\[\]]+")


def _source_url(candidate: dict[str, Any]) -> str:
    """A clickable URL, or empty string if the source genuinely has none.

    Curated cells carry their anchor as a locator (often a DOI plus a Chinese note),
    not a url, so a naive candidate['url'] renders an empty or malformed link.
    """
    url = str(candidate.get("url") or "").strip()
    if url.startswith("http"):
        match = _DOI_IN_TEXT.search(url)
        # Strip any trailing prose that got concatenated into the locator.
        return f"https://doi.org/{match.group(0)}" if match and "doi.org" in url else url
    anchor = (candidate.get("curated") or {}).get("anchor") or {}
    match = _DOI_IN_TEXT.search(str(anchor.get("locator") or ""))
    if match:
        return f"https://doi.org/{match.group(0)}"
    return str(anchor.get("url") or "").strip()


def _source(candidate: dict[str, Any]) -> str:
    label = candidate.get("title") or str(candidate.get("text") or "")[:90] or candidate["candidate_id"]
    handle = _account_handle(candidate)
    prefix = f"@{handle}: " if handle else ""
    text = _cell(prefix + str(label), 120)
    url = _source_url(candidate)
    return f"[{text}]({url})" if url else text


def _named(path: str, section: str, key: Any) -> str:
    """Render the human name, never the codename — the report should not train on IDs."""
    if not key:
        return "—"
    definition = (load_yaml(REPO / "config" / path).get(section) or {}).get(str(key)) or {}
    return _cell(definition.get("name") or key, 40)


def _deadline(candidate: dict[str, Any]) -> str:
    """Structured comment/close date if the source carries one (Federal Register)."""
    for entry in candidate.get("provenance") or []:
        close = (entry or {}).get("comments_close_on")
        if close:
            return str(close)
    return "—"


def _operator_label(row: dict[str, Any]) -> str:
    return _named("operators.yml", "operators", row.get("operator_id"))


def _form_label(row: dict[str, Any]) -> str:
    return _named("forms.yml", "forms", row.get("form"))


def _axis_label(row: dict[str, Any]) -> str:
    return _named("axes.yml", "axes", row.get("axis"))


def _rotation_section(
    original_rows: list[dict[str, Any]], form_usage: dict[str, dict[str, int]]
) -> list[str]:
    if not original_rows:
        return []
    axes_cfg = load_yaml(REPO / "config" / "axes.yml")
    axis_counts: dict[str, int] = {}
    for row in original_rows:
        axis_id = str(row.get("axis") or "")
        if axis_id:
            axis_counts[axis_id] = axis_counts.get(axis_id, 0) + 1
    lines = ["## 轮换自查", "", "| 轴 | 本期条数 |", "|---|---:|"]
    for axis_id, count in sorted(axis_counts.items(), key=lambda item: (-item[1], item[0])):
        lines.append(f"| {_named('axes.yml', 'axes', axis_id)} | {count} |")
    lines.append("")
    if len(axis_counts) == 1 and (axes_cfg.get("rotation") or {}).get("warn_when_single_axis"):
        only = next(iter(axis_counts))
        lines.extend([
            f"> 告警：本期全部原创落在同一根轴（{_named('axes.yml', 'axes', only)}）。"
            f"母问题只有一句，轴决定今天从哪个切面问——换一根。",
            "",
        ])
    if form_usage:
        lines.extend(["| 形态 | 本期 | 近 7 天 | 上限 |", "|---|---:|---:|---|"])
        for form_id, counts in sorted(form_usage.items()):
            caps = "/".join(
                str(counts.get(key) if counts.get(key) is not None else "—")
                for key in ("report_cap", "week_cap")
            )
            flag = " ⚠️" if counts.get("over") else ""
            lines.append(
                f"| {_named('forms.yml', 'forms', form_id)}{flag} | {counts['report']} | "
                f"{counts['week']} | {caps} |"
            )
        lines.append("")
        over_lines = [
            f"> 形态告警：{_named('forms.yml', 'forms', form_id)} —— {'；'.join(counts['over'])}。"
            f"形态与算子刻意解耦，换形态、不换选题。"
            for form_id, counts in sorted(form_usage.items())
            if counts.get("over")
        ]
        if over_lines:
            lines.extend(over_lines + [""])
    return lines


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
    form_usage = enforce_form_quota(original_rows, day)
    # Order originals column-by-column so the index and the detail below use the
    # same numbering. The old layout was one 11-column table per section — every
    # cell held a paragraph, so it only read after horizontal scrolling. Now: a
    # narrow index up top, then one vertical table per item (field | content),
    # which wraps instead of scrolling.
    ordered: list[tuple[int, str, dict[str, Any]]] = []
    for column_id, definition in columns.items():
        for row in (r for r in original_rows if r["primary_destination"] == column_id):
            ordered.append((len(ordered) + 1, str(definition["name"]), row))

    if ordered:
        deadlines = [
            (num, _deadline(candidates[row["candidate_id"]]))
            for num, _name, row in ordered
        ]
        with_dates = [f"#{num} 截止 {close}" for num, close in deadlines if close != "—"]
        summary = f"今天可写 {len(ordered)} 条"
        if with_dates:
            summary += "，有截止日：" + " · ".join(with_dates)
        lines.extend([summary, "", "## 速览", "", "| # | 栏目 | 一句话角度 | 形态 | 截止日 |", "|---|---|---|---|---|"])
        for num, name, row in ordered:
            candidate = candidates[row["candidate_id"]]
            lines.append(
                f"| {num} | {name} | {_cell(row['possible_angle'], 70)} | {_form_label(row)} | {_deadline(candidate)} |"
            )
        lines.append("")

    for num, name, row in ordered:
        candidate = candidates[row["candidate_id"]]
        combo = f"{_operator_label(row)} · {_form_label(row)} · {_axis_label(row)}"
        lines.extend([
            f"## {num} · {name}", "",
            _source(candidate), "",
            "| 字段 | 内容 |", "|---|---|",
            f"| 一句话角度 | {_cell(row['possible_angle'])} |",
            f"| 来源说了什么 | {_cell(row['source_says'])} |",
            f"| 为什么现在 | {_cell(row['why_now'])} |",
            f"| 为什么是我们 | {_cell(row['why_apodex'])} |",
            f"| 推断边界（不能写成事实的） | {_cell(row['inference_boundary'])} |",
            f"| 发布前需核验 | {_cell(row['needs_verification'])} |",
            f"| 截止日 | {_deadline(candidate)} |",
            f"| 算子·形态·轴 | {combo} |",
            "",
        ])
    # 轮换自查 / 观察 / 来源覆盖三节已于 2026-07-29 从日报移除（Selene 明确要求）：
    # 它们是运行体检，不是选题决策，Selene 在对话里拿就够了。
    # form_usage 仍然计算，因为配额校验要用；只是不再打印。
    _ = form_usage
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
        # 用哪个账号发是第一列：这是唯一不可撤回的选择，读日报时不该需要往右滚才看到。
        lines.extend([
            f"## {title}", "",
            "| 用哪个号 | 来源 | 动作 | 对方在主张什么 | 怎么挂回验证 | 边界 |",
            "|---|---|---|---|---|---|",
        ])
        for row in grouped:
            candidate = candidates[row["candidate_id"]]
            route = str(row.get("account_route") or "founder")
            label = "**官号**" if route == "official" else "老板个人号"
            lines.append(
                "| " + " | ".join(
                    [
                        label, _source(candidate), _cell(row["primary_action"]),
                        _cell(row.get("stance_read")), _cell(row.get("verification_hook")),
                        _cell(row["inference_boundary"]),
                    ]
                ) + " |"
            )
        routed = [
            f"{str(row.get('account_tier') or '未收录')}／{str(row.get('account_route_reason') or '—')}"
            for row in grouped
        ]
        lines.extend(["", "分流依据：" + "；".join(dict.fromkeys(routed)), ""])
    # 观察 / 来源覆盖 / 终审淘汰三节不再进日报（2026-07-29）。
    # 它们回答的是"系统跑得怎么样"，不是"今天写什么"；数据仍在
    # .evidence-clean-*.json 里，跑完由 Claude 在对话里报。
    return "\n".join(lines)


def scan_evidence(args: argparse.Namespace) -> None:
    """Collect every Evidence-stage violation without dying on the first.

    The judge mis-pairs intent/shape/thesis at Stage 2 just as it does at Stage 1
    (observed 2026-07-29: EI3 x PS8). This lets run_oracle re-judge the offending
    rows instead of a human hand-editing evidence-raw.
    """
    source = load_json(args.candidates)
    judged = load_json_loose(args.judged)
    candidates = {item["candidate_id"]: item for item in source.get("candidates") or []}
    decisions, problems = decision_accounting(
        judged, set(candidates), EVIDENCE_STATES, "Evidence"
    )
    pillars = load_yaml(REPO / "config" / "pillars.yml")
    illegal: list[dict[str, str]] = []
    for candidate_id, row in decisions.items():
        try:
            normalize_evidence_decision(candidates[candidate_id], row, pillars)
        except SystemExit as exc:
            illegal.append({"candidate_id": candidate_id, "reason": str(exc)})
    for candidate_id in problems["missing"]:
        illegal.append({"candidate_id": candidate_id, "reason": "判官没有返回这条候选的决策（漏答）"})
    for candidate_id in problems["bad_state"]:
        illegal.append({"candidate_id": candidate_id, "reason": "判官返回了非法 decision 状态"})
    for candidate_id in problems["duplicate"]:
        illegal.append({"candidate_id": candidate_id, "reason": "判官对同一条候选返回了多行"})
    payload = {"ok": not illegal, "illegal": illegal, "accounting": problems}
    if args.out:
        Path(args.out).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False))


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
    report = render_report(source, candidates, normalized)
    Path(args.out).write_text(report, encoding="utf-8")
    day = str(source.get("enriched_at") or dt.date.today().isoformat())[:10]
    record_report_forms(
        day,
        [
            row for row in normalized
            if row["decision"] == "keep" and row["primary_action"] == "original_post"
        ],
    )
    record_evergreen_usage(day, candidates, normalized)
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
    scanner = sub.add_parser("scan-recall")
    scanner.add_argument("judged")
    scanner.add_argument("--candidates", required=True)
    scanner.add_argument("--out")
    escanner = sub.add_parser("scan-evidence")
    escanner.add_argument("judged")
    escanner.add_argument("--candidates", required=True)
    escanner.add_argument("--out")
    splicer = sub.add_parser("splice-recall")
    splicer.add_argument("--base", required=True)
    splicer.add_argument("--patch", nargs="+", required=True)
    splicer.add_argument("--out", required=True)
    evidence = sub.add_parser("render")
    evidence.add_argument("judged")
    evidence.add_argument("--candidates", required=True)
    evidence.add_argument("--out", required=True)
    evidence.add_argument("--clean-out", required=True)
    return parser


def main() -> None:
    argv = sys.argv[1:]
    commands = {
        "prefilter", "recall-chunks", "merge-recall", "validate-recall",
        "scan-recall", "scan-evidence", "splice-recall", "render",
    }
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
    elif args.command == "scan-recall":
        scan_recall(args)
    elif args.command == "scan-evidence":
        scan_evidence(args)
    elif args.command == "splice-recall":
        splice_recall(args)
    else:
        render(args)


if __name__ == "__main__":
    main()
