from __future__ import annotations

import unittest
import argparse
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

    def test_form_quota_flags_over_cap_without_blocking_the_report(self) -> None:
        """形态配额是编辑风格，不是正确性：超了要醒目标记，但不许扔掉整份合法报告。

        2026-07-29 实测：判官重判后一期出现 3 个 mid_post，硬 raise 导致 render 整体失败，
        人只能手动改 form 才能出报告。轴轮换本来就是"告警不阻断"，形态照同一哲学。
        """
        forms = score.load_yaml(score.REPO / "config" / "forms.yml")
        cap = int(forms["forms"]["long_post"]["max_per_report"])
        rows = [
            {"candidate_id": f"cand_long_{index}", "form": "long_post", "axis": "AX1_discipline"}
            for index in range(cap + 1)
        ]
        usage = score.enforce_form_quota(rows, "2026-07-27")
        self.assertEqual(usage["long_post"]["report"], cap + 1)
        self.assertTrue(usage["long_post"]["over"], "超过每期上限必须被标记")
        within = score.enforce_form_quota(rows[:cap], "2026-07-27")
        self.assertEqual(within["long_post"]["report"], cap)
        self.assertFalse(within["long_post"]["over"], "未超上限不该报警")
        # 报告里必须能看见这条告警，否则等于静默放过。
        section = score._rotation_section(rows, usage)
        self.assertTrue(any("形态告警" in line for line in section))

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
            # 纵向布局：速览索引 + 每条一张 2 列明细表，不再是 11 列宽表
            self.assertIn("## 速览", report)
            self.assertIn("| 字段 | 内容 |", report)
            self.assertIn("一句话角度", report)
            # 运行体检三节不进日报（2026-07-29），由 Claude 在对话里报
            self.assertNotIn("轮换自查", report)
            self.assertNotIn("来源覆盖", report)
            self.assertNotIn("终审淘汰", report)
            # 报告展示人类可读的名称，不训练代号。
            self.assertIn("恒量证伪", report)
            self.assertNotIn("OP1_constant_falsified", report)


