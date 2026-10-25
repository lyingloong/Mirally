"""Read-only, indexed legacy records; new archives are independent of old runtimes."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import uuid

MAX_BYTES = 128 * 1024 * 1024
MAX_LINE = 1024 * 1024


def plain(path: Path) -> Path:
    try:
        for part in (path, *path.parents):
            info = part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise ValueError("旧记录路径不能包含符号链接或目录联接")
        return path.resolve(strict=True)
    except OSError as error:
        raise ValueError("旧记录文件已移走或无法读取；尚未归档的记录需要恢复原文件后再显式归档") from error


def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def entries(home: Path) -> list[dict]:
    index = home / "framework/legacy-history.json"
    if not index.exists():
        return []
    if index.stat().st_size > 16 * 1024 * 1024:
        raise ValueError("旧记录索引超过显示上限")
    value = json.loads(plain(index).read_text(encoding="utf-8"))
    if not isinstance(value, list) or len(value) > 10000:
        raise ValueError("旧记录索引格式无效")
    output = []
    for entry in value:
        if not isinstance(entry, dict) or not isinstance(entry.get("path"), str):
            raise ValueError("旧记录索引条目无效")
        sha = entry.get("sha256", "")
        if not isinstance(sha, str) or len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha):
            raise ValueError("旧记录缺少有效内容指纹")
        output.append({**entry, "id": hashlib.sha256((entry["path"] + sha).encode()).hexdigest()[:32],
                       "archived": bool(entry.get("archive_path"))})
    return output


def source(home: Path, entry: dict) -> Path:
    if entry.get("archive_path"):
        path = home / entry["archive_path"]
        root = (home / "framework/legacy-sessions").resolve()
        if not path.resolve().is_relative_to(root):
            raise ValueError("旧记录归档路径越界")
        resolved = plain(path)
        if not resolved.is_relative_to(root):
            raise ValueError("旧记录归档路径越界")
    else:
        resolved = plain(Path(entry["path"]))
    size = resolved.stat().st_size
    if not resolved.is_file() or size > MAX_BYTES:
        raise ValueError("旧记录不是可读取文件，或超过 128 MiB 上限")
    if size != entry.get("bytes") or digest(resolved) != entry["sha256"]:
        raise ValueError("旧记录已改变，与迁移时指纹不符；请重新显式迁移")
    return resolved


def archive(home: Path) -> dict:
    current = entries(home)
    target = home / "framework/legacy-sessions"
    target.mkdir(parents=True, exist_ok=True)
    plain(target)
    updated = []
    for entry in current:
        original = source(home, entry)
        relative = Path("framework/legacy-sessions") / (entry["sha256"] + original.suffix.lower())
        final = home / relative
        if final.exists():
            if digest(plain(final)) != entry["sha256"]:
                raise ValueError("已有旧记录归档发生内容冲突")
        else:
            temporary = final.with_name(final.name + "." + uuid.uuid4().hex + ".copying")
            try:
                with original.open("rb") as reader, temporary.open("xb") as writer:
                    shutil.copyfileobj(reader, writer)
                if digest(temporary) != entry["sha256"] or digest(original) != entry["sha256"]:
                    raise ValueError("复制期间旧记录发生变化，归档未发布")
                os.replace(temporary, final)
            finally:
                temporary.unlink(missing_ok=True)
        updated.append({k: v for k, v in {**entry, "archive_path": relative.as_posix()}.items() if k not in {"id", "archived"}})
    index = home / "framework/legacy-history.json"
    temp = index.with_name(index.name + "." + uuid.uuid4().hex + ".copying")
    try:
        with temp.open("x", encoding="utf-8") as stream:
            json.dump(updated, stream, ensure_ascii=False, indent=2)
        os.replace(temp, index)
    finally:
        temp.unlink(missing_ok=True)
    return {"archived": len(updated), "readonly": True, "source_modified": False}


def record_view(record) -> dict:
    data = record.get("message", record) if isinstance(record, dict) else {}
    if not isinstance(data, dict):
        data = {}
    role = str(data.get("role", record.get("type", "record") if isinstance(record, dict) else "record"))[:80]
    content = data.get("content", data.get("text", ""))
    blocks_truncated = isinstance(content, list) and len(content) > 100
    if isinstance(content, list):
        parts = []
        for block in content[:100]:
            if not isinstance(block, dict):
                continue
            if block.get("type") in {"text", "thinking"}:
                parts.append(str(block.get("text", block.get("thinking", ""))))
            elif block.get("type") in {"toolCall", "toolcall", "tool_use"}:
                parts.append(str(block.get("name", "工具")) + " " + json.dumps(block.get("arguments", block.get("input", {})), ensure_ascii=False))
            elif block.get("type") in {"image", "image_url"}:
                parts.append("[旧记录图片；此视图不加载外部或本机原始附件]")
        content = "\n\n".join(parts)
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return {"role": role, "text": content[:65536], "truncated": blocks_truncated or len(content) > 65536,
            "timestamp": data.get("timestamp", record.get("timestamp") if isinstance(record, dict) else None)}


def read(home: Path, record_id: str, cursor: int = 0, limit: int = 50) -> dict:
    if not isinstance(record_id, str) or type(cursor) is not int or cursor < 0 or cursor > 1000000 or type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("旧记录分页参数无效")
    entry = next((entry for entry in entries(home) if entry["id"] == record_id), None)
    if entry is None:
        raise ValueError("旧记录不在已迁移索引中")
    path = source(home, entry)
    items = []
    if path.suffix.lower() == ".json":
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("JSON 数组旧记录超过 8 MiB；请先导出为逐行 JSONL")
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        records = value if isinstance(value, list) else value.get("messages", [value]) if isinstance(value, dict) else []
        if not isinstance(records, list):
            raise ValueError("旧记录 messages 必须为数组")
        items = [record_view(item) for item in records[cursor:cursor + limit]]
        more = cursor + limit < len(records)
    else:
        with path.open("r", encoding="utf-8-sig") as stream:
            line_number = 0
            while line_number < cursor:
                line = stream.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    raise ValueError("旧记录单行超过 1 MiB，无法安全显示")
                line_number += 1
            for _ in range(limit):
                line = stream.readline(MAX_LINE + 1)
                if not line:
                    break
                if len(line) > MAX_LINE:
                    raise ValueError("旧记录单行超过 1 MiB，无法安全显示")
                try:
                    items.append(record_view(json.loads(line)))
                except json.JSONDecodeError:
                    items.append({"role": "record", "text": "[此行不是有效 JSON，未解释为消息]"})
            more = bool(stream.read(1))
    source(home, entry)  # Reject a source changed while the page was being read.
    return {"session": {k: v for k, v in entry.items() if k != "path"}, "messages": items,
            "next_cursor": cursor + len(items) if more else None, "readonly": True, "transcript_conversion": False}
