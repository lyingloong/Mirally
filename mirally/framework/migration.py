"""Explicit, offline legacy copy-in and rollback preparation.

Preview is the default. No source is discovered from the machine or repo.
Apply requires stopped owners, uses SQLite backup (including committed WAL),
and publishes a completely new home only after every copy has been checked.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import stat
import sys
import tempfile
import time
from typing import Any, Callable

from .service import FrameworkError, FrameworkService


class MigrationError(ValueError):
    pass


def _plain_path(path: Path) -> Path:
    """Reject symlinks/junctions instead of resolving a surprise copy target."""
    absolute = Path(os.path.abspath(path.expanduser()))
    for part in (absolute, *absolute.parents):
        if part.exists() or part.is_symlink():
            details = part.lstat()
            if stat.S_ISLNK(details.st_mode) or getattr(details, "st_file_attributes", 0) & 0x400:
                raise MigrationError("migration paths must not contain symlinks or junctions")
    return absolute


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _read_json(path: Path) -> Any:
    _plain_path(path)
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as error:
        raise MigrationError("source must be an explicit readable standalone JSON export") from error


def _snapshot(paths: list[Path]) -> dict[str, dict[str, Any]]:
    return {str(path): {"size": path.stat().st_size, "sha256": _hash(path)} for path in paths}


def _database_info(connection: sqlite3.Connection) -> dict[str, Any]:
    tables = [row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    counts = {}
    for name in tables:
        escaped = name.replace('"', '""')
        counts[name] = connection.execute(f'SELECT COUNT(*) FROM "{escaped}"').fetchone()[0]
    schema = connection.execute("PRAGMA user_version").fetchone()[0]
    # Activity's owner version is in knowledge_meta/state rather than always in
    # user_version. Retain both without interpreting it as a product migration.
    owner_schema = None
    if "knowledge_meta" in tables:
        row = connection.execute("SELECT value FROM knowledge_meta WHERE key='schema_version'").fetchone()
        owner_schema = row[0] if row else None
    return {"user_version": schema, "owner_schema": owner_schema, "table_counts": counts}


def _backup(source: Path, destination: Path) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    def progress(_status, _remaining, _total):
        if time.monotonic() - started > 60:
            raise MigrationError("SQLite backup exceeded its deadline; stop source writers and retry")
    with sqlite3.connect(source.resolve().as_uri() + "?mode=ro", uri=True, timeout=2) as reader:
        reader.execute("PRAGMA query_only=ON")
        with sqlite3.connect(destination) as writer:
            reader.backup(writer, pages=128, progress=progress, sleep=0.02)
            if writer.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise MigrationError("copied SQLite database failed quick_check")
            result = _database_info(writer)
    return {**result, "sha256": _hash(destination), "bytes": destination.stat().st_size}


def _import_model(config: dict[str, Any], reference: str | None) -> dict[str, Any]:
    if "$include" in config or "include" in config:
        raise MigrationError("export a standalone OpenClaw config before migration; includes are not followed")
    selected = reference or config.get("agents", {}).get("defaults", {}).get("model", {})
    if isinstance(selected, dict):
        selected = selected.get("primary")
    if not isinstance(selected, str) or "/" not in selected:
        raise MigrationError("OpenClaw model must have an explicit provider/model primary reference")
    provider_id, model_id = selected.split("/", 1)
    provider = config.get("models", {}).get("providers", {}).get(provider_id)
    if not isinstance(provider, dict):
        raise MigrationError("selected OpenClaw provider does not exist")
    key = provider.get("apiKey", "")
    if not isinstance(key, str) or "${" in key:
        raise MigrationError("resolve the selected provider credential in an explicit local export first")
    model = next((entry for entry in provider.get("models", []) if isinstance(entry, dict) and entry.get("id") == model_id), {})
    api = model.get("api") or provider.get("api", "openai-completions")
    formats = {"openai-completions": "openai-chat", "openai-responses": "openai-responses", "anthropic-messages": "anthropic-messages"}
    if api not in formats:
        raise MigrationError("selected OpenClaw API format requires an explicit product adapter")
    result = {"provider": "anthropic" if api == "anthropic-messages" else "openai-compatible", "model_id": model_id, "api_key": key,
              "base_url": provider.get("baseUrl", ""), "api_format": formats[api],
              "image_enabled": "image" in model.get("input", []), "reasoning_enabled": bool(model.get("reasoning", False))}
    if isinstance(model.get("maxTokens"), int):
        result["max_tokens"] = model["maxTokens"]
    return result


class MigrationPlan:
    def __init__(self, root: Path | str, home: Path | str, *, activity_profile: Path | str | None = None,
                 memory_profile: Path | str | None = None, openclaw_config: Path | str | None = None,
                 preferences_json: Path | str | None = None, legacy_history: Path | str | None = None,
                 model_ref: str | None = None) -> None:
        self.root = _plain_path(Path(root))
        self.home = _plain_path(Path(home))
        self.databases: list[tuple[str, Path, Path]] = []
        self.assets: list[tuple[Path, Path]] = []
        self.asset_root: Path | None = None
        self.history_root: Path | None = None
        self.source_files: list[Path] = []
        self.memory_database: Path | None = None
        self.activity_database: Path | None = None
        self.model: dict[str, Any] | None = None
        self.preferences: list[dict[str, Any]] = []
        self.history: list[dict[str, Any]] = []
        self.profiles: dict[str, str] = {}
        if not any((activity_profile, memory_profile, openclaw_config, preferences_json, legacy_history)):
            raise MigrationError("provide at least one explicit source; nothing is discovered automatically")
        if activity_profile:
            from mirally.runtime.activity.config import load_activity_config
            path = self._source_file(activity_profile)
            self.profiles["activity"] = str(path)
            config = load_activity_config(self.root, path)
            self.activity_database = config.database_path
            self._database("activity", config.database_path, Path("data/activity/observations.sqlite3"), required=True)
            knowledge = config.knowledge_database_path or config.database_path.with_name("activity-knowledge.sqlite3")
            self._database("activity_knowledge", knowledge, Path("data/activity/knowledge.sqlite3"))
            if config.asset_directory.exists():
                directory = _plain_path(config.asset_directory)
                self.asset_root = directory
                for source in sorted(directory.rglob("*")):
                    _plain_path(source)
                    if source.is_file():
                        self.assets.append((source, Path("data/activity/assets") / source.relative_to(directory)))
                        self.source_files.append(source)
        if memory_profile:
            from mirally.runtime.memory.config import load_memory_config
            path = self._source_file(memory_profile)
            self.profiles["memory"] = str(path)
            config = load_memory_config(self.root, path)
            self.memory_database = config.database_path
            self._database("memory", config.database_path, Path("memory/memory.sqlite3"), required=True)
            self._database("memory_projection", config.projection_database_path, Path("memory/activity-projection.sqlite3"))
        if openclaw_config:
            path = self._source_file(openclaw_config)
            self.profiles["openclaw"] = str(path)
            value = _read_json(path)
            if not isinstance(value, dict):
                raise MigrationError("OpenClaw config must be an object")
            self.model = _import_model(value, model_ref)
        if preferences_json:
            path = self._source_file(preferences_json)
            self.profiles["preferences"] = str(path)
            value = _read_json(path)
            if not isinstance(value, list) or len(value) > 1000 or any(not isinstance(entry, dict) or "key" not in entry or "value" not in entry for entry in value):
                raise MigrationError("preferences export must be an array of explicit key/value/scope objects")
            self.preferences = value
        if legacy_history:
            directory = _plain_path(Path(legacy_history))
            if not directory.is_dir():
                raise MigrationError("legacy history must be an explicit directory")
            self.profiles["legacy_history"] = str(directory)
            self.history_root = directory
            for source in sorted(directory.rglob("*")):
                _plain_path(source)
                if source.is_file() and source.suffix.lower() in {".json", ".jsonl"}:
                    self.source_files.append(source)
                    self.history.append({"session_id": source.stem, "path": str(source), "readonly": True,
                                         "bytes": source.stat().st_size, "sha256": _hash(source)})
                    if len(self.history) > 10000:
                        raise MigrationError("legacy history index exceeds 10000 files")
        self.source_files = sorted(set(self.source_files))
        if any(source == self.home or self.home in source.parents or source in self.home.parents for source in self.source_files):
            raise MigrationError("target home must be separate from every source")
        self.fingerprint = _snapshot(self.source_files)

    def _inventory(self) -> list[Path]:
        """Also detect files/WAL created since the reviewed preview."""
        paths = set(self.source_files)
        for _kind, source, _destination in self.databases:
            wal = source.with_name(source.name + "-wal")
            if wal.exists():
                paths.add(_plain_path(wal))
        for directory, history_only in ((self.asset_root, False), (self.history_root, True)):
            if directory is not None:
                for path in directory.rglob("*"):
                    _plain_path(path)
                    if path.is_file() and (not history_only or path.suffix.lower() in {".json", ".jsonl"}):
                        paths.add(path)
        return sorted(paths)

    def _source_file(self, value: Path | str) -> Path:
        path = _plain_path(Path(value))
        if not path.is_file():
            raise MigrationError("explicit source file does not exist")
        self.source_files.append(path)
        return path

    def _database(self, kind: str, source: Path, destination: Path, *, required: bool = False) -> None:
        source = _plain_path(source)
        if not source.is_file():
            if required:
                raise MigrationError(f"{kind} database does not exist")
            return
        self.databases.append((kind, source, destination))
        self.source_files.append(source)
        wal = source.with_name(source.name + "-wal")
        if wal.is_file():
            self.source_files.append(_plain_path(wal))

    def _target_empty(self) -> None:
        _plain_path(self.home)
        if self.home.exists() and (not self.home.is_dir() or any(self.home.iterdir())):
            raise MigrationError("target home must be absent or empty; existing state is never overwritten")

    def preview(self) -> dict[str, Any]:
        self._target_empty()
        return {"mode": "preview", "home": str(self.home), "profiles": self.profiles,
                "databases": [{"kind": kind, "source": str(source), "destination": str(relative)} for kind, source, relative in self.databases],
                "asset_count": len(self.assets), "legacy_session_count": len(self.history), "preference_count": len(self.preferences),
                "model": {"provider": self.model["provider"], "model_id": self.model["model_id"], "api_key_configured": bool(self.model["api_key"])} if self.model else None,
                "source_fingerprints": self.fingerprint, "requires_stopped_owners": True,
                "owner_locks_checked": False, "transcript_conversion": False, "writes": False}

    def _locks(self, stack: ExitStack, lock_factory: Callable[..., Any]) -> None:
        if self.activity_database:
            lock = lock_factory(fallback_path=self.activity_database.with_suffix(".lock"))
            if not lock.acquire():
                raise MigrationError("source Activity collector is running; stop it before migration")
            stack.callback(lock.release)
        if self.memory_database:
            digest = hashlib.sha256(str(self.memory_database.resolve()).casefold().encode()).hexdigest()[:24]
            lock = lock_factory(name="Local\\MirallyMemory-" + digest, fallback_path=self.memory_database.with_suffix(".lock"))
            if not lock.acquire():
                raise MigrationError("source Memory maintenance is running; stop it before migration")
            stack.callback(lock.release)

    def apply(self, *, confirm_stopped: bool = False, lock_factory: Callable[..., Any] | None = None) -> dict[str, Any]:
        if not confirm_stopped:
            raise MigrationError("apply requires confirm_stopped after shutting down the old collector and maintenance")
        from mirally.runtime.activity.lock import ActivityProcessLock
        self._target_empty()
        self.home.parent.mkdir(parents=True, exist_ok=True)
        stage: Path | None = None
        with ExitStack() as stack:
            self._locks(stack, lock_factory or ActivityProcessLock)
            if _snapshot(self._inventory()) != self.fingerprint:
                raise MigrationError("source changed since preview; create a fresh migration plan")
            stage = Path(tempfile.mkdtemp(prefix=".mirally-migration-", dir=self.home.parent))
            try:
                database_results = []
                for kind, source, relative in self.databases:
                    result = _backup(source, stage / relative)
                    database_results.append({"kind": kind, "source": str(source), "destination": str(relative),
                                             "old_schema": result["user_version"], "new_schema": result["user_version"], **result})
                for source, relative in self.assets:
                    destination = stage / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    if _hash(source) != _hash(destination):
                        raise MigrationError("asset copy verification failed")
                with FrameworkService(self.root, stage) as service:
                    if self.model:
                        service.settings_update({"model": self.model})
                    for preference in self.preferences:
                        service.preference_set(**preference)
                if _snapshot(self._inventory()) != self.fingerprint:
                    raise MigrationError("source changed while copying; no target was published")
                manifest = {"version": 1, "status": "copied", "created_at_utc_ms": time.time_ns() // 1_000_000,
                            "home": str(self.home), "profiles": self.profiles, "databases": database_results,
                            "asset_count": len(self.assets), "assets": [{"destination": str(relative), "sha256": _hash(stage / relative)} for _, relative in self.assets],
                            "source_fingerprints": self.fingerprint, "legacy_session_count": len(self.history), "legacy_sessions_archived": True,
                            "preference_count": len(self.preferences), "model_imported": self.model is not None,
                            "owner_locks_checked": True, "sources_modified": False, "transcript_conversion": False,
                            "rollback": "stop the new host, run --rollback, then use the saved old launcher/config; keep this home for new data"}
                (stage / "framework" / "migration-manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
                (stage / "framework" / "legacy-history.json").write_text(json.dumps(self.history, ensure_ascii=False, indent=2), encoding="utf-8")
                # Keep old records independently readable after the original
                # runtime/source folder has been retired. No transcript conversion.
                from .legacy_history import archive
                archive(stage)
                self._target_empty()
                existed_empty = self.home.exists()
                if existed_empty:
                    self.home.rmdir()  # Fails safely if another process added files.
                try:
                    os.rename(stage, self.home)
                except BaseException:
                    if existed_empty and not self.home.exists():
                        self.home.mkdir()
                    raise
                stage = None
                return manifest
            finally:
                if stage is not None and stage.exists():
                    # Only remove the exact freshly created sibling stage.
                    if stage.parent.resolve() != self.home.parent.resolve() or not stage.name.startswith(".mirally-migration-"):
                        raise MigrationError("refusing cleanup outside migration stage")
                    shutil.rmtree(stage)


def rollback(home: Path | str, root: Path | str) -> dict[str, Any]:
    """Quiesce a stopped new home; preserve all new data and all old sources."""
    from .__main__ import worker_lock
    path = _plain_path(Path(home))
    manifest_path = path / "framework" / "migration-manifest.json"
    manifest = _read_json(manifest_path)
    if not isinstance(manifest, dict) or manifest.get("version") != 1:
        raise MigrationError("no supported migration manifest in this home")
    with worker_lock(path):
        with FrameworkService(root, path) as service:
            service.settings_update({"activity_enabled": False, "background_enabled": False, "general_initiative_enabled": False})
        result = {"status": "ready_for_previous_launcher", "new_home_preserved": True, "home": str(path),
                  "profiles": manifest["profiles"], "old_sources_modified": False, "automatic_session_conversion": False,
                  "next": "start the saved old launcher with its original profiles; new data stays in this home"}
        (path / "framework" / "migration-rollback.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--home", type=Path, required=True)
    parser.add_argument("--activity-profile", type=Path)
    parser.add_argument("--memory-profile", type=Path)
    parser.add_argument("--openclaw-config", type=Path)
    parser.add_argument("--preferences-json", type=Path)
    parser.add_argument("--legacy-history", type=Path)
    parser.add_argument("--model-ref")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--confirm-stopped", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    try:
        if args.rollback:
            result = rollback(args.home, args.root)
        else:
            plan = MigrationPlan(args.root, args.home, activity_profile=args.activity_profile, memory_profile=args.memory_profile,
                                 openclaw_config=args.openclaw_config, preferences_json=args.preferences_json,
                                 legacy_history=args.legacy_history, model_ref=args.model_ref)
            result = plan.apply(confirm_stopped=args.confirm_stopped) if args.apply else plan.preview()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (MigrationError, FrameworkError, OSError, sqlite3.Error) as error:
        print(f"Migration refused: {type(error).__name__}: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
