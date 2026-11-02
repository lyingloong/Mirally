"""Offline tests for tools/dev_workflow/progress_page.py (dev-0023).

Every fixture is a throwaway temporary workdir holding a minimal
``output/agent-workflow`` tree. Nothing here reads the real workflow state, the
user home, credentials or ``.agent-teams/``.
"""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from tools.dev_workflow import agent_workflow as wf  # noqa: E402
from tools.dev_workflow import progress_page as pp  # noqa: E402

FIXED_NOW = "2026-11-02T08:00:00+00:00"


def _point(point_id: str, *, source="user", title=None, status="queued", **extra):
    """Build a point with the canonical new_point() field set."""
    state = {"points": [], "next_id": int(point_id.split("-")[1])}
    point = wf.new_point(state, source=source, title=title or f"开发点 {point_id}")
    point["id"] = point_id
    point["status"] = status
    point.update(extra)
    return point


class ProgressPageTests(unittest.TestCase):
    def setUp(self):
        # The generator refuses a workdir outside the repository, so fixtures are
        # created under the gitignored output/ tree. Sandboxed checkouts may only
        # allow writes elsewhere, so honour the explicit fixture-root override.
        fixture_root = pp.fixture_root(ROOT)
        fixture_root.mkdir(parents=True, exist_ok=True)
        self._temp = tempfile.TemporaryDirectory(dir=fixture_root)
        self.addCleanup(self._temp.cleanup)
        self.workdir = Path(self._temp.name) / "agent-workflow"
        (self.workdir / "specs").mkdir(parents=True)
        (self.workdir / "verification").mkdir()
        self.state = {"version": 1, "next_id": 1, "points": [], "updated_at": FIXED_NOW}

    # -- fixture helpers -------------------------------------------------
    def write_state(self, points, config=None):
        self.state["points"] = points
        (self.workdir / "state.json").write_text(json.dumps(self.state, ensure_ascii=False), encoding="utf-8")
        (self.workdir / "config.json").write_text(
            json.dumps(config or {"concurrency": 3}, ensure_ascii=False), encoding="utf-8")

    def spec(self, point_id, body):
        path = self.workdir / "specs" / f"{point_id}.md"
        path.write_text(body, encoding="utf-8")
        # absolute: the fixture workdir may live outside the repo in sandboxed runs
        return str(path)

    def verification(self, name, body):
        (self.workdir / "verification" / f"{name}.md").write_text(body, encoding="utf-8")

    def build(self, **kwargs):
        return pp.build_page(workdir=self.workdir, root=ROOT, now=FIXED_NOW,
                             head="abc1234", **kwargs)

    def cards(self, model):
        for group in model["groups"].values():
            for source in ("user", "agent"):
                yield from group[source]

    # -- 1. empty state --------------------------------------------------
    def test_empty_state_renders_all_groups_with_empty_copy(self):
        self.write_state([])
        page = self.build()
        self.assertEqual(page["model"]["summary"]["total"], 0)
        html = page["html"]
        for group in ("待你决定", "进行中", "已完成", "已关闭"):
            self.assertIn(group, html)
        self.assertIn("（暂无）", html)
        self.assertIn("只读生成物", html)
        self.assertEqual(page["model"]["validate_issues"], [])

    # -- 2. full status coverage ----------------------------------------
    def test_every_status_lands_in_the_expected_group_and_card_has_all_fields(self):
        statuses = {
            "dev-0001": "queued", "dev-0002": "proposed", "dev-0003": "blocked",
            "dev-0004": "implementing", "dev-0005": "verifying", "dev-0006": "reviewing",
            "dev-0007": "awaiting_merge", "dev-0008": "merging", "dev-0009": "done",
            "dev-0010": "rejected",
        }
        points = []
        for point_id, status in statuses.items():
            extra = {"blocked_reason": "quota", "quota_retries": 2} if status == "blocked" else {}
            if status == "awaiting_merge":
                extra["pr"] = 12
            points.append(_point(point_id, status=status,
                                 spec_path=self.spec(point_id, "# t\n\n**目标**：目标句。\n"), **extra))
        self.write_state(points)
        model = self.build()["model"]
        pending = {c["id"] for src in model["groups"]["待你决定"].values() for c in src}
        active = {c["id"] for src in model["groups"]["进行中"].values() for c in src}
        done = {c["id"] for src in model["groups"]["已完成"].values() for c in src}
        closed = {c["id"] for src in model["groups"]["已关闭"].values() for c in src}
        self.assertEqual(pending, {"dev-0002", "dev-0003", "dev-0007"})
        # queued (dev-0001) waits for a slot and is surfaced with the moving work
        self.assertEqual(active, {"dev-0001", "dev-0004", "dev-0005", "dev-0006", "dev-0008"})
        self.assertEqual(done, {"dev-0009"})
        self.assertEqual(closed, {"dev-0010"})
        required = {"id", "title", "source", "risk", "status", "purpose", "changes", "verification",
                    "decisions", "pr", "branch", "head_sha", "owner", "task", "updated_at"}
        for card in self.cards(model):
            self.assertTrue(required.issubset(card.keys()), card["id"])

    def test_done_group_sorts_by_merged_at_descending(self):
        self.write_state([
            _point("dev-0001", status="done", merged_at="2026-11-01T00:00:00+00:00"),
            _point("dev-0002", status="done", merged_at="2026-11-02T00:00:00+00:00"),
        ])
        model = self.build()["model"]
        self.assertEqual([c["id"] for c in model["groups"]["已完成"]["user"]], ["dev-0002", "dev-0001"])

    # -- 3. escaping -----------------------------------------------------
    def test_titles_and_notes_are_html_escaped_and_paths_relative(self):
        point = _point("dev-0001", title="<script>alert(1)</script> & 中文 路径",
                       notes=r"见 C:\Users\someone\secret\file.txt 与 <b>x</b>")
        point["spec_path"] = self.spec("dev-0001", "# <b>标题</b>\n\n**目标**：含 & 与 <i> 的目标。\n")
        self.write_state([point])
        html = self.build()["html"]
        self.assertNotIn("<script>alert(1)</script>", html)
        self.assertIn("&lt;script&gt;", html)
        self.assertNotIn("<b>x</b>", html)
        self.assertNotIn("C:\\Users", html)
        self.assertNotIn("<i>", html)

    # -- 4. config whitelist --------------------------------------------
    def test_unknown_config_keys_and_secrets_never_reach_the_page(self):
        self.write_state([_point("dev-0001")], config={
            "concurrency": 3, "max_auto_agent_in_flight": 1, "max_auto_agent_per_day": 2,
            "merge_queue_exempt": 1, "secret": "SHOULD_NOT_APPEAR", "api_key": "SECRET_TOKEN_XYZ",
        })
        page = self.build()
        self.assertNotIn("SECRET_TOKEN_XYZ", page["html"])
        self.assertNotIn("SHOULD_NOT_APPEAR", page["html"])
        self.assertNotIn("SECRET_TOKEN_XYZ", json.dumps(page["data"], ensure_ascii=False))
        self.assertEqual(set(page["model"]["config"]), set(pp.CONFIG_KEYS))

    def test_secret_inside_quota_fallback_is_filtered(self):
        config = {"concurrency": 3, "quota": {"fallback": {"provider": "p", "model": "m", "api_key": "LEAKED_KEY"}}}
        self.write_state([_point("dev-0001")], config=config)
        page = self.build()
        blob = page["html"] + json.dumps(page["data"], ensure_ascii=False)
        self.assertNotIn("LEAKED_KEY", blob)

    # -- 5. verification parsing ----------------------------------------
    def test_verification_verdict_pass_fail_and_missing(self):
        self.write_state([
            _point("dev-0001", status="verifying"), _point("dev-0002", status="verifying"),
            _point("dev-0003", status="verifying")])
        self.verification("dev-0001", "# dev-0001 独立验证\n\n- 结论：**PASS（验证门）**\n")
        self.verification("dev-0002", "# dev-0002 独立验证\n\n- 结论：**FAIL**；两条 findings\n")
        model = self.build()["model"]
        by_id = {c["id"]: c for c in self.cards(model)}
        self.assertEqual(by_id["dev-0001"]["verification"]["verdict"], "PASS")
        self.assertEqual(by_id["dev-0002"]["verification"]["verdict"], "FAIL")
        self.assertIsNone(by_id["dev-0003"]["verification"]["verdict"])
        self.assertIsNone(by_id["dev-0003"]["verification"]["file"])
        self.assertGreaterEqual(model["summary"]["data_gaps"], 1)

    def test_verification_chinese_wording_is_recognised(self):
        self.write_state([_point("dev-0001", status="verifying")])
        self.verification("dev-0001", "- 结论：**验证通过（见文末）**；CI 两项均已 pass\n")
        model = self.build()["model"]
        self.assertEqual(next(self.cards(model))["verification"]["verdict"], "PASS")

    # -- 6. pending-decision derivation ---------------------------------
    def test_pending_decision_priority_and_spec_decision_count(self):
        points = [
            _point("dev-0001", source="agent", status="proposed"),
            _point("dev-0002", status="blocked", blocked_reason="quota", quota_retries=3),
            _point("dev-0003", status="awaiting_merge", pr=12),
            _point("dev-0004", status="reviewing"),
        ]
        # dev-0004 has BOTH a failing review (rule 4) and an open spec decision
        # section (rule 5); §4.4 lists the spec-decisions rule first.
        points[3]["spec_path"] = self.spec("dev-0004", "# x\n\n**目标**：y\n\n## 12. 待队长决定\n\n1. 甲\n2. 乙\n")
        self.write_state(points)
        self.verification("dev-0004-r1", "- 结论：**FAIL**；F-1 未关闭\n")
        by_id = {c["id"]: c for c in self.cards(self.build()["model"])}
        self.assertEqual(by_id["dev-0001"]["decisions"], ["AI 提案待批准：promote 或 reject"])
        self.assertIn("quota 重试 3 次", by_id["dev-0002"]["decisions"][0])
        self.assertIn("PR #12", by_id["dev-0003"]["decisions"][0])
        self.assertIn("规格列有 2 项待决策", by_id["dev-0004"]["decisions"][0])

    def test_failed_review_surfaces_when_spec_has_no_open_decisions(self):
        point = _point("dev-0004", status="reviewing")
        point["spec_path"] = self.spec("dev-0004", "# x\n\n**目标**：y\n")
        self.write_state([point])
        (self.workdir / "review-dev-0004-pr-comment.md").write_text(
            "审查未通过；findings 摘要一行\n", encoding="utf-8")
        card = next(self.cards(self.build()["model"]))
        self.assertIn("审查未通过", card["decisions"][0])

    def test_spec_pending_decisions_and_manual_decisions_are_merged(self):
        point = _point("dev-0001", status="done", notes="遗留：未覆盖真实数据\n其余正常")
        point["spec_path"] = self.spec("dev-0001", "# x\n\n**目标**：y\n\n## 9. 待用户决定\n\n1. 甲\n2. 乙\n")
        self.write_state([point])
        (self.workdir / "page-decisions.md").write_text("dev-0001: 队长补充的一项决定\n", encoding="utf-8")
        card = next(self.cards(self.build()["model"]))
        self.assertIn("规格列有 2 项待决策", card["decisions"][0])
        self.assertIn("队长补充的一项决定", card["decisions"])

    def test_done_notes_leftovers_become_decisions(self):
        self.write_state([_point("dev-0001", status="done",
                                 notes="遗留：未覆盖真实数据\n未覆盖：并发\nTODO: 补文档\n")])
        card = next(self.cards(self.build()["model"]))
        self.assertEqual(len(card["decisions"]), 2)
        self.assertTrue(all(any(k in d for k in ("遗留", "未覆盖", "TODO")) for d in card["decisions"]))

    # -- 7. offline / --refresh-pr --------------------------------------
    def test_missing_pr_cache_is_not_fatal(self):
        self.write_state([_point("dev-0001", status="awaiting_merge", pr=7)])
        model = self.build()["model"]
        self.assertFalse(model["pr_cache"]["available"])
        self.assertIn("--refresh-pr", model["pr_cache"]["note"])
        card = next(self.cards(model))
        self.assertIsNone(card["pr_url"])
        self.assertTrue(card["changes"]["missing"])

    def test_refresh_pr_without_gh_returns_code_2_and_keeps_existing_cache(self):
        self.write_state([_point("dev-0001")])
        cached = self.workdir / pp.PR_CACHE_NAME
        cached.write_text(json.dumps({"generated_at": "old", "pulls": []}), encoding="utf-8")
        with mock.patch.object(pp.shutil, "which", return_value=None):
            code = pp.main(["--workdir", str(self.workdir), "--refresh-pr"])
        self.assertEqual(code, 2)
        self.assertIn("old", cached.read_text(encoding="utf-8"))

    def test_pr_cache_supplies_changes_and_ci_summary(self):
        self.write_state([_point("dev-0001", status="awaiting_merge", pr=7)])
        (self.workdir / pp.PR_CACHE_NAME).write_text(json.dumps({
            "generated_at": "2026-11-02T07:00:00+00:00",
            "pulls": [{"number": 7, "title": "t", "headRefName": "b", "state": "OPEN", "mergeable": "MERGEABLE",
                       "url": "https://github.com/x/y/pull/7",
                       "files": [{"path": "tools/a.py", "additions": 10, "deletions": 2},
                                 {"path": "tests/b.py", "additions": 5, "deletions": 0}],
                       "statusCheckRollup": [{"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"},
                                             {"state": "PENDING"}]}],
        }), encoding="utf-8")
        model = self.build()["model"]
        card = next(self.cards(model))
        self.assertEqual(card["changes"]["files"], 2)
        self.assertEqual(card["changes"]["additions"], 15)
        self.assertEqual(card["verification"]["ci"], {"pass": 1, "pending": 1, "fail": 1})
        self.assertEqual(card["pr_url"], "https://github.com/x/y/pull/7")

    def test_refresh_pr_writes_cache_from_gh_json(self):
        self.write_state([_point("dev-0001")])
        payload = json.dumps([{"number": 9, "title": "t", "headRefName": "b", "state": "OPEN",
                               "mergeable": "MERGEABLE", "url": "u", "files": [], "statusCheckRollup": []}])

        class Completed:
            returncode = 0
            stdout = payload
            stderr = ""

        def fake_run(*args, **kwargs):
            return Completed()

        # main()/refresh_pr_cache() capture subprocess.run as keyword-only default
        # arguments at import time, so stub __kwdefaults__ as well as the module.
        with mock.patch.object(pp.shutil, "which", return_value="gh"):
            with mock.patch.object(pp.subprocess, "run", fake_run):
                with mock.patch.object(pp._git_head, "__defaults__", (fake_run,)):
                    with mock.patch.object(pp.main, "__kwdefaults__", {"run": fake_run}):
                        with mock.patch.object(pp.refresh_pr_cache, "__kwdefaults__", {"run": fake_run}):
                            code = pp.main(["--workdir", str(self.workdir), "--refresh-pr",
                                            "--now", FIXED_NOW,
                                            "--out", str(Path(self.workdir) / "p.html")])
        self.assertEqual(code, 0)
        cache = json.loads((self.workdir / pp.PR_CACHE_NAME).read_text(encoding="utf-8"))
        self.assertEqual(cache["pulls"][0]["number"], 9)

    # -- 8. --workdir boundary ------------------------------------------
    def test_workdir_outside_repository_is_rejected(self):
        outside = Path(tempfile.gettempdir()).resolve()
        self.assertNotEqual(outside, ROOT)
        self.assertEqual(pp.main(["--workdir", str(outside)]), 2)

    def test_missing_workdir_is_rejected(self):
        self.assertEqual(pp.main(["--workdir", str(ROOT / "output" / "does-not-exist-xyz")]), 2)

    def test_generator_never_reads_agent_teams_or_home(self):
        """The generator must not touch .agent-teams/, .artifacts/ or credentials."""
        self.write_state([_point("dev-0001")])
        touched = []
        real_open = Path.open

        def spy(path_self, *args, **kwargs):
            touched.append(str(path_self))
            return real_open(path_self, *args, **kwargs)

        with mock.patch.object(Path, "open", spy):
            self.build()
        forbidden = [p for p in touched if ".agent-teams" in p or ".artifacts" in p]
        self.assertEqual(forbidden, [])

    # -- 9. determinism --------------------------------------------------
    def test_same_input_produces_identical_html(self):
        self.write_state([_point("dev-0001", status="implementing"),
                          _point("dev-0002", source="agent", status="proposed")])
        first = self.build()
        second = self.build()
        self.assertEqual(first["html"], second["html"])
        self.assertEqual(json.dumps(first["data"], sort_keys=True, ensure_ascii=False),
                         json.dumps(second["data"], sort_keys=True, ensure_ascii=False))

    def test_outputs_are_written_and_state_is_untouched(self):
        self.write_state([_point("dev-0001")])
        state_file = self.workdir / "state.json"
        before = state_file.read_bytes()
        html_out = Path(self.workdir) / "out.html"
        data_out = Path(self.workdir) / "out.json"
        code = pp.main(["--workdir", str(self.workdir), "--now", FIXED_NOW,
                        "--out", str(html_out), "--data", str(data_out)])
        self.assertEqual(code, 0)
        self.assertIn("待你决定", html_out.read_text(encoding="utf-8"))
        self.assertEqual(json.loads(data_out.read_text(encoding="utf-8"))["schema"], pp.SCHEMA)
        self.assertEqual(state_file.read_bytes(), before)

    def test_default_output_paths_land_in_workdir(self):
        self.write_state([_point("dev-0001")])
        code = pp.main(["--workdir", str(self.workdir), "--now", FIXED_NOW])
        self.assertEqual(code, 0)
        self.assertTrue((self.workdir / pp.HTML_NAME).is_file())
        self.assertTrue((self.workdir / pp.DATA_NAME).is_file())


if __name__ == "__main__":
    unittest.main()
