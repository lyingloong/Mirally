"""Bounded workspace capabilities with explicit, persisted product access grants."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import stat
import uuid

from .materials import extract

DEFAULT = {"revision": 1, "read_roots": [], "web_enabled": False,
           "workspace_write_enabled": False, "search_connection": "chat"}


def get(service):
    row=service.db.execute("SELECT value_json FROM settings WHERE key='capabilities'").fetchone()
    value={**DEFAULT, **(json.loads(row[0]) if row else {})}
    return {**value, "workspace": str(service.home / "workspace")}


def configure(service, expected_revision, patch):
    if type(expected_revision) is not int or not isinstance(patch,dict) or not patch or set(patch)-{"read_roots","web_enabled","workspace_write_enabled","search_connection"}:
        raise ValueError("能力设置参数无效")
    current=get(service)
    if expected_revision != current["revision"]:
        raise ValueError("能力设置已改变，请刷新后重试")
    for key,value in patch.items():
        if key in {"web_enabled","workspace_write_enabled"} and type(value) is not bool:
            raise ValueError("能力开关必须为布尔值")
        if key=="read_roots":
            if not isinstance(value,list) or len(value)>20 or any(not isinstance(p,str) or not p.strip() or len(p)>2048 for p in value):
                raise ValueError("最多授权 20 个只读文件夹")
            value=[str(_plain(Path(p).expanduser() if Path(p).expanduser().is_absolute() else service.home/Path(p))) for p in value]
            if any(not Path(p).is_dir() for p in value):
                raise ValueError("只读授权必须指向存在的文件夹")
            value=list(dict.fromkeys(value))
        if key=="search_connection" and (not isinstance(value,str) or not value or len(value)>100):
            raise ValueError("网页连接标识无效")
        current[key]=value
    connection=current["search_connection"]
    if current["web_enabled"] and connection not in {"chat","default"} and connection not in service._settings().get("model_connections",{}):
        raise ValueError("所选网页连接已不存在，请重新选择后保存")
    current.pop("workspace",None);current["revision"]+=1
    with service._transaction():
        service.db.execute("INSERT OR REPLACE INTO settings(key,value_json) VALUES('capabilities',?)",(json.dumps(current),))
    return get(service)


def _plain(path):
    try:
        for part in (path,*path.parents):
            info=part.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info,"st_file_attributes",0)&0x400:
                raise ValueError("文件访问不跟随符号链接或目录联接")
        return path.resolve(strict=True)
    except OSError as error:
        raise ValueError("授权文件夹或文件已移走，或当前无法读取") from error


def roots(service):
    config=get(service)
    workspace=service.home/"workspace";workspace.mkdir(parents=True,exist_ok=True)
    return [{"id":"workspace","name":"Mirally 工作区","path":str(_plain(workspace)),"writable":config["workspace_write_enabled"]}]+[
        {"id":hashlib.sha256(p.encode()).hexdigest()[:16],"name":Path(p).name or p,"path":p,"writable":False} for p in config["read_roots"]]


def target(service, root_id, path, *, write=False):
    root=next((item for item in roots(service) if item["id"]==root_id),None)
    if root is None or not isinstance(path,str) or len(path)>2048 or "\x00" in path:
        raise ValueError("文件路径或授权文件夹无效")
    if write and (root_id!="workspace" or not root["writable"]):
        raise ValueError("工作区写入尚未开启；其他授权文件夹仅可读取")
    base=_plain(Path(root["path"]))
    candidate=Path(path) if Path(path).is_absolute() else base/path
    # Check lexical ancestry and each existing component before opening it.
    normalized=candidate.resolve()
    if not normalized.is_relative_to(base):
        raise ValueError("路径超出授权文件夹")
    for part in (candidate,*candidate.parents):
        if part.exists() or part.is_symlink():
            _plain(part)
        if part==base:break
    return normalized


def list_files(service, root_id="workspace", path="."):
    folder=target(service,root_id,path)
    if not folder.is_dir():raise ValueError("请选择文件夹")
    items=[]
    with os.scandir(folder) as stream:
        for item in stream:
            if len(items)>=200:break
            if item.is_symlink() or getattr(item.stat(follow_symlinks=False),"st_file_attributes",0)&0x400:continue
            items.append({"name":item.name,"directory":item.is_dir(follow_symlinks=False),"bytes":item.stat(follow_symlinks=False).st_size})
    return {"entries":sorted(items,key=lambda x:x["name"].casefold()),"limit":200,"root_id":root_id,"path":path}


def read_file(service,root_id="workspace",path=""):
    file=target(service,root_id,path)
    if not file.is_file() or file.stat().st_size>8*1024*1024:raise ValueError("文件不存在或超过 8 MiB 读取上限")
    with file.open("rb") as stream:data=stream.read(8*1024*1024+1)
    if len(data)>8*1024*1024:raise ValueError("文件在读取时超过上限")
    return {"name":file.name,"sha256":hashlib.sha256(data).hexdigest(),**extract(file.name,data)}


def search_files(service,query,root_id="workspace",path="."):
    if not isinstance(query,str) or not 1<=len(query.strip())<=512:raise ValueError("搜索词长度需为 1–512 字符")
    folder=target(service,root_id,path)
    root=target(service,root_id,".")
    if not folder.is_dir():raise ValueError("请选择文件夹")
    hits=[];visited=0;total_bytes=0;directories=0
    for directory,dirs,files in os.walk(folder,followlinks=False):
        directories+=1
        if directories>1000:return {"matches":hits,"truncated":True}
        dirs[:]=[d for d in dirs if d not in {".git","node_modules",".venv"} and not (Path(directory)/d).is_symlink() and not getattr((Path(directory)/d).lstat(),"st_file_attributes",0)&0x400]
        for name in files:
            visited+=1
            if visited>1000 or total_bytes>16*1024*1024 or len(hits)>=50:return {"matches":hits,"truncated":True}
            file=Path(directory)/name
            try:
                _plain(file)
                if file.stat().st_size>1024*1024:continue
                with file.open("rb") as stream:data=stream.read(1024*1024+1)
                total_bytes+=len(data)
                if len(data)>1024*1024:continue
                text=data.decode("utf-8-sig")
                if "\x00" in text:continue
            except (OSError,ValueError,UnicodeError):continue
            for line_number,line in enumerate(text.splitlines(),1):
                if query.casefold() in line.casefold():
                    hits.append({"path":str(file.relative_to(root)),"line":line_number,"text":line[:1000]})
                    if len(hits)>=50:return {"matches":hits,"truncated":True}
    return {"matches":hits,"truncated":False}


def _file_digest(file):
    if not file.is_file():raise ValueError("目标不是普通文件")
    with file.open("rb") as stream:data=stream.read(8*1024*1024+1)
    if len(data)>8*1024*1024:raise ValueError("文件超过 8 MiB 读取上限")
    return hashlib.sha256(data).hexdigest()


def write_file(service,path,content,expected_sha256=None):
    if not isinstance(content,str) or len(content.encode("utf-8"))>1024*1024:raise ValueError("工作区文字输出最多 1 MiB")
    file=target(service,"workspace",path,write=True)
    if file.exists():
        if not isinstance(expected_sha256,str) or _file_digest(file)!=expected_sha256:
            raise ValueError("覆盖已有文件需先读取并提供匹配的内容指纹")
    elif expected_sha256 is not None:raise ValueError("待覆盖文件不存在")
    file.parent.mkdir(parents=True,exist_ok=True);target(service,"workspace",path,write=True)
    temp=file.with_name(file.name+"."+uuid.uuid4().hex+".tmp")
    try:
        with temp.open("x",encoding="utf-8",newline="") as stream:stream.write(content)
        if file.exists():
            if _file_digest(file)!=expected_sha256:raise ValueError("写入前文件已改变")
            os.replace(temp,file)
        else:
            # Link exclusively publishes a new file; it cannot overwrite a race winner.
            os.link(temp,file);temp.unlink()
    finally:temp.unlink(missing_ok=True)
    return {"path":str(file.relative_to(service.home/"workspace")),"bytes":file.stat().st_size,"sha256":_file_digest(file)}
