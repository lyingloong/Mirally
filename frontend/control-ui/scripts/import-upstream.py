"""Import only pinned upstream browser sources, verifying Git blob identities.

This is a maintenance tool, not part of the product runtime or installation.
An optional local checkout is only a byte cache: every copied file must match
the pinned Git tree before it can be used.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import time
from urllib.request import Request, urlopen

COMMIT = "eb377ac59e6c9fd6c7705028034812becf00271b"
ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = (".test.", ".test-", ".spec.", ".fixtures.", "config-test-harness", "/test-helpers/", "/test/", "test-support", "/__fixtures__/", "/fixtures/")
PACKAGES = ("gateway-client", "gateway-protocol", "markdown-core", "media-core", "mermaid-renderer", "model-catalog-core", "net-policy", "normalization-core", "retry", "session-url-contract", "terminal-core", "workboard-contract")


def blob_hash(data: bytes) -> str:
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()


def fetch(url: str) -> bytes:
    last = None
    for attempt in range(4):
        try:
            with urlopen(Request(url, headers={"User-Agent": "Mirally-source-import"}), timeout=50) as response:
                return response.read()
        except Exception as exc:
            last = exc
            time.sleep(attempt + 1)
    raise RuntimeError(f"Could not fetch {url}") from last


def seed(path: str) -> bool:
    if path in {"LICENSE", "THIRD_PARTY_NOTICES.md", "ui/index.html", "ui/package.json"}:
        return True
    if path.startswith("ui/public/"):
        return True
    if path.startswith("ui/src/"):
        return not any(value in path for value in EXCLUDED) and "/.i18n/" not in path
    for package in PACKAGES:
        if path == f"packages/{package}/package.json":
            return True
        if path.startswith(f"packages/{package}/src/"):
            return not any(value in path for value in EXCLUDED)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tree", type=Path)
    parser.add_argument("--cache", type=Path)
    parser.add_argument("--paths", type=Path, help="JSON array of additional repository-relative paths")
    parser.add_argument("--workers", type=int, default=12)
    args = parser.parse_args()
    tree = json.loads(args.tree.read_text(encoding="utf-8")) if args.tree else json.loads(fetch(f"https://api.github.com/repos/openclaw/openclaw/git/trees/{COMMIT}?recursive=1"))
    if tree.get("sha") != COMMIT or tree.get("truncated"):
        raise RuntimeError("Expected the complete, pinned Git tree")
    entries = {entry["path"]: entry for entry in tree["tree"] if entry["type"] == "blob"}
    paths = json.loads(args.paths.read_text(encoding="utf-8")) if args.paths else [path for path in entries if seed(path)]
    manifest_path = ROOT / "upstream-files.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {"commit": COMMIT, "files": {}}

    def copy(path: str) -> tuple[str, str, int, bool]:
        if path not in entries or Path(path).is_absolute() or ".." in Path(path).parts:
            raise RuntimeError(f"Not a pinned upstream file: {path}")
        entry = entries[path]
        target = ROOT / path
        data = None
        cached = False
        for candidate in (target, args.cache / path if args.cache else None):
            if candidate and candidate.is_file():
                candidate_data = candidate.read_bytes()
                if blob_hash(candidate_data) == entry["sha"]:
                    data, cached = candidate_data, True
                    break
        if data is None:
            data = fetch(f"https://raw.githubusercontent.com/openclaw/openclaw/{COMMIT}/{path}")
        if blob_hash(data) != entry["sha"]:
            raise RuntimeError(f"Git blob verification failed: {path}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return path, entry["sha"], len(data), cached

    copied = 0
    try:
        with ThreadPoolExecutor(max_workers=args.workers) as executor:
            for task in as_completed([executor.submit(copy, path) for path in paths]):
                path, identity, size, cached = task.result()
                manifest["files"][path] = {"gitBlob": identity, "bytes": size}
                copied += 1
                if copied % 100 == 0:
                    print(f"Imported {copied}/{len(paths)} verified files", flush=True)
    finally:
        manifest["files"] = dict(sorted(manifest["files"].items()))
        manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(f"Imported {copied} verified browser source files", flush=True)


if __name__ == "__main__":
    main()
