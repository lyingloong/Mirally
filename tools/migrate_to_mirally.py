"""Preview or apply the product-name migration without rewriting user data.

Stop the Gateway, desktop windows and collector before applying. This tool
does not stop processes, connect to the Gateway, or call a model.
"""

from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import uuid
from typing import Any

import yaml


LEGACY_NAME = "openassist"
PRODUCT_NAME = "mirally"
LEGACY_ENV_PREFIX = "OPENASSIST_"
PRODUCT_ENV_PREFIX = "MIRALLY_"
ROOT = Path(__file__).resolve().parents[1]
PATH_ARGUMENTS = frozenset({"--root", "--config", "--activity-config", "--memory-config"})
PATH_ENVIRONMENT = frozenset({
    "MIRALLY_ROOT", "MIRALLY_ACTIVITY_CONFIG", "MIRALLY_MEMORY_CONFIG",
    "MIRALLY_PYTHON", "MIRALLY_WGC_HELPER", "MIRALLY_UFO_CONFIG_ROOT",
    "OPENCLAW_STATE_DIR", "OPENCLAW_CONFIG_PATH",
})
ENV_REFERENCE = re.compile(
    r"\$\{OPENASSIST_([A-Za-z0-9_]+)\}|\$OPENASSIST_([A-Za-z0-9_]+)|%OPENASSIST_([A-Za-z0-9_]+)%"
)
SPEECH_ENV_REFERENCE = re.compile(r"\$\{OPENASSIST_([A-Za-z0-9_]+)\}")
# Only these exact public release files may coexist with legacy private state.
# Normalize checkout line endings for recognition, but always preserve raw bytes.
# Update the fingerprint when changing a shipped file; unknown/authored files
# must never become an implicitly trusted destination merely by their filename.
PUBLIC_SCAFFOLD_SHA256 = {
    "activity.yaml.template": "ee2a8dcabf71ceafe81272b9ae4dc7115eced3bfabe0acf2bfcc8b089658d90c",
    "execution.yaml": "c37bcc6dac5562e65f711e4698bb08e91ef557a2c54123c1a140a57eb9880401",
    "memory.yaml.template": "b8fabf4494955c24020db0664e67e76d00b5100c802d386399341a847b9968d2",
    "openclaw-workspace/IDENTITY.md": "795b4284e969ef033a4764536e77959562198bc87664219276bb64f5b9e80f60",
    "openclaw.template.json": "baae5804e2b3ebefd7c2c297b9e05b7f185de8a6227923e6cce2294568955e8a",
    "speech.yaml.template": "29bbad783c5222ce1ee5ead1cedcbbc123055517c40788a16cc3613c683af258",
    "ufo/agents.yaml.template": "36729b415c1e07bb0309cd2614efaabc5b861d852878632ceeb904cfc3757add",
    "ufo/mcp.yaml": "45505b029ef24c8088bd02c180b2d28241efabe312514c0af17d5a815c743fe9",
    "ufo/prices.yaml": "fc067872fe87603f20c80d1d96448d013b40fd0ccd7c9244d359194ad233e4d7",
    "ufo/rag.yaml": "bc1ad8d256d5e14071bfb03125943db781e77e6021e06c6821e1925f18154ee0",
    "ufo/system.yaml": "bb4bc2829aa04e536419fd530f369b9ed6bd979217a30add3a84bd9447a8e155",
    "ufo/third_party.yaml": "166c1acb8e42d5c431c94a9f35e52815ec43243d0e246715546da99c1de52d50",
}


class MigrationError(RuntimeError):
    """The migration cannot proceed without changing its stated scope."""


@dataclass(frozen=True)
class FileChange:
    relative_path: Path
    original: bytes
    updated: bytes


@dataclass(frozen=True)
class TreeEntry:
    relative_path: Path
    digest: str | None  # None denotes a directory.
    mode: int


@dataclass(frozen=True)
class MigrationPlan:
    root: Path
    source: Path
    target: Path
    move_directory: bool
    changes: tuple[FileChange, ...]
    source_snapshot: tuple[TreeEntry, ...] | None
    target_snapshot: tuple[TreeEntry, ...] | None


def _rename_environment_reference(value: str) -> str:
    def replace(match: re.Match[str]) -> str:
        if match.group(1):
            return "${" + PRODUCT_ENV_PREFIX + match.group(1) + "}"
        if match.group(2):
            return "$" + PRODUCT_ENV_PREFIX + match.group(2)
        return "%" + PRODUCT_ENV_PREFIX + match.group(3) + "%"
    return ENV_REFERENCE.sub(replace, value)


