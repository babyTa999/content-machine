from __future__ import annotations

import unittest
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from oracle import score
from oracle import collect


def candidate(
    candidate_id: str,
    *,
    platform: str = "web",
    url: str = "https://example.org/item",
    evidence_role: str = "official_record",
) -> dict:
    return {
        "candidate_id": candidate_id,
        "canonical_event_id": candidate_id,
        "object_type": "canonical_event",
        "platform": platform,
        "url": url,
        "canonical_url": url,
        "title": "Updated official guidance changes the decision",
        "text": "Updated guidance based on new evidence",
        "source_path": "official_update",
        "source_role": evidence_role,
        "context": {"evidence_role": evidence_role},
        "authority": {"institution": evidence_role == "official_record"},
        "engagement": 0,
    }


class ProductLedContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.pillars = score.load_yaml(score.REPO / "config" / "pillars.yml")
        cls.sources = score.load_yaml(score.REPO / "config" / "sources.yml")
        cls.intents = score.load_yaml(score.REPO / "config" / "editorial_intents.yml")
        cls.golden = score.load_yaml(score.REPO / "config" / "editorial_golden_set.yml")

    def test_editorial_intents_and_golden_set_reference_valid_ids(self) -> None:
        intents = self.intents.get("intents") or {}
        for intent_id, definition in intents.items():
            self.assertTrue(definition.get("event_triggers"), intent_id)
            self.assertTrue(definition.get("source_types"), intent_id)
            self.assertTrue(definition.get("positive_examples"), intent_id)
            self.assertTrue(definition.get("negative_examples"), intent_id)
            self.assertTrue(definition.get("product_backing"), intent_id)
            for column in definition.get("columns") or []:
                self.assertIn(column, self.pillars["columns"], intent_id)
            for shape in definition.get("problem_shapes") or []:
                self.assertIn(shape, self.pillars["problem_shapes"], intent_id)
            for thesis in definition.get("theses") or []:
                self.assertIn(thesis, self.pillars["theses"], intent_id)
            for backing in definition.get("product_backing") or []:
                self.assertIn(backing, self.pillars["capability_backing"], intent_id)
        for example in self.golden.get("examples") or []:
            for intent_id in example.get("matched_intents") or []:
                self.assertIn(intent_id, intents, example["id"])

    def test_valid_original_has_one_destination_and_action(self) -> None:
        item = candidate("cand_keep")
        row = {
            "candidate_id": "cand_keep",
            "decision": "keep",
            "editorial_intent_id": "EI1_official_answer_changed",
            "event_match_reason": "正式指南因新证据更新",
            "problem_shape_id": "PS1_evidence_shift",
            "thesis_id": "T3_asynchronous_verification",
            "signal_role": "update",
            "capability_backing": "technical_report",
            "primary_destination": "C1",
            "primary_action": "original_post",
            "operator_id": "OP1_constant_falsified",
            "form": "mid_post",
            "axis": "AX2_workflow_moment",
            "source_says": "官方更新了既有判断",
            "why_now": "刚刚更新",
            "why_apodex": "对应持续检索与异步验证",
            "possible_angle": "新证据如何改变答案",
            "inference_boundary": "不能外推到所有情况",
            "needs_verification": ["官方原文"],
            "reason": "通过",
        }
        normalized = score.normalize_evidence_decision(item, row, self.pillars)
        self.assertEqual(normalized["primary_destination"], "C1")
        self.assertEqual(normalized["primary_action"], "original_post")
        self.assertEqual(normalized["operator_id"], "OP1_constant_falsified")
        self.assertEqual(normalized["form"], "mid_post")
        self.assertEqual(normalized["axis"], "AX2_workflow_moment")

    def _writable_row(self, candidate_id: str, **overrides: object) -> dict:
        row = {
            "candidate_id": candidate_id,
            "decision": "keep",
            "editorial_intent_id": "EI1_official_answer_changed",
            "event_match_reason": "正式指南因新证据更新",
            "problem_shape_id": "PS1_evidence_shift",
            "thesis_id": "T3_asynchronous_verification",
            "signal_role": "update",
            "capability_backing": "technical_report",
            "primary_destination": "C1",
            "primary_action": "original_post",
            "operator_id": "OP1_constant_falsified",
            "form": "mid_post",
            "axis": "AX2_workflow_moment",
            "source_says": "官方更新了既有判断",
            "why_now": "刚刚更新",
            "why_apodex": "对应持续检索与异步验证",
            "possible_angle": "新证据如何改变答案",
            "inference_boundary": "不能外推到所有情况",
            "needs_verification": ["官方原文"],
            "reason": "通过",
        }
        row.update(overrides)
        return row

    def test_candidate_without_operator_is_not_writable(self) -> None:
        """选题合法但不可动笔，正是 source 那步死掉的地方。"""
        item = candidate("cand_no_op")
        row = self._writable_row("cand_no_op", operator_id="")
        with self.assertRaises(SystemExit):
            score.normalize_evidence_decision(item, row, self.pillars)

    def test_dropped_and_paused_operators_are_rejected(self) -> None:
        for operator_id in ("OP6_price_vs_cost", "OP13_own_numbers_as_industry_index",
                            "OP3_cost_confession_table"):
            item = candidate("cand_" + operator_id)
            row = self._writable_row("cand_" + operator_id, operator_id=operator_id)
            with self.assertRaises(SystemExit, msg=operator_id):
                score.normalize_evidence_decision(item, row, self.pillars)

    def test_operator_must_fit_the_problem_shape(self) -> None:
        """PS10 声明了 operator_fit；不在表里的算子必须被拒。"""
        item = candidate("cand_misfit")
        row = self._writable_row(
            "cand_misfit",
            editorial_intent_id="EI6_missing_control",
            problem_shape_id="PS10_missing_control",
            thesis_id="T2_independent_verification",
            operator_id="OP7_compliance_clock_recompute",
        )
        with self.assertRaises(SystemExit):
            score.normalize_evidence_decision(item, row, self.pillars)
        row["operator_id"] = "OP8_primary_document_raid"
        self.assertEqual(
            score.normalize_evidence_decision(item, row, self.pillars)["operator_id"],
            "OP8_primary_document_raid",
        )

    def test_invalid_form_and_axis_are_rejected(self) -> None:
        for field, bad in (("form", "essay"), ("axis", "AX99_nope")):
            item = candidate("cand_" + field)
            row = self._writable_row("cand_" + field, **{field: bad})
            with self.assertRaises(SystemExit, msg=field):
                score.normalize_evidence_decision(item, row, self.pillars)

    def test_internal_codename_in_prose_is_rejected(self) -> None:
        leaks = [
            ("possible_angle", "走 E1 引擎讲这条"),
            ("why_apodex", "对应 PS10_missing_control"),
            ("source_says", "用 OP8 写"),
            ("inference_boundary", "限于 EI6 范围"),
        ]
        for field, text in leaks:
            item = candidate("cand_leak_" + field)
            row = self._writable_row("cand_leak_" + field, **{field: text})
            with self.assertRaises(SystemExit, msg=field):
                score.normalize_evidence_decision(item, row, self.pillars)

    def test_plain_language_prose_passes_the_codename_filter(self) -> None:
        item = candidate("cand_clean")
        row = self._writable_row(
            "cand_clean",
            possible_angle="你测到的结果，可能来自操作本身而不是被干预的对象",
            why_apodex="外部独立验证，不是同一个推理者自查",
        )
        self.assertEqual(
            score.normalize_evidence_decision(item, row, self.pillars)["candidate_id"],
            "cand_clean",
        )

    def test_form_quota_caps_long_posts_in_one_report(self) -> None:
        forms = score.load_yaml(score.REPO / "config" / "forms.yml")
        cap = int(forms["forms"]["long_post"]["max_per_report"])
        rows = [
            {"candidate_id": f"cand_long_{index}", "form": "long_post", "axis": "AX1_discipline"}
            for index in range(cap + 1)
        ]
        with self.assertRaises(SystemExit):
            score.enforce_form_quota(rows, "2026-07-27")
        usage = score.enforce_form_quota(rows[:cap], "2026-07-27")
        self.assertEqual(usage["long_post"]["report"], cap)

    def test_original_without_owned_thesis_fails(self) -> None:
        item = candidate("cand_bad")
        row = {
            "candidate_id": "cand_bad",
            "decision": "keep",
            "problem_shape_id": "PS1_evidence_shift",
            "thesis_id": "",
            "signal_role": "update",
            "capability_backing": "technical_report",
            "primary_destination": "C1",
            "primary_action": "original_post",
        }
        with self.assertRaises(SystemExit):
            score.normalize_evidence_decision(item, row, self.pillars)

    def test_interaction_cannot_also_be_original(self) -> None:
        item = candidate("cand_interaction", platform="x")
        row = {
            "candidate_id": "cand_interaction",
            "decision": "interaction",
            "primary_destination": "C1",
            "primary_action": "x_reply",
        }
        normalized = score.normalize_evidence_decision(item, row, self.pillars)
        self.assertEqual(normalized["primary_destination"], "C8")
        self.assertEqual(normalized["primary_action"], "x_reply")

    def test_same_source_can_appear_only_once(self) -> None:
        first = candidate("cand_a")
        second = candidate("cand_b")
        first["canonical_event_id"] = "evt_shared"
        second["canonical_event_id"] = "evt_shared"
        candidates = {"cand_a": first, "cand_b": second}
        rows = [
            {
                "candidate_id": "cand_a",
                "decision": "keep",
                "primary_destination": "C1",
                "primary_action": "original_post",
                "source_says": "one event",
                "why_now": "current",
                "why_apodex": "matched",
                "possible_angle": "angle",
                "inference_boundary": "limited",
                "needs_verification": [],
            },
            {
                "candidate_id": "cand_b",
                "decision": "interaction",
                "primary_destination": "C8",
                "primary_action": "x_reply",
            },
        ]
        with self.assertRaises(SystemExit):
            score.render_report({"enriched_at": "2026-07-27"}, candidates, rows)

    def test_official_source_outranks_anonymous_opinion(self) -> None:
        official = candidate("cand_official", evidence_role="official_record")
        anonymous = candidate(
            "cand_anon",
            platform="reddit",
            url="https://reddit.com/r/example/1",
            evidence_role="anonymous_opinion",
        )
        self.assertGreater(
            score.priority_score(official, self.sources),
            score.priority_score(anonymous, self.sources),
        )

    def test_related_mentions_become_one_canonical_event(self) -> None:
        first = collect.make_signal(
            platform="x",
            source_path="x_watchlist",
            url="https://x.com/agency/status/1",
            external_id="1",
            title="Agency revises safety guidance after new evidence",
            text="Agency revises safety guidance after new evidence",
            published_at="2026-07-27T01:00:00Z",
        )
        second = collect.make_signal(
            platform="web",
            source_path="official_update",
            url="https://agency.example/revised-guidance",
            title="Agency revises safety guidance after new evidence",
            text="The official record.",
            published_at="2026-07-27T02:00:00Z",
        )
        events = collect.build_canonical_events([first, second])
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["object_type"], "canonical_event")
        self.assertEqual(events[0]["mention_count"], 2)

    def test_query_provenance_is_not_an_intent_match(self) -> None:
        event = candidate("evt_query_noise")
        event["title"] = "Unrelated personal opinion"
        event["text"] = "A personal anecdote with no qualifying event."
        event["provenance"] = [{"editorial_intent_id": "EI5_fragmented_record_changes_case"}]
        hints = score.editorial_intent_hints(event, self.intents)
        self.assertEqual(hints, [])

    def test_each_active_intent_contributes_one_rotating_query(self) -> None:
        now = __import__("datetime").datetime(2026, 7, 27, tzinfo=__import__("datetime").timezone.utc)
        queries = collect._editorial_intent_queries(now)
        active = {
            intent_id
            for intent_id, definition in self.intents["intents"].items()
            if definition.get("status") == "active"
        }
        self.assertEqual({intent_id for intent_id, _ in queries}, active)
        self.assertEqual(len(queries), len(active))

    def test_offline_cli_round_trip(self) -> None:
        with tempfile.TemporaryDirectory(prefix="apodex-contract-") as temp:
            directory = Path(temp)
            env = dict(os.environ)
            env["APODEX_CONTENT_STATE_DB"] = str(directory / "state.sqlite3")
            item = {
                **candidate("cand_contract"),
                "source": "web",
                "discovery_paths": ["official_update"],
                "content_hash": "contract-hash",
                "story_key": "contract-story",
                "lang": "en",
                "published_at": "2026-07-27T00:00:00+00:00",
                "age_h": 1,
                "author": {},
                "metrics": {},
                "provenance": [],
                "action_hints": ["original_post"],
            }
            raw = {
                "schema_version": 4,
                "object_type": "canonical_event_collection",
                "collected_at": "2026-07-27T08:00:00+00:00",
                "source_health": {},
                "candidates": [item],
            }
            raw_path = directory / "raw.json"
            recall_input = directory / "input.json"
            raw_path.write_text(json.dumps(raw), encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(score.REPO / "oracle" / "score.py"),
                    "prefilter",
                    str(raw_path),
                    "--out",
                    str(directory / "recall.md"),
                    "--json-out",
                    str(recall_input),
                ],
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )
            recall = {
                "stage": "recall",
                "decisions": [
                    {
                        "candidate_id": "cand_contract",
                        "decision": "keep_for_enrichment",
                        "editorial_intent_id": "EI1_official_answer_changed",
                        "event_match_reason": "official answer changed",
                        "problem_shape_id": "PS1_evidence_shift",
                        "thesis_id": "T3_asynchronous_verification",
                        "likely_column": "C1",
                        "primary_action": "original_post",
                        "signal_role": "update",
                        "capability_backing": "technical_report",
                        "reason": "matched",
                        "questions_for_enrichment": ["official source"],
                    }
                ],
            }
            recall_path = directory / "recall.json"
            recall_path.write_text(json.dumps(recall), encoding="utf-8")
            subprocess.run(
                [
                    sys.executable,
                    str(score.REPO / "oracle" / "score.py"),
                    "validate-recall",
                    str(recall_path),
                    "--candidates",
                    str(recall_input),
                    "--out",
                    str(directory / "recall-clean.json"),
                    "--queue-out",
                    str(directory / "queue.json"),
                ],
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )
            enriched = {
                "schema_version": 4,
                "run_id": "test",
                "enriched_at": "2026-07-27T08:10:00+00:00",
                "source_audit": {},
                "candidates": json.loads(recall_input.read_text())["candidates"],
            }
            enriched_path = directory / "enriched.json"
            enriched_path.write_text(json.dumps(enriched), encoding="utf-8")
            evidence = {
                "stage": "evidence",
                "decisions": [
                    {
                        "candidate_id": "cand_contract",
                        "decision": "keep",
                        "editorial_intent_id": "EI1_official_answer_changed",
                        "event_match_reason": "official answer changed",
                        "problem_shape_id": "PS1_evidence_shift",
                        "thesis_id": "T3_asynchronous_verification",
                        "signal_role": "update",
                        "capability_backing": "technical_report",
                        "primary_destination": "C1",
                        "primary_action": "original_post",
                        "operator_id": "OP1_constant_falsified",
                        "form": "mid_post",
                        "axis": "AX2_workflow_moment",
                        "source_says": "official update",
                        "why_now": "current",
                        "why_apodex": "matched design choice",
                        "possible_angle": "evidence changed the answer",
                        "inference_boundary": "limited",
                        "needs_verification": ["official page"],
                        "secondary_note": "none",
                        "reason": "keep",
                    }
                ],
            }
            evidence_path = directory / "evidence.json"
            evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
            report_path = directory / "report.md"
            subprocess.run(
                [
                    sys.executable,
                    str(score.REPO / "oracle" / "score.py"),
                    "render",
                    str(evidence_path),
                    "--candidates",
                    str(enriched_path),
                    "--out",
                    str(report_path),
                    "--clean-out",
                    str(directory / "evidence-clean.json"),
                ],
                check=True,
                env=env,
                capture_output=True,
                text=True,
            )
            report = report_path.read_text(encoding="utf-8")
            self.assertIn("Problem-aware｜现实中的难题形态", report)
            self.assertNotIn("X 互动池", report)
            self.assertIn("轮换自查", report)
            # 报告展示人类可读的名称，不训练代号。
            self.assertIn("恒量证伪", report)
            self.assertNotIn("OP1_constant_falsified", report)


if __name__ == "__main__":
    unittest.main()
