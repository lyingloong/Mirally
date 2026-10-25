"""Real SQLite grants and bounded filesystem effects of the basic capability pack."""
from pathlib import Path
import hashlib
import os
import subprocess
import tempfile
import unittest
import zipfile

from mirally.framework import FrameworkError, FrameworkService


class BasicCapabilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mirally 能力 test ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "product home"
        self.service = FrameworkService(self.root, self.home)
        self.addCleanup(lambda: self.service.close())
        self.external = self.root / "用户授权资料"
        self.external.mkdir()
        (self.external / "资料.txt").write_text("第一行\n明确授权的内容 needle\n", encoding="utf-8")
        self.outside = self.root / "outside.txt"
        self.outside.write_text("不属于授权文件夹", encoding="utf-8")

    def call(self, method, **params):
        return self.service.call(method, params)

    def configure(self, **patch):
        revision = self.call("capabilities.get")["revision"]
        return self.call("capabilities.configure", expected_revision=revision, patch=patch)

    def granted(self):
        self.configure(read_roots=[str(self.external)])
        return next(row for row in self.call("workspace.roots")["roots"] if row["id"] != "workspace")

    def test_default_denies_external_access_and_writes_until_explicit_grants(self):
        config = self.call("capabilities.get")
        self.assertFalse(config["web_enabled"])
        self.assertFalse(config["workspace_write_enabled"])
        self.assertEqual([row["id"] for row in self.call("workspace.roots")["roots"]], ["workspace"])
        with self.assertRaisesRegex(FrameworkError, "超出授权"):
            self.call("workspace.read", path=str(self.outside))
        with self.assertRaisesRegex(FrameworkError, "尚未开启"):
            self.call("internal.workspace.write", path="result.txt", content="未经设置开启")
        self.assertFalse((self.home / "workspace/result.txt").exists())

    def test_readonly_grants_support_real_list_read_search_but_not_path_escape(self):
        grant = self.granted()
        listed = self.call("workspace.list", root_id=grant["id"])
        self.assertEqual([row["name"] for row in listed["entries"]], ["资料.txt"])
        read = self.call("workspace.read", root_id=grant["id"], path="资料.txt")
        self.assertIn("明确授权", read["text"])
        self.assertEqual(read["sha256"], hashlib.sha256((self.external / "资料.txt").read_bytes()).hexdigest())
        found = self.call("workspace.search", root_id=grant["id"], query="NEEDLE")
        self.assertEqual(found["matches"][0]["line"], 2)
        self.assertEqual(found["matches"][0]["path"], "资料.txt")
        for path in ("../outside.txt", str(self.outside), str(self.root / "用户授权资料-sibling/file.txt")):
            with self.subTest(path=path), self.assertRaisesRegex(FrameworkError, "超出授权"):
                self.call("workspace.read", root_id=grant["id"], path=path)
        with self.assertRaises(FrameworkError):
            self.call("workspace.read", root_id="invented", path="资料.txt")

    def test_settings_and_root_ids_persist_and_withdrawn_roots_stop_reads(self):
        grant = self.granted()
        changed = self.configure(workspace_write_enabled=True)
        self.service.close()
        self.service = FrameworkService(self.root, self.home)
        self.assertEqual(self.call("capabilities.get"), changed)
        self.assertEqual(self.call("workspace.roots")["roots"][1], grant)
        self.configure(read_roots=[])
        with self.assertRaisesRegex(FrameworkError, "授权文件夹无效"):
            self.call("workspace.read", root_id=grant["id"], path="资料.txt")

    def test_write_is_workspace_only_and_overwrite_requires_current_read_hash(self):
        self.configure(workspace_write_enabled=True)
        created = self.call("internal.workspace.write", path="子目录/result.txt", content="初稿")
        file = self.home / "workspace/子目录/result.txt"
        self.assertEqual(file.read_text(encoding="utf-8"), "初稿")
        self.assertEqual(created["bytes"], len("初稿".encode("utf-8")))
        for params in ({}, {"expected_sha256": "0" * 64}):
            with self.subTest(params=params), self.assertRaisesRegex(FrameworkError, "内容指纹"):
                self.call("internal.workspace.write", path="子目录/result.txt", content="错误覆盖", **params)
        previous = self.call("workspace.read", path="子目录/result.txt")["sha256"]
        file.write_text("用户在读取后自行修改", encoding="utf-8")
        with self.assertRaisesRegex(FrameworkError, "内容指纹"):
            self.call("internal.workspace.write", path="子目录/result.txt", content="陈旧结果", expected_sha256=previous)
        current = self.call("workspace.read", path="子目录/result.txt")["sha256"]
        final = self.call("internal.workspace.write", path="子目录/result.txt", content="基于最新内容的修改", expected_sha256=current)
        self.assertEqual(final["sha256"], hashlib.sha256(file.read_bytes()).hexdigest())
        for path in ("../outside.txt", str(self.external / "资料.txt")):
            with self.subTest(path=path), self.assertRaisesRegex(FrameworkError, "超出授权"):
                self.call("internal.workspace.write", path=path, content="越界")
        self.configure(workspace_write_enabled=False)
        with self.assertRaisesRegex(FrameworkError, "尚未开启"):
            self.call("internal.workspace.write", path="after-revoke.txt", content="迟到写入")
        self.assertFalse((self.home / "workspace/after-revoke.txt").exists())
        self.assertEqual(list(file.parent.glob("*.tmp")), [])

    def test_configuration_rejects_stale_revisions_bad_types_and_unknown_connections(self):
        revision = self.call("capabilities.get")["revision"]
        self.configure(web_enabled=False)
        with self.assertRaisesRegex(FrameworkError, "刷新"):
            self.call("capabilities.configure", expected_revision=revision, patch={"web_enabled": True})
        for patch in ({"workspace_write_enabled": 1}, {"web_enabled": "yes"}, {"read_roots": [str(self.outside)]},
                      {"read_roots": [str(self.external)] * 21}, {"unknown": True},
                      {"web_enabled": True, "search_connection": "missing"}):
            with self.subTest(patch=patch), self.assertRaises(FrameworkError):
                self.call("capabilities.configure", expected_revision=self.call("capabilities.get")["revision"], patch=patch)
        self.assertFalse(self.call("capabilities.get")["web_enabled"])

    def test_file_and_response_work_is_bounded_and_partial_extraction_remains_explicit(self):
        grant = self.granted()
        (self.external / "large.txt").write_bytes(b"x" * (8 * 1024 * 1024 + 1))
        with self.assertRaisesRegex(FrameworkError, "8 MiB"):
            self.call("workspace.read", root_id=grant["id"], path="large.txt")
        self.configure(workspace_write_enabled=True)
        with self.assertRaisesRegex(FrameworkError, "1 MiB"):
            self.call("internal.workspace.write", path="too-large.txt", content="中" * 400000)
        for number in range(230):
            (self.external / (str(number) + ".txt")).write_text("needle\n" * 2, encoding="utf-8")
        self.assertEqual(len(self.call("workspace.list", root_id=grant["id"])["entries"]), 200)
        search = self.call("workspace.search", root_id=grant["id"], query="needle")
        self.assertEqual(len(search["matches"]), 50)
        self.assertTrue(search["truncated"])

        with zipfile.ZipFile(self.external / "工作表.xlsx", "w") as archive:
            archive.writestr("xl/sharedStrings.xml", '<sst><si><t>真实共享文字</t></si></sst>')
        extracted = self.call("workspace.read", root_id=grant["id"], path="工作表.xlsx")
        self.assertEqual(extracted["status"], "partial")
        self.assertIn("真实共享文字", extracted["text"])
        self.assertIn("共享字符串索引", extracted["note"])

    def test_search_subdirectory_hits_can_be_read_with_the_same_root_without_ambiguous_names(self):
        grant = self.granted()
        (self.external / "docs").mkdir()
        (self.external / "same.txt").write_text("错误的根文件", encoding="utf-8")
        (self.external / "docs/same.txt").write_text("实际搜索目标 needle", encoding="utf-8")
        search = self.call("workspace.search", root_id=grant["id"], path="docs", query="needle")
        hit = search["matches"][0]
        self.assertEqual(Path(hit["path"]), Path("docs/same.txt"))
        self.assertEqual(self.call("workspace.read", root_id=grant["id"], path=hit["path"])["text"], "实际搜索目标 needle")

    def test_last_file_hit_limit_reports_partial_results(self):
        workspace=self.home / "workspace"
        workspace.mkdir(exist_ok=True)
        (workspace / "matches.txt").write_text("needle\n"*51,encoding="utf-8")
        result=self.call("workspace.search",query="needle")
        self.assertEqual(len(result["matches"]),50)
        self.assertTrue(result["truncated"])

    def directory_link(self, target, link):
        try:
            os.symlink(target, link, target_is_directory=True)
        except OSError as error:
            if os.name != "nt":
                self.skipTest("Creating directory links unavailable: " + str(error))
            completed = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], capture_output=True)
            if completed.returncode:
                self.skipTest("Creating directory junction unavailable")

    def test_explicit_and_nested_symlinks_never_expand_a_grant(self):
        link = self.root / "linked root"
        self.directory_link(self.external, link)
        with self.assertRaisesRegex(FrameworkError, "符号链接|目录联接"):
            self.configure(read_roots=[str(link)])
        grant = self.granted()
        nested = self.external / "outside-link"
        self.directory_link(self.root, nested)
        with self.assertRaisesRegex(FrameworkError, "超出授权|符号链接|目录联接"):
            self.call("workspace.read", root_id=grant["id"], path="outside-link/outside.txt")
        self.assertNotIn("outside-link", [row["name"] for row in self.call("workspace.list", root_id=grant["id"])["entries"]])
        self.assertFalse(self.call("workspace.search", root_id=grant["id"], query="不属于")["matches"])


if __name__ == "__main__":
    unittest.main()