def _rename_speech_environment_reference(value: str) -> str:
    """Match the speech loader: only a stripped, complete ${VAR} is an env key."""
    stripped = value.strip()
    match = SPEECH_ENV_REFERENCE.fullmatch(stripped)
    if match is None:
        return value
    start = len(value) - len(value.lstrip())
    return value[:start] + "${" + PRODUCT_ENV_PREFIX + match.group(1) + "}" + value[start + len(stripped):]


def _repository_path(root: Path, path: Path) -> None:
    """Reject directory links and Windows junctions before reading or writing."""
    try:
        relative = path.relative_to(root)
    except ValueError as error:
        raise MigrationError("The migration path is outside the repository.") from error
    current = root
    for component in relative.parts:
        current = current / component
        try:
            info = current.lstat()
        except FileNotFoundError:
            break
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise MigrationError("Migration paths must not contain symbolic links or Windows reparse points: " + current.relative_to(root).as_posix())
    if not path.resolve().is_relative_to(root):
        raise MigrationError("The resolved migration path is outside the repository.")


def _tree_snapshot(root: Path, directory: Path) -> tuple[TreeEntry, ...] | None:
    """Inspect without following links, retaining hashes rather than private bytes."""
    _repository_path(root, directory)
    if not directory.exists():
        return None
    if not directory.is_dir():
        raise MigrationError("The configuration path is not a directory.")
    entries = []

    def visit(parent: Path) -> None:
        for path in sorted(parent.iterdir()):
            _repository_path(root, path)
            info = path.lstat()
            if stat.S_ISDIR(info.st_mode):
                entries.append(TreeEntry(path.relative_to(directory), None, info.st_mode))
                visit(path)
            elif stat.S_ISREG(info.st_mode) and info.st_nlink == 1:
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                after = path.stat()
                if (info.st_size, info.st_mtime_ns, info.st_ino) != (after.st_size, after.st_mtime_ns, after.st_ino):
                    raise MigrationError("A configuration changed while building its migration snapshot.")
                entries.append(TreeEntry(path.relative_to(directory), digest.hexdigest(), info.st_mode))
            else:
                raise MigrationError("Migration trees must contain only regular files and directories, without links.")

    visit(directory)
    return tuple(entries)


def _validate_checkout_scaffold(root: Path, target: Path, source: tuple[TreeEntry, ...],
                                scaffold: tuple[TreeEntry, ...]) -> None:
    allowed_directories = {parent for name in PUBLIC_SCAFFOLD_SHA256
                           for parent in Path(name).parents if parent != Path(".")}
    originals = {entry.relative_path: entry for entry in source}
    for entry in scaffold:
        relative = entry.relative_path
        if entry.digest is None:
            public = relative in allowed_directories
        else:
            raw = (target / relative).read_bytes()
            public = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest() == PUBLIC_SCAFFOLD_SHA256.get(relative.as_posix())
        if not public:
            raise MigrationError("Both configuration directories exist; refusing to merge private, unknown or modified destination files: "
                                 + relative.as_posix())
        old = originals.get(relative)
        if old is not None and old.digest != entry.digest:
            raise MigrationError("Both configuration directories contain different content or types at: " + relative.as_posix())


def _check_snapshot(root: Path, path: Path, expected: tuple[TreeEntry, ...] | None) -> None:
    if _tree_snapshot(root, path) != expected:
        raise MigrationError("A configuration tree changed after preview; build a new migration plan.")


def _contains_include(config: Any) -> bool:
    if isinstance(config, dict):
        return "$include" in config or any(_contains_include(value) for value in config.values())
    if isinstance(config, list):
        return any(_contains_include(value) for value in config)
    return False


class PathMigration:
    def __init__(self, root: Path, previous_root: Path | None = None):
        current = str(root).replace("\\", "/").rstrip("/")
        previous = str(previous_root or root).replace("\\", "/").rstrip("/")
        self.rules = [
            (previous + "/config/" + LEGACY_NAME, current + "/config/" + PRODUCT_NAME),
            (current + "/config/" + LEGACY_NAME, current + "/config/" + PRODUCT_NAME),
        ]
        if previous_root is not None and previous != current:
            self.rules.append((previous, current))
        self.rules.extend([
            ("config/" + LEGACY_NAME, "config/" + PRODUCT_NAME),
            ("./config/" + LEGACY_NAME, "./config/" + PRODUCT_NAME),
        ])

    def __call__(self, value: Any) -> Any:
        if not isinstance(value, str):
            return value
        value = _rename_environment_reference(value)
        normalized = value.replace("\\", "/")
        compared = normalized.casefold() if os.name == "nt" else normalized
        for source, destination in self.rules:
            prefix = source.casefold() if os.name == "nt" else source
            if compared == prefix or compared.startswith(prefix + "/"):
                result = destination + normalized[len(source):]
                return result.replace("/", "\\") if "\\" in value and "/" not in value else result
        return value


