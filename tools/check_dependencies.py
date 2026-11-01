"""Read-only dependency audit for Mirally manifests.

Run from the repository root:

    python tools/check_dependencies.py            # human summary, exit 0/1
    python tools/check_dependencies.py --json     # machine-readable report

The audit only reads files: it never installs, upgrades or deletes anything and
never uses the network. It reports (1) the same direct dependency declared with
different constraints inside one environment, (2) requirements-framework.txt
entries whose module is never imported under ``mirally/`` (candidate only), and
(3) which manifests are runtime, optional, CI, upstream, tooling or fixtures.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {".git", ".venv", "node_modules", "output", ".artifacts", "UFO", "__pycache__",
             "dist", "build", "coverage", ".vite", ".turbo"}
FIXTURE_PARTS = {"fixture", "fixtures", "test", "tests", "example", "examples", "sample", "samples",
                 "demo", "demos", "benchmark", "benchmarks", "e2e"}

PIP_MANIFESTS = (
    ("requirements-framework.txt", "product-venv", "runtime", "产品运行时（.venv）"),
    ("requirements-ufo.txt", "product-venv", "optional", "UFO 参考适配（dev-0012 处理中）"),
    ("kernel/dsh/.github/review-ownership/requirements.txt", "kernel-ci", "ci", "kernel CI 审查机器人"),
)
NPM_MANIFESTS = (
    ("tools/testing/package.json", "test-tools", "devtools", "浏览器/DOM 验收工具"),
)
LOCKFILES = ("frontend/control-ui/package-lock.json", "tools/testing/package-lock.json",
             "kernel/dsh/pnpm-lock.yaml")
FRONTEND_ROOT = "frontend/control-ui"
KERNEL_ROOT = "kernel/dsh"
# Data-only companions or split namespaces that are imported through another name.
IMPORT_NAMES = {
    "pywebview": ("webview",),
    "pillow": ("PIL",),
    "pyyaml": ("yaml",),
    "tzdata": ("zoneinfo",),
    "winrt-runtime": ("winrt",),
}
REQUIREMENT = re.compile(r"^(?P<name>[A-Za-z0-9][A-Za-z0-9._-]*)(?:\[[^\]]*\])?\s*"
                         r"(?P<constraint>[^;#]*?)\s*(?:;.*)?$")
IMPORT = re.compile(r"^\s*(?:from\s+(?P<from>[A-Za-z_][\w.]*)\s+import|import\s+(?P<import>[A-Za-z_][\w.]*))",
                    re.MULTILINE)


def canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name.strip().lower())


def _walk(base: Path, filename: str | None = None) -> list[Path]:
    found: list[Path] = []
    if not base.is_dir():
        return found
    for current, dirs, names in os.walk(base):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        for name in names:
            if filename is None or name == filename:
                found.append(Path(current) / name)
    return sorted(found)


def read_requirements(path: Path) -> list[dict[str, str]]:
    dependencies: list[dict[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-")):
            continue
        match = REQUIREMENT.match(line)
        if match:
            dependencies.append({"name": match.group("name"), "constraint": match.group("constraint")})
    return dependencies


def read_package_json(path: Path) -> list[dict[str, str]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    dependencies: list[dict[str, str]] = []
    for field in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        for name, constraint in sorted((payload.get(field) or {}).items()):
            dependencies.append({"name": name, "constraint": str(constraint), "field": field})
    return dependencies


def _manifest(root: Path, relative: str, ecosystem: str, environment: str, kind: str,
              owner: str) -> dict | None:
    path = root / relative
    if not path.is_file():
        return None
    reader = read_requirements if ecosystem == "pip" else read_package_json
    return {"path": relative, "ecosystem": ecosystem, "environment": environment, "kind": kind,
            "owner": owner, "dependencies": reader(path)}


def collect_manifests(root: Path) -> tuple[list[dict], list[str], dict]:
    manifests: list[dict] = []
    warnings: list[str] = []
    excluded_fixtures = 0

    def add(relative: str, ecosystem: str, environment: str, kind: str, owner: str,
            required: bool = False) -> None:
        path = root / relative
        if not path.is_file():
            if required:
                warnings.append(f"missing expected manifest: {relative}")
            return
        try:
            manifest = _manifest(root, relative, ecosystem, environment, kind, owner)
        except (json.JSONDecodeError, UnicodeDecodeError):
            warnings.append(f"unreadable manifest: {relative}")
            return
        if manifest:
            manifests.append(manifest)

    for relative, environment, kind, owner in PIP_MANIFESTS:
        add(relative, "pip", environment, kind, owner, required=kind in {"runtime", "optional", "ci"})
    for relative, environment, kind, owner in NPM_MANIFESTS:
        add(relative, "npm", environment, kind, owner, required=True)
    for path in _walk(root / FRONTEND_ROOT, "package.json"):
        add(path.relative_to(root).as_posix(), "npm", "frontend", "upstream", "前端固定上游（Control UI）")
    for path in _walk(root / KERNEL_ROOT, "package.json"):
        relative = path.relative_to(root).as_posix()
        parts = {part.lower() for part in Path(relative).parts}
        if parts & FIXTURE_PARTS:
            excluded_fixtures += 1
            continue
        add(relative, "npm", "kernel-workspace", "workspace", "kernel/dsh pnpm workspace")
    if not any(item["environment"] == "frontend" for item in manifests):
        warnings.append(f"missing expected manifest: {FRONTEND_ROOT}/package.json")
    lockfiles = [relative for relative in LOCKFILES if (root / relative).is_file()]
    return manifests, warnings, {"excluded_fixture_package_json": excluded_fixtures,
                                 "lockfiles": lockfiles}


def duplicate_declarations(manifests: list[dict]) -> tuple[list[dict], int]:
    """(conflicting groups, duplicate group count) for same-environment declarations."""
    index: dict[tuple[str, str, str], list[dict]] = {}
    for manifest in manifests:
        for dependency in manifest["dependencies"]:
            key = (manifest["ecosystem"], manifest["environment"], canonical(dependency["name"]))
            index.setdefault(key, []).append({"manifest": manifest["path"], "name": dependency["name"],
                                              "constraint": dependency["constraint"]})
    groups = [declarations for declarations in index.values() if len(declarations) > 1]
    conflicts: list[dict] = []
    for (ecosystem, environment, name), declarations in sorted(index.items()):
        if len(declarations) < 2 or len({item["constraint"] for item in declarations}) < 2:
            continue
        conflicts.append({
            "ecosystem": ecosystem,
            "environment": environment,
            "name": name,
            "conflict": True,
            # pnpm importers may pin different ranges on purpose; the lockfile resolves them.
            "severity": "info" if environment == "kernel-workspace" else "finding",
            "declarations": declarations,
        })
    return conflicts, len(groups)


def imports_under(package_root: Path) -> set[str]:
    modules: set[str] = set()
    for path in _walk(package_root):
        if path.suffix != ".py":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for match in IMPORT.finditer(text):
            module = match.group("from") or match.group("import")
            modules.add(module)
    return modules


def unused_candidates(root: Path, manifests: list[dict]) -> list[dict]:
    modules = imports_under(root / "mirally")
    candidates: list[dict] = []
    for manifest in manifests:
        if manifest["environment"] != "product-venv" or manifest["kind"] != "runtime":
            continue
        for dependency in manifest["dependencies"]:
            canonical_name = canonical(dependency["name"])
            if canonical_name.startswith("winrt-windows-"):
                targets = ("winrt",)
            else:
                targets = IMPORT_NAMES.get(canonical_name, (canonical_name.replace("-", "_"),))
            if any(module == target or module.startswith(target + ".") for target in targets for module in modules):
                continue
            candidates.append({"manifest": manifest["path"], "name": dependency["name"],
                               "constraint": dependency["constraint"], "imports": list(targets)})
    return candidates


def audit(root: Path = ROOT) -> dict:
    root = root.resolve()
    manifests, warnings, extras = collect_manifests(root)
    duplicates, duplicate_groups = duplicate_declarations(manifests)
    unused = unused_candidates(root, manifests)
    findings = [{"kind": "duplicate-constraint", **item} for item in duplicates
                if item["severity"] == "finding"]
    findings += [{"kind": "unused-candidate", **item} for item in unused]
    declarations = sum(len(item["dependencies"]) for item in manifests)
    return {
        "manifests": manifests,
        "lockfiles": extras["lockfiles"],
        "duplicates": duplicates,
        "unused_candidates": unused,
        "findings": findings,
        "warnings": warnings,
        "summary": {
            "manifests": len(manifests),
            "declarations": declarations,
            "lockfiles": len(extras["lockfiles"]),
            "excluded_fixture_package_json": extras["excluded_fixture_package_json"],
            "duplicate_groups": duplicate_groups,
            "conflicting_groups": len(duplicates),
            "informational_conflicts": sum(1 for item in duplicates if item["severity"] == "info"),
            "unused_candidates": len(unused),
            "findings": len(findings),
            "warnings": len(warnings),
        },
    }


def print_summary(report: dict) -> None:
    summary = report["summary"]
    print("Dependency audit (read-only, offline)")
    print(f"  Manifests: {summary['manifests']}  Declarations: {summary['declarations']}  "
          f"Lockfiles: {summary['lockfiles']}")
    print(f"  Excluded fixture package.json: {summary['excluded_fixture_package_json']}  "
          f"Duplicate groups: {summary['duplicate_groups']} "
          f"(conflicting: {summary['conflicting_groups']}, "
          f"informational: {summary['informational_conflicts']})")
    for warning in report["warnings"]:
        print(f"  WARN {warning}")
    if report["findings"]:
        print(f"  Findings: {summary['findings']}")
        for finding in report["findings"]:
            if finding["kind"] == "duplicate-constraint":
                constraints = ", ".join(f"{item['manifest']}={item['constraint']}"
                                        for item in finding["declarations"])
                print(f"    conflict[{finding['environment']}] {finding['name']}: {constraints}")
            else:
                print(f"    unused-candidate {finding['manifest']}: {finding['name']}"
                      f"{finding['constraint']} (no import of {', '.join(finding['imports'])})")
    else:
        print("  Findings: 0")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Read-only dependency audit (never modifies files).")
    parser.add_argument("--root", default=str(ROOT), help="repository root to audit")
    parser.add_argument("--json", action="store_true", help="print the machine-readable report only")
    args = parser.parse_args(argv)
    report = audit(Path(args.root))
    if args.json:
        # ASCII-escaped JSON stays machine-readable on consoles without CJK support.
        print(json.dumps(report, ensure_ascii=True, indent=2, sort_keys=False))
    else:
        print_summary(report)
    return 1 if report["findings"] else 0


if __name__ == "__main__":
    sys.exit(main())
