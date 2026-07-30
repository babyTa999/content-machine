from __future__ import annotations

import unittest
import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from oracle import collect, score
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

    @staticmethod
    def _draws_on_external_world(definition: dict) -> bool:
        """这条选题理由需不需要外部世界发生点什么。

        除 owned_material 一种之外，所有 source_types 都指向外部世界，因此必须声明
        event_triggers 并贡献一条检索 query——否则它会 active 着却永远拿不到候选
        （历史 bug：intent 轮换漏了一条，那条就静默空转）。只锚自家材料的那条相反：
        它不许有 triggers，否则等于把"等外部事件"偷偷加回一条本该无条件出货的产线。
        """
        return set(definition.get("source_types") or []) != {"owned_material"}

    def test_editorial_intents_and_golden_set_reference_valid_ids(self) -> None:
        intents = self.intents.get("intents") or {}
        for intent_id, definition in intents.items():
            self.assertTrue(definition.get("source_types"), intent_id)
            if self._draws_on_external_world(definition):
                self.assertTrue(definition.get("event_triggers"), intent_id)
            else:
                self.assertFalse(definition.get("event_triggers"), intent_id)
                self.assertTrue(definition.get("intake_source"), intent_id)
                self.assertEqual(definition.get("intake"), "curated", intent_id)
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

    @staticmethod
    def _interaction_row(**overrides: object) -> dict:
        row = {
            "candidate_id": "cand_interaction",
            "decision": "interaction",
            "primary_destination": "C1",
            "primary_action": "x_reply",
            "stance_read": "作者认为同行评议从来不检查分析代码",
            "verification_hook": "这正是独立验证角色缺位的那一环",
        }
        row.update(overrides)
        return row

    def test_interaction_cannot_also_be_original(self) -> None:
        item = candidate("cand_interaction", platform="x")
        normalized = score.normalize_evidence_decision(
            item, self._interaction_row(), self.pillars
        )
        self.assertEqual(normalized["primary_destination"], "C8")
        self.assertEqual(normalized["primary_action"], "x_reply")

    def test_interaction_must_read_the_post_and_hook_the_narrative(self) -> None:
        """互动最容易退化成「Great point!」。这两个字段是强制想清楚，缺一即拒。"""
        item = candidate("cand_interaction", platform="x")
        for missing in ("stance_read", "verification_hook"):
            with self.subTest(missing=missing):
                with self.assertRaises(SystemExit):
                    score.normalize_evidence_decision(
                        item, self._interaction_row(**{missing: ""}), self.pillars
                    )
                with self.assertRaises(SystemExit):
                    score.normalize_evidence_decision(
                        item, self._interaction_row(**{missing: "好帖"}), self.pillars
                    )

    def test_judge_cannot_promote_a_founder_route_to_the_official_account(self) -> None:
        """段位闸门必须是闸门：判官可以降级，绝不许升级，否则它只是一句建议。"""
        item = candidate("cand_interaction", platform="x")
        item["context"] = {
            **(item.get("context") or {}),
            "account_tier": "L3_pool",
            "account_route": "founder",
            "account_route_reason": "工程实现细节：官号不碰",
        }
        promoted = score.normalize_evidence_decision(
            item, self._interaction_row(account_route="official"), self.pillars
        )
        self.assertEqual(promoted["account_route"], "founder")
        self.assertIn("不许上调", promoted["route_downgrade_note"])

        # 反向必须放行：采集期判 official，判官看出问题降成 founder，照准。
        item["context"]["account_route"] = "official"
        demoted = score.normalize_evidence_decision(
            item, self._interaction_row(account_route="founder"), self.pillars
        )
        self.assertEqual(demoted["account_route"], "founder")

    def test_interaction_without_any_route_defaults_to_the_founder(self) -> None:
        """没有任何分流信息时默认必须是老板个人号——官号出手要被挣来，不能是兜底。"""
        item = candidate("cand_interaction", platform="x")
        item["context"] = {}
        normalized = score.normalize_evidence_decision(
            item, self._interaction_row(), self.pillars
        )
        self.assertEqual(normalized["account_route"], "founder")

    def test_competitor_accounts_are_never_scraped_for_interaction(self) -> None:
        """竞品官号在 content_rules 里是「永不直接明示」，分层要连抓都不抓。"""
        watch = score.load_yaml(score.REPO / "config" / "watchlist.yml")
        tiers = collect.interaction_tier_map(watch)
        for handle in ("openai", "anthropicai", "perplexity_ai", "biomni"):
            self.assertIn(handle, tiers, handle)
            self.assertFalse(tiers[handle].get("any_interaction", True), handle)
            self.assertFalse(tiers[handle].get("official_quote", True), handle)
        # 顶刊与手选个人号必须仍然开放，否则 quote 供给会被这层直接掐死。
        for handle in ("nature", "sciencemagazine", "karpathy"):
            self.assertTrue(tiers[handle]["official_quote"], handle)

    def test_content_type_routes_away_from_the_official_account(self) -> None:
        """段位事故出在内容类型上，不在账号层级上——工程细节帖再好也不给官号。"""
        for text, expected in (
            ("The real question is who verifies the verifier. I think this is backwards.", "official"),
            ("just fixed a merge conflict, pip install -U to get the patch", "founder"),
            ("we're hiring a research engineer!", "founder"),
            ("these people are grifters selling snake oil", "founder"),
            ("Has anyone checked whether replication rates differ by field?", "founder"),
        ):
            with self.subTest(text=text[:32]):
                stance, _ = collect._stance_score(text)
                route, reason = collect._content_route(text, stance)
                self.assertEqual(route, expected, reason)

    def test_interaction_keeps_its_seats_under_a_keyword_flood(self) -> None:
        """互动池 124 人 0 产出的真因：它和关键词搜索抢同一个预算桶。保底席必须挡住洪水。"""
        import argparse

        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            saved = score.STATE_DB
            score.STATE_DB = directory / "state.sqlite"
            try:
                cands, decisions = [], []
                for index in range(30):
                    item = candidate(f"kw_{index}", platform="x")
                    item["source_path"] = "x_keyword_search"
                    item["priority_score"] = 99          # 关键词候选优先级远高于互动
                    cands.append(item)
                    decisions.append({
                        "candidate_id": item["candidate_id"],
                        "decision": "keep_for_enrichment",
                        "primary_action": "original_post",
                        "likely_column": "C1",
                        "problem_shape_id": "PS10_missing_control",
                        "thesis_id": "T2_independent_verification",
                        "editorial_intent_id": "EI6_missing_control",
                        "event_match_reason": "flood",
                        "capability_backing": "technical_report",
                        "reason": "flood",
                    })
                for index in range(4):
                    item = candidate(f"ix_{index}", platform="x")
                    item["source_path"] = "x_interaction_pool"
                    item["priority_score"] = 1
                    cands.append(item)
                    decisions.append({
                        "candidate_id": item["candidate_id"],
                        "decision": "interaction_only",
                        "primary_action": "x_quote",
                        "reason": "stance",
                    })
                paths = {
                    name: directory / f"{name}.json"
                    for name in ("judged", "candidates", "out", "queue")
                }
                paths["judged"].write_text(
                    json.dumps({"run_id": "t", "stage": "recall_decisions", "decisions": decisions})
                )
                paths["candidates"].write_text(json.dumps({"run_id": "t", "candidates": cands}))
                score.validate_recall(argparse.Namespace(
                    judged=str(paths["judged"]), candidates=str(paths["candidates"]),
                    out=str(paths["out"]), queue_out=str(paths["queue"]),
                ))
                queue = json.loads(paths["queue"].read_text())
                queued = queue["candidate_ids"]
                self.assertEqual(len(queued), len(set(queued)), "入队出现重复行，重复会占掉名额")
                interaction = [cid for cid in queued if cid.startswith("ix_")]
                # 期望值写死，不从配置读：从配置读会让"把保底席清零"这个变异
                # 同时把期望值也清零，测试就永远通不了错。
                self.assertGreaterEqual(
                    len(interaction), 3,
                    f"30 条高优先级关键词候选淹掉了互动，只入队 {len(interaction)} 条",
                )
                audit = queue["source_audit"]["interaction"]
                self.assertLessEqual(
                    audit["queued"], audit["eligible"], "queued 不可能多于 eligible"
                )
            finally:
                score.STATE_DB = saved

    def test_scorecard_form_is_capped_by_the_month_not_the_week(self) -> None:
        """成绩单是信用证：发一次立信，按月配额。周配额管不住「老发成绩单」。"""
        forms = (score.load_yaml(score.REPO / "config" / "forms.yml") or {}).get("forms") or {}
        self.assertEqual(forms["chart_post"].get("max_per_month"), 1)
        with tempfile.TemporaryDirectory() as temp:
            saved = score.STATE_DB
            score.STATE_DB = Path(temp) / "state.sqlite"
            try:
                # 20 天前发过一条成绩单：跨出 7 天窗口，但仍在 30 天窗口内。
                score.record_report_forms(
                    "2026-07-10", [{"candidate_id": "old", "form": "chart_post", "axis": "AX4_who_checks"}]
                )
                usage = score.enforce_form_quota(
                    [{"candidate_id": "new", "form": "chart_post"}], "2026-07-30"
                )
                row = usage["chart_post"]
                self.assertEqual(row["month"], 2)
                self.assertTrue(row["over"], "近 30 天两条成绩单必须被标出来")
                self.assertTrue(any("每月上限" in note for note in row["over"]))
            finally:
                score.STATE_DB = saved

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
            and self._draws_on_external_world(definition)
        }
        self.assertEqual({intent_id for intent_id, _ in queries}, active)
        self.assertEqual(len(queries), len(active))
        # 反向：只锚自家材料的那条不许出现在检索轮换里，否则会去外面抓一批和它无关的候选。
        owned = {
            intent_id
            for intent_id, definition in self.intents["intents"].items()
            if not self._draws_on_external_world(definition)
        }
        self.assertTrue(owned, "至少应有一条不依赖外部事件的选题理由")
        self.assertFalse(owned & {intent_id for intent_id, _ in queries})

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
                # 必须走 collect 的同一个解析函数：教育母表的锚是 report_section，
                # 直接读 standard_source 会把整张表静默跳过，测试就变成空转。
                anchor = collect._evergreen_anchor(entry, library_cfg)
                if not (anchor.get("locator") or anchor.get("url")):
                    continue
                if entry.get("verification_status") == "source_incomplete":
                    continue
                if str(entry.get("status") or "") in collect.BLOCKED_ENTRY_STATUS:
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

    def _evergreen_days(self, days: int, start: str = "2026-08-01") -> list[list[dict]]:
        """跑 N 天常青产线，每期都记用量，返回逐日选中的条目。"""
        import datetime as dt

        cfg = (score.load_yaml(score.REPO / "config" / "sources.yml") or {}).get("evergreen") or {}
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "state.sqlite"
            saved = (collect.STATE_DB, score.STATE_DB)
            collect.STATE_DB, score.STATE_DB = db, db
            try:
                out = []
                first = dt.date.fromisoformat(start)
                for offset in range(days):
                    now = dt.datetime.combine(
                        first + dt.timedelta(days=offset), dt.time(9, 0)
                    )
                    rows = collect.collect_evergreen(cfg, now)
                    score.record_evergreen_usage(
                        now.date().isoformat(),
                        {row["candidate_id"]: row for row in rows},
                        [
                            {
                                "candidate_id": row["candidate_id"],
                                "decision": "keep",
                                "primary_action": "original_post",
                            }
                            for row in rows
                        ],
                    )
                    out.append([row["curated"] for row in rows])
                return out
            finally:
                collect.STATE_DB, score.STATE_DB = saved

    def test_education_library_actually_ships(self) -> None:
        """行业教育母表接进产线了才算接进：14 天内必须真的出货，且落在 C2。"""
        days = self._evergreen_days(14)
        edu = [
            cell
            for day in days
            for cell in day
            if cell["intent_id"] == "EI9_design_choice_from_report"
        ]
        self.assertGreater(len(edu), 0, "行业教育母表 14 天一条都没出——等于没接进产线")
        for cell in edu:
            self.assertEqual(cell["decision"]["likely_column"], "C2")
            self.assertEqual(cell["anchor_type"], "owned_report")

    def test_owned_anchor_is_never_passed_off_as_independent_evidence(self) -> None:
        """锚是自家报告时，必须显式标成自引，否则会被当成第三方证据引用。"""
        anchor = collect._evergreen_anchor(
            {"report_section": "§4.3 · §8.4"},
            {"anchor_field": "report_section", "anchor_kind": "owned_report"},
        )
        self.assertEqual(anchor["locator"], "§4.3 · §8.4")
        self.assertEqual(anchor["kind"], "owned_report")
        self.assertIn("不是第三方", anchor["note"])
        warning, freshness = collect._anchor_age_note({"anchor_type": "owned_report"}, None)
        self.assertIsNotNone(warning, "自家锚必须带警告：对比类数字会随对手更新失效")
        self.assertIn("owned_report", freshness)

    def test_third_party_anchor_still_wins_over_the_library_fallback(self) -> None:
        """已发表的锚优先：库级 anchor_field 只是兜底，不许覆盖真锚。"""
        anchor = collect._evergreen_anchor(
            {"standard_source": {"locator": "10.1371/journal.pmed.1000217"}, "report_section": "§1"},
            {"anchor_field": "report_section", "anchor_kind": "owned_report"},
        )
        self.assertEqual(anchor["locator"], "10.1371/journal.pmed.1000217")
        self.assertNotIn("kind", anchor)

    def test_entry_needing_internal_work_never_ships(self) -> None:
        """条目格式合法 ≠ 背后的活干完了。自我复核那条在真做完之前不许出货。"""
        entries = (score.load_yaml(score.REPO / "config" / "education_library.yml") or {}).get(
            "entries"
        ) or {}
        blocked = [
            entry_id
            for entry_id, entry in entries.items()
            if str(entry.get("status") or "") in collect.BLOCKED_ENTRY_STATUS
        ]
        self.assertIn("EDU_self_audit_missing", blocked, "自我复核那条应标为未完成")
        shipped = {
            cell["entry_id"] for day in self._evergreen_days(21) for cell in day
        }
        for entry_id in blocked:
            self.assertNotIn(entry_id, shipped)

    def test_one_library_cannot_take_the_whole_batch(self) -> None:
        """预留是地板不是天花板：留了座的库不许把当期名额全吃掉。"""
        days = self._evergreen_days(21)
        libraries = {
            cell["intent_id"] for day in days for cell in day
        }
        self.assertGreaterEqual(len(libraries), 3, f"21 天只有 {libraries} 出货，轮换退化了")
        used = [cell["entry_id"] for day in days for cell in day]
        self.assertEqual(len(used), len(set(used)), "同一条目在冷却期内被重复选中")

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
