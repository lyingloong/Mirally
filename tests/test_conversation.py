import json
import tempfile
import unittest
from pathlib import Path

from mirally.conversation import ConversationStore
from mirally.core.execution import TaskResult


class ConversationTests(unittest.TestCase):
    def test_restart_resumes_context_and_marks_unfinished_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            memory = ConversationStore(path)
            memory.begin("one", "整理资料")
            memory.note("one", "保留原文件")
            memory.finish("one", "completed", "生成 D:/reports/week.pdf")
            memory.begin("two", "把刚才的结果转成图片")
            restored = ConversationStore(path)
            previous = restored.context()["previous_tasks"]
            self.assertEqual(previous[0]["inputs"], ["保留原文件"])
            self.assertIn("D:/reports/week.pdf", previous[0]["result"])
            self.assertEqual(previous[1]["status"], "interrupted")
            self.assertEqual(restored.snapshot()["id"], memory.snapshot()["id"])

    def test_new_select_and_clear_are_isolated(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = ConversationStore(Path(directory))
            memory.begin("one", "first")
            memory.finish("one", "completed", "result")
            first = memory.snapshot()["id"]
            memory.new()
            second = memory.snapshot()["id"]
            self.assertNotEqual(first, second)
            self.assertFalse(memory.context()["previous_tasks"])
            self.assertEqual(len(memory.sessions()), 2)
            memory.select(first)
            self.assertEqual(memory.context()["previous_tasks"][0]["request"], "first")
            memory.clear()
            self.assertFalse(ConversationStore(Path(directory)).context()["previous_tasks"])
            self.assertEqual(len(memory.sessions()), 2)
            with self.assertRaises(ValueError): memory.select("../../outside")

    def test_context_is_bounded_keeps_latest_and_redacts_recognizable_secrets(self):
        memory = ConversationStore()
        for index in range(45):
            memory.begin(str(index), f"task {index} " + "r" * 3000)
            for _ in range(6): memory.note(str(index), "API_KEY=abcdefghijk " + "n" * 2000)
            memory.finish(str(index), "completed", "result " * 600)
        context = memory.context()
        serialized = json.dumps(context, ensure_ascii=False)
        self.assertLess(len(serialized), 14500)
        self.assertIn("task 44", context["previous_tasks"][-1]["request"])
        self.assertNotIn("abcdefghijk", json.dumps(memory.snapshot()))
        self.assertEqual(len(memory.snapshot()["tasks"]), 40)
        self.assertTrue(context["older_summary"])

    def test_corrupt_session_is_preserved_without_blocking_new_work(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            memory = ConversationStore(path)
            memory.new()
            session = path / (memory.snapshot()["id"] + ".json")
            session.write_text("broken", encoding="utf-8")
            restored = ConversationStore(path)
            self.assertTrue(restored.error)
            restored.begin("new", "new work")
            self.assertEqual(session.read_text(encoding="utf-8"), "broken")

    def test_latest_answer_is_kept_verbatim_for_a_followup_operation(self):
        memory = ConversationStore()
        answer = "邮件正文：\n\n" + "需要粘贴的内容。" * 2200 + "\n结尾不能被截断。"
        memory.begin("draft", "写一封邮件")
        memory.finish("draft", "completed", answer)
        context = memory.context()
        self.assertEqual(context["previous_tasks"][-1]["result"], answer)
        self.assertTrue(context["latest_result_over_budget"])

    def test_partial_result_is_not_relabelled_as_completed(self):
        memory = ConversationStore()
        memory.begin("task", "处理两份文件")
        memory.finish("task", "partial", "仅保存了第一份文件。")
        self.assertEqual(memory.context()["previous_tasks"][-1]["status"], "partial")

    def test_result_references_survive_restart_without_internal_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            memory = ConversationStore(Path(directory))
            memory.begin("task", "生成文件")
            result = TaskResult("partial", "第一份已保存。", (
                {"path": "D:/reports/one.txt", "source": "native_report", "verified": "exists"},
            ), "native_error", {"event_log": "D:/logs/mirally-events.jsonl",
                                "prompt": "PRIVATE_DIAGNOSTIC"})
            memory.finish("task", result.status, result.message, execution_result=result)
            restored = ConversationStore(Path(directory))
            record = restored.snapshot()["tasks"][-1]
            self.assertEqual(record["reason"], "native_error")
            self.assertEqual(record["event_log"], "D:/logs/mirally-events.jsonl")
            self.assertEqual(restored.context()["previous_tasks"][-1]["artifacts"], list(result.artifacts))
            self.assertNotIn("PRIVATE_DIAGNOSTIC", json.dumps(restored.snapshot()))


if __name__ == "__main__":
    unittest.main()