def _path_fields(value: Any, fields: tuple[str, ...], migrate: PathMigration) -> None:
    if isinstance(value, dict):
        for name in fields:
            if name in value:
                value[name] = migrate(value[name])


def _path_list(value: Any, field: str, migrate: PathMigration) -> None:
    if isinstance(value, dict) and isinstance(value.get(field), list):
        value[field] = [migrate(item) for item in value[field]]


def _migrate_mcp_server(server: Any, migrate: PathMigration) -> None:
    if not isinstance(server, dict):
        return
    _path_fields(server, ("command", "cwd"), migrate)
    arguments = server.get("args")
    if isinstance(arguments, list):
        arguments = list(arguments)
        for index, argument in enumerate(arguments):
            if not isinstance(argument, str):
                continue
            if index and arguments[index - 1] == "-m":
                if argument == LEGACY_NAME or argument.startswith(LEGACY_NAME + "."):
                    arguments[index] = PRODUCT_NAME + argument[len(LEGACY_NAME):]
            elif index and arguments[index - 1] in PATH_ARGUMENTS:
                arguments[index] = migrate(argument)
            else:
                for flag in PATH_ARGUMENTS:
                    if argument.startswith(flag + "="):
                        arguments[index] = flag + "=" + migrate(argument[len(flag) + 1:])
                        break
        server["args"] = arguments
    environment = server.get("env")
    if isinstance(environment, dict):
        renamed = {}
        for name, value in environment.items():
            new_name = PRODUCT_ENV_PREFIX + name[len(LEGACY_ENV_PREFIX):] if name.startswith(LEGACY_ENV_PREFIX) else name
            if new_name in renamed or (new_name != name and new_name in environment):
                raise MigrationError("MCP environment contains conflicting legacy and Mirally variable names.")
            renamed[new_name] = migrate(value) if new_name in PATH_ENVIRONMENT else value
        server["env"] = renamed


def migrate_openclaw_config(config: dict, migrate: PathMigration) -> dict:
    """Update documented path fields only; authored strings remain untouched."""
    result = deepcopy(config)
    agents = result.get("agents", {})
    if isinstance(agents, dict):
        _path_fields(agents.get("defaults"), ("workspace", "agentDir"), migrate)
        entries = agents.get("entries", {})
        if isinstance(entries, dict):
            for entry in entries.values():
                _path_fields(entry, ("workspace", "agentDir"), migrate)
        for entry in agents.get("list", []) if isinstance(agents.get("list"), list) else []:
            _path_fields(entry, ("workspace", "agentDir"), migrate)
    plugins = result.get("plugins", {})
    if isinstance(plugins, dict):
        _path_list(plugins.get("load"), "paths", migrate)
        entries = plugins.get("entries", {})
        if isinstance(entries, dict):
            for entry in entries.values():
                if isinstance(entry, dict):
                    _path_fields(entry.get("config"), ("root", "configPath", "pythonPath"), migrate)
    skills = result.get("skills", {})
    if isinstance(skills, dict):
        _path_list(skills.get("load"), "extraDirs", migrate)
    mcp = result.get("mcp", {})
    if isinstance(mcp, dict) and isinstance(mcp.get("servers"), dict):
        for server in mcp["servers"].values():
            _migrate_mcp_server(server, migrate)
    return result


