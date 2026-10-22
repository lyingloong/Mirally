"""Resolve the assistant workspace independently from the development checkout."""

from __future__ import annotations

import os
from pathlib import Path

from mirally.runtime.activity.config import default_data_directory


def resolve_workspace(product_root: Path, override: Path | str | None = None) -> Path:
    """Defaults stay outside repositories; an explicitly chosen workspace wins."""
    root = Path(product_root).resolve()
    if override is not None:
        path = Path(os.path.expandvars(os.path.expanduser(str(override))))
        return (path if path.is_absolute() else root / path).resolve()
    workspace = (default_data_directory().parent / "openclaw" / "workspace").resolve()
    if workspace == root or root in workspace.parents or any(
        (parent / ".git").exists() for parent in (workspace, *workspace.parents)
    ):
        raise ValueError(
            "The default Mirally workspace is inside a Git checkout or product directory. "
            "Set LOCALAPPDATA to a user data directory, or pass --workspace with an explicit path."
        )
    return workspace