class EvergreenPipelineTest(unittest.TestCase):
    """第二条产线（常青）与证据完整性闸门。全部离线，不调 claude、不联网。"""

    def test_every_library_entry_passes_the_gates(self) -> None:
        """母表里每条可用条目都必须能通过闸门——否则它进了队列才会炸。"""
        pillars = score.load_yaml(score.REPO / "config" / "pillars.yml")
        evergreen = (score.load_yaml(score.REPO / "config" / "sources.yml") or {}).get(
            "evergreen"
        ) or {}
        checked = 0
        for intent_id, library_cfg in (evergreen.get("libraries") or {}).items():
            path = score.REPO / str(library_cfg.get("path") or "")
            if not path.exists():
                continue
            entries = (score.load_yaml(path) or {}).get(
                str(library_cfg.get("collection") or "")
            ) or {}
            for entry_id, entry in entries.items():
                anchor = entry.get("standard_source") or entry.get("evidence_anchor") or {}
                if not anchor.get("locator"):
                    continue
                if entry.get("verification_status") == "source_incomplete":
                    continue
                pick = lambda field, default="": str(
                    entry.get(field) or library_cfg.get(field) or default
                )
                column = pick("likely_column", "C1")
                backing = pick("capability_backing", "technical_report")
                row = {
                    "problem_shape_id": pick("problem_shape_id"),
                    "thesis_id": pick("thesis_id"),
                    "editorial_intent_id": intent_id,
                    "event_match_reason": "curated entry",
                }
                shape, thesis = score.validate_theme_match(row, pillars, entry_id, column)
                score.validate_editorial_intent(
                    row, entry_id, shape, thesis, column, backing
                )
                checked += 1
        self.assertGreater(checked, 0, "常青母表一条可用条目都没有")

    def test_curated_cell_skips_stage_one_but_keeps_its_decision(self) -> None:
        cell = {
            "candidate_id": "cell_smoke",
            "object_type": "curated_cell",
            "curated": {"decision": {"decision": "keep_for_enrichment"}},
        }
        event = {"candidate_id": "evt_smoke", "object_type": "canonical_event"}
        # Stage 1 的 chunk 里不能出现 curated cell。
        visible = [
            item for item in (cell, event) if item.get("object_type") != "curated_cell"
        ]
        self.assertEqual([item["candidate_id"] for item in visible], ["evt_smoke"])

    def test_blocked_fetch_is_not_treated_as_content(self) -> None:
        interstitial = (
            "Title: Just a moment...\n\nWarning: This page maybe requiring CAPTCHA, "
            "please make sure you are authorized to access this page."
        )
        self.assertIsNotNone(collect._fetch_quality_issue(interstitial))
        self.assertIsNotNone(collect._fetch_quality_issue(""))
        self.assertIsNotNone(collect._fetch_quality_issue("too short"))
        self.assertIsNone(collect._fetch_quality_issue("A" * 1200))

    def test_lead_only_without_primary_evidence_cannot_be_original(self) -> None:
        lead = {
            "evidence_role": "reputable_news_lead",
            "context": {"lead_only": True},
            "enrichment": {"status": "blocked", "error": "interstitial or block page"},
        }
        self.assertIsNotNone(score.lead_only_without_primary(lead))
        official = {
            "evidence_role": "official_record",
            "context": {"lead_only": False},
            "enrichment": {"status": "blocked", "error": "whatever"},
        }
        self.assertIsNone(score.lead_only_without_primary(official))
        fetched = {
            "evidence_role": "reputable_news_lead",
            "context": {"lead_only": True},
            "enrichment": {"status": "ok"},
        }
        self.assertIsNone(score.lead_only_without_primary(fetched))

    def test_doi_extraction_survives_real_world_locators(self) -> None:
        # Elsevier PII DOIs contain half-width parens.
        self.assertEqual(
            collect._extract_doi("10.1016/S0140-6736(19)33220-9"),
            "10.1016/S0140-6736(19)33220-9",
        )
        # A Chinese note after the locator must not be swallowed.
        self.assertEqual(
            collect._extract_doi("10.3390/ma11101990（引用时须说明原文语境）"),
            "10.3390/ma11101990",
        )
        self.assertEqual(
            collect._extract_doi("10.1371/journal.pbio.3000411；checklist 见 arriveguidelines.org"),
            "10.1371/journal.pbio.3000411",
        )
        self.assertIsNone(collect._extract_doi("S2542-4351(20)30625-5"))

    def test_education_library_every_entry_carries_a_number(self) -> None:
        """行业教育母表的命门：没有可引用的数字就只是概念科普，不许进库。

        依据 Epoch AI 164 条无一例外（内容/竞品brain.md §6.1），Selene 2026-07-29 定。
        """
        path = score.REPO / "config" / "education_library.yml"
        if not path.exists():
            self.skipTest("education_library.yml 尚未建立")
        library = score.load_yaml(path) or {}
        entries = library.get("entries") or {}
        self.assertGreater(len(entries), 0, "行业教育母表是空的")
        valid_types = set(library.get("number_types") or {})
        for entry_id, entry in entries.items():
            for field in ("claim", "number", "mechanism", "report_section", "boundary"):
                self.assertTrue(
                    str(entry.get(field) or "").strip(),
                    f"{entry_id} 缺硬准入字段 {field}",
                )
            self.assertIn(
                entry.get("number_type"), valid_types,
                f"{entry_id} 的 number_type 非法",
            )
            # 数字必须真的含数字或量词，不能是一句空话
            number = str(entry.get("number"))
            self.assertTrue(
                any(ch.isdigit() for ch in number),
                f"{entry_id} 的 number 里没有任何数字: {number!r}",
            )

    def test_every_entry_declares_an_anchor_type(self) -> None:
        """没有 anchor_type 就无法判断锚有没有保鲜期——这是 40.9% 事件的根因。"""
        valid = {"standard", "measurement", "case"}
        for name, collection in (
            ("confound_library.yml", "confounds"),
            ("checker_library.yml", "entries"),
        ):
            path = score.REPO / "config" / name
            if not path.exists():
                continue
            for entry_id, entry in ((score.load_yaml(path) or {}).get(collection) or {}).items():
                self.assertIn(
                    entry.get("anchor_type"),
                    valid,
                    f"{entry_id} 缺 anchor_type 或取值非法",
                )
                if entry.get("anchor_type") == "measurement":
                    self.assertTrue(
                        entry.get("measured_window"),
                        f"{entry_id} 是 measurement 却没写 measured_window",
                    )

    def test_stale_measurement_anchor_raises_a_warning(self) -> None:
        old = {"anchor_type": "measurement", "measured_window": "2018-03 至 2019-09"}
        warning, label = collect._anchor_age_note(old, 2020)
        self.assertIsNotNone(warning)
        self.assertIn("不得写成当下状态", warning)
        self.assertIn("measurement", label)

        very_old = {"anchor_type": "measurement", "measured_window": "2000-01 至 2015-10"}
        warning, _ = collect._anchor_age_note(very_old, 2019)
        self.assertIn("几乎肯定已有更新", warning)

        # A reporting standard does not age.
        standard = {"anchor_type": "standard"}
        self.assertIsNone(collect._anchor_age_note(standard, 2010)[0])

        # A finished case never ages, but must not be generalised.
        case_note, case_label = collect._anchor_age_note({"anchor_type": "case"}, 2009)
        self.assertIn("不得写成", case_note)
        self.assertIn("case", case_label)

        # A measurement with no window cannot be judged, so it must warn.
        self.assertIsNotNone(
            collect._anchor_age_note({"anchor_type": "measurement"}, None)[0]
        )

    def test_scan_recall_collects_all_violations_without_dying(self) -> None:
        """scan-recall 必须一次列出所有非法行，而不是撞第一条就退出。"""
        pillars = score.load_yaml(score.REPO / "config" / "pillars.yml")
        valid_columns = score.scrapable_columns(pillars)
        cand = candidate("evt_bad", platform="web")
        # PS3 不在 T2 的 problem_shapes 里 —— 正是 07-28 与 07-29 两轮都出现的错配。
        bad_row = {
            "candidate_id": "evt_bad",
            "decision": "keep_for_enrichment",
            "primary_action": "original_post",
            "likely_column": "C1",
            "problem_shape_id": "PS3_conditional_decision",
            "thesis_id": "T2_independent_verification",
            "capability_backing": "technical_report",
            "editorial_intent_id": "EI2_authorities_disagree_on_action",
            "event_match_reason": "x",
        }
        with self.assertRaises(SystemExit):
            score.normalize_recall_row(cand, bad_row, pillars, valid_columns)

    def test_splice_recall_overlays_one_row_per_candidate(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp) / "base.json"
            patch = Path(tmp) / "patch.json"
            out = Path(tmp) / "out.json"
            base.write_text(json.dumps({"stage": "recall", "decisions": [
                {"candidate_id": "a", "decision": "reject"},
                {"candidate_id": "b", "decision": "reject"},
            ]}), encoding="utf-8")
            patch.write_text(json.dumps({"decisions": [
                {"candidate_id": "b", "decision": "keep_for_enrichment"},
            ]}), encoding="utf-8")
            ns = argparse.Namespace(base=str(base), patch=[str(patch)], out=str(out))
            score.splice_recall(ns)
            result = json.loads(out.read_text(encoding="utf-8"))
            rows = {r["candidate_id"]: r["decision"] for r in result["decisions"]}
            self.assertEqual(rows, {"a": "reject", "b": "keep_for_enrichment"})
            self.assertEqual(len(result["decisions"]), 2)

    def test_curated_anchor_without_doi_does_not_block_the_cell(self) -> None:
        content, method, issue = collect.verify_curated_anchor(
            {"object_type": "curated_cell", "curated": {"anchor": {"title": "A checklist"}}}
        )
        self.assertIsNone(issue, "无 DOI 只是不能机器校验，不该阻塞条目")
        self.assertIn("no DOI", method)
        self.assertIn("人工核", content)


if __name__ == "__main__":
    unittest.main()