def _yaml_updates(raw: bytes, filename: str, migrate: PathMigration) -> bytes:
    """Patch selected scalar nodes so comments and unrelated values survive."""
    text = raw.decode("utf-8-sig")
    document = yaml.compose(text)
    if document is None:
        return raw
    if not isinstance(document, yaml.MappingNode):
        raise MigrationError(f"{filename} must contain a YAML mapping.")
    paths = {
        "activity.yaml": {("storage", "directory"), ("storage", "database_path"), ("storage", "knowledge_database_path")},
        "memory.yaml": {
            ("activity_config_path",), ("database_path",), ("projection_database_path",),
            ("storage", "database_path"), ("storage", "projection_database_path"),
        },
        "speech.yaml": {("api_key",)},
    }[filename]
    edits = []
    anchors = [token for token in yaml.scan(text) if isinstance(token, yaml.tokens.AnchorToken)]
    for path in paths:
        node = document
        last_key_end = 0
        shared_node = False
        for component in path:
            if not isinstance(node, yaml.MappingNode):
                break
            matches = [(key, value) for key, value in node.value
                       if isinstance(key, yaml.ScalarNode) and key.value == component]
            if len(matches) > 1:
                raise MigrationError(f"{filename} contains duplicate migration path fields.")
            if not matches:
                break
            key, node = matches[0]
            last_key_end = key.end_mark.index
            shared_node = shared_node or node.start_mark.index < last_key_end or any(
                node.start_mark.index <= token.start_mark.index < node.end_mark.index for token in anchors
            )
        else:
            if not isinstance(node, yaml.ScalarNode) or node.tag != "tag:yaml.org,2002:str":
                continue
            updated = _rename_speech_environment_reference(node.value) if filename == "speech.yaml" else migrate(node.value)
            if updated != node.value:
                if shared_node:
                    raise MigrationError(f"{filename} uses YAML anchors for a migration path; replace the alias with an explicit path first.")
                edits.append((node.start_mark.index, node.end_mark.index, json.dumps(updated, ensure_ascii=False)))
    if not edits:
        return raw
    for start, end, replacement in sorted(edits, reverse=True):
        text = text[:start] + replacement + text[end:]
    prefix = b"\xef\xbb\xbf" if raw.startswith(b"\xef\xbb\xbf") else b""
    return prefix + text.encode("utf-8")


def build_plan(root: Path = ROOT, previous_root: Path | None = None) -> MigrationPlan:
    root = Path(root).resolve()
    previous_root = Path(previous_root).resolve() if previous_root is not None else None
    source = root / "config" / LEGACY_NAME
    target = root / "config" / PRODUCT_NAME
    _repository_path(root, source)
    _repository_path(root, target)
    source_snapshot = _tree_snapshot(root, source)
    target_snapshot = _tree_snapshot(root, target)
    if source_snapshot is not None and target_snapshot is not None:
        _validate_checkout_scaffold(root, target, source_snapshot, target_snapshot)
    current = source if source.exists() else target
    if current.exists() and not current.is_dir():
        raise MigrationError("The configuration path is not a directory.")
    migrate = PathMigration(root, previous_root)
    changes = []
    candidates = (Path("openclaw/openclaw.json"), Path("activity.yaml"), Path("memory.yaml"), Path("speech.yaml"))
    for relative in candidates:
        path = current / relative
        _repository_path(root, path)
        if not path.is_file():
            continue
        raw = path.read_bytes()
        try:
            if relative.suffix == ".json":
                config = json.loads(raw.decode("utf-8-sig"))
                if not isinstance(config, dict):
                    raise MigrationError("openclaw.json must contain a JSON object.")
                if _contains_include(config):
                    raise MigrationError("openclaw.json contains $include; migrate included configuration explicitly before using this tool.")
                updated_config = migrate_openclaw_config(config, migrate)
                updated = (json.dumps(updated_config, ensure_ascii=False, indent=2) + "\n").encode("utf-8") if updated_config != config else raw
            else:
                updated = _yaml_updates(raw, relative.name, migrate)
        except (UnicodeError, json.JSONDecodeError, yaml.YAMLError) as error:
            raise MigrationError(f"Cannot parse {relative.as_posix()}; no configuration has been changed.") from error
        if updated != raw:
            changes.append(FileChange(relative, raw, updated))
    return MigrationPlan(root, source, target, source.exists(), tuple(changes), source_snapshot, target_snapshot)


def _atomic_write(path: Path, data: bytes, mode: int | None = None) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".mirally-migration-", dir=path.parent)
    pending = Path(temporary)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if mode is not None:
            pending.chmod(stat.S_IMODE(mode))
        os.replace(pending, path)
    finally:
        pending.unlink(missing_ok=True)


