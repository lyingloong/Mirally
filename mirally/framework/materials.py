"""Bounded, offline extraction of explicitly supplied document bytes."""
from __future__ import annotations

import base64
import io
import json
import re
from pathlib import Path
import sys
import zipfile
from xml.etree import ElementTree

MAX_TEXT = 100_000


def extract(name: str, data: bytes) -> dict:
    suffix = Path(name).suffix.lower()
    if len(data) > 8 * 1024 * 1024:
        raise ValueError("材料超过 8 MiB 上限")
    text = None
    if suffix in {".txt", ".md", ".csv", ".json", ".jsonl", ".log", ".html", ".htm", ".xml", ".yaml", ".yml", ".py", ".js", ".ts", ".css", ".svg"}:
        encodings = ("utf-16",) if data.startswith((b"\xff\xfe", b"\xfe\xff")) else ("utf-8-sig", "gb18030")
        for encoding in encodings:
            try:
                text = data.decode(encoding)
                break
            except UnicodeError:
                continue
        if text is None or "\x00" in text:
            return {"status": "unsupported", "note": "无法按支持的文本编码读取，请导出为 UTF-8 文本。"}
    elif suffix in {".docx", ".pptx", ".xlsx"}:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            names = [value for value in archive.namelist() if
                     value == "word/document.xml" or value == "xl/sharedStrings.xml" or
                     value.startswith("xl/worksheets/") and value.endswith(".xml") or
                     value.startswith("ppt/slides/slide") and value.endswith(".xml")]
            if len(names) > 300 or sum(archive.getinfo(value).file_size for value in names) > 24 * 1024 * 1024:
                raise ValueError("文档展开内容超过解析上限")
            parts = []
            for value in sorted(names, key=lambda name: [int(part) if part.isdigit() else part for part in re.split(r"(\d+)", name)]):
                raw = archive.read(value)
                declarations = raw.replace(b"\x00", b"").upper()
                if b"<!DOCTYPE" in declarations or b"<!ENTITY" in declarations:
                    raise ValueError("不支持含外部实体的文档")
                tree = ElementTree.fromstring(raw)
                parts.append(" ".join(node.text or "" for node in tree.iter() if node.tag.rsplit("}", 1)[-1] in {"t", "v"}))
            text = "\n".join(parts)
            if suffix == ".xlsx":
                return {"status": "partial", "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT,
                        "note": "工作表文字与单元格值；共享字符串索引、样式和公式关系未重建，不能据此声称精确表格计算。"}
            if suffix == ".pptx":
                return {"status": "partial", "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT,
                        "note": "按幻灯片文件序号提取文字；未重建自定义页序、布局、图形和备注。"}
    elif suffix == ".pdf":
        try:
            from pypdf import PdfReader
        except ImportError:
            return {"status": "unsupported", "note": "PDF 解析需要 requirements-framework.txt 中的 pypdf；扫描页需另行 OCR。"}
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            return {"status": "unsupported", "note": "加密 PDF 需先解密。"}
        text = "\n\n".join((page.extract_text() or "") for page in reader.pages[:100])
        return {"status": "partial", "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT or len(reader.pages) > 100,
                "note": "最多前 100 页文字，扫描画面、图表和排版不代表已经读取。"}
    else:
        return {"status": "unsupported", "note": "材料已保存，但此格式尚未自动解析；音视频需要已配置的转写能力，不能当作已听过或看过。"}
    return {"status": "ready", "text": text[:MAX_TEXT], "truncated": len(text) > MAX_TEXT}


def main() -> int:
    for stream in (sys.stdin, sys.stdout):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    request = json.loads(sys.stdin.read(12_000_000))
    try:
        result = extract(request["name"], base64.b64decode(request["data"], validate=True))
    except Exception as error:
        result = {"status": "error", "note": f"材料解析失败（{type(error).__name__}），请换用文本或图片。"}
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
