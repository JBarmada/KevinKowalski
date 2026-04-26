"""Stable node identifiers for function-level graphs (shared by basic and CHA builders)."""

import pathlib


def node_id(name: str, module_path: pathlib.Path, line: int) -> str:
    """Return a unique string identifier for a function definition."""
    return f"{module_path.stem}__{name}__{line}"