def apply_plan(plan: MigrationPlan) -> Path | None:
    """Back up originals, move the whole state tree, and atomically patch files."""
    current = plan.source if plan.move_directory else plan.target
    _repository_path(plan.root, plan.source)
    _repository_path(plan.root, plan.target)
    _check_snapshot(plan.root, plan.source, plan.source_snapshot)
    _check_snapshot(plan.root, plan.target, plan.target_snapshot)
    if plan.move_directory and not plan.source.is_dir():
        raise MigrationError("The legacy configuration directory changed after preview.")
    for change in plan.changes:
        _repository_path(plan.root, current / change.relative_path)
        if (current / change.relative_path).read_bytes() != change.original:
            raise MigrationError("A configuration changed after preview; build a new migration plan.")
    if not plan.move_directory and not plan.changes:
        return None
    backup = None
    modes = {change.relative_path: (current / change.relative_path).stat().st_mode for change in plan.changes}
    merge_scaffold = plan.move_directory and plan.target_snapshot is not None
    if plan.changes or merge_scaffold:
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
        backup = plan.root / ".artifacts" / "name-migration" / stamp
        _repository_path(plan.root, backup)
        backup.mkdir(parents=True)
        for change in plan.changes:
            path = backup / change.relative_path
            path.parent.mkdir(parents=True, exist_ok=True)
            _atomic_write(path, change.original, modes[change.relative_path])
    applied = []
    copied_files = []
    created_directories = []
    moved = False
    scaffold_backup = None
    try:
        _check_snapshot(plan.root, plan.source, plan.source_snapshot)
        _check_snapshot(plan.root, plan.target, plan.target_snapshot)
        if merge_scaffold:
            scaffold_backup = backup / "checkout-scaffold"
            _repository_path(plan.root, scaffold_backup)
            plan.target.rename(scaffold_backup)
        if plan.move_directory:
            plan.source.rename(plan.target)
            moved = True
        if scaffold_backup is not None:
            _check_snapshot(plan.root, scaffold_backup, plan.target_snapshot)
            for entry in plan.target_snapshot:
                path = plan.target / entry.relative_path
                _repository_path(plan.root, path)
                if path.exists():
                    if ((entry.digest is None and not path.is_dir()) or
                            (entry.digest is not None and (not path.is_file() or
                             hashlib.sha256(path.read_bytes()).hexdigest() != entry.digest))):
                        raise MigrationError("A shared template changed during migration.")
                    continue
                if entry.digest is None:
                    path.mkdir()
                    created_directories.append(path)
                else:
                    raw = (scaffold_backup / entry.relative_path).read_bytes()
                    if hashlib.sha256(raw).hexdigest() != entry.digest:
                        raise MigrationError("A public template changed during migration.")
                    # Exclusive creation cannot overwrite a file appearing after preflight.
                    with path.open("xb") as stream:
                        copied_files.append(path)
                        stream.write(raw)
                        stream.flush()
                        os.fsync(stream.fileno())
                    path.chmod(stat.S_IMODE(entry.mode))
        for change in plan.changes:
            path = plan.target / change.relative_path
            _repository_path(plan.root, path)
            if path.read_bytes() != change.original:
                raise MigrationError("A configuration changed during migration; no later file was overwritten.")
            _atomic_write(path, change.updated, modes[change.relative_path])
            applied.append(change)
    except Exception as error:
        try:
            for change in reversed(applied):
                _repository_path(plan.root, plan.target / change.relative_path)
                _atomic_write(plan.target / change.relative_path, change.original, modes[change.relative_path])
            for path in reversed(copied_files):
                _repository_path(plan.root, path)
                path.unlink()
            for path in reversed(created_directories):
                _repository_path(plan.root, path)
                path.rmdir()
            if moved:
                _repository_path(plan.root, plan.source)
                _repository_path(plan.root, plan.target)
                plan.target.rename(plan.source)
            if scaffold_backup is not None and scaffold_backup.exists():
                _repository_path(plan.root, plan.target)
                scaffold_backup.rename(plan.target)
        except (OSError, MigrationError):
            raise MigrationError("Migration failed and rollback could not finish; restore the private backup under .artifacts/name-migration/.") from error
        raise MigrationError("Migration failed; prior configuration was restored. Close services and try again.") from error
    return backup


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT, help="Current repository root")
    parser.add_argument("--previous-root", type=Path, help="Repository root before a physical directory move")
    parser.add_argument("--apply", action="store_true", help="Write the reviewed migration; default only previews")
    args = parser.parse_args()
    print("Stop the Gateway, Mirally windows and Activity collector before applying; this tool does not stop services.")
    try:
        plan = build_plan(args.root, args.previous_root)
        if plan.move_directory:
            print("Move config/openassist to config/mirally, including private state and history.")
        for change in plan.changes:
            print("Update configuration paths: " + change.relative_path.as_posix())
        if not plan.move_directory and not plan.changes:
            print("No migration changes are needed.")
        elif args.apply:
            backup = apply_plan(plan)
            print("Migration applied. Restart services from the current repository.")
            if backup is not None:
                print("Original private configurations backed up under .artifacts/name-migration/.")
        else:
            print("Preview only. Use --apply to migrate after stopping services.")
    except (MigrationError, OSError) as error:
        # Error detail from parsers or OS APIs must not echo configuration data.
        import sys
        print(str(error) if isinstance(error, MigrationError) else "Migration could not access the configuration files.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
