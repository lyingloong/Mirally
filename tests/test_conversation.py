import json
import tempfile
import unittest
from pathlib import Path

from openassist.conversation import ConversationStore


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


if __name__ == "__main__":
    unittest.main()
