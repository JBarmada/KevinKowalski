"""Shared types between the MCP server and any analyzer implementation.

The MCP server depends only on this file. The fake analyzer satisfies it today;
the real analyzer (built by the metrics team) will satisfy it later. Swapping
between them is a one-line import change in mcp_server.py.

Anyone building an analyzer: implement the Analyzer protocol, return GraphSnapshot
instances populated with ModuleMetrics. That's the entire surface area.
"""

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class ModuleMetrics:
    module: str                # dotted name, e.g. "handlers.user"
    path: str                  # repo-relative path, e.g. "handlers/user.py"
    ca: int                    # afferent coupling: how many modules import this one
    ce: int                    # efferent coupling: how many modules this one imports
    instability: float         # ce / (ca + ce); 0.0 if both are zero
    lcom4: float | None        # cohesion (1 = cohesive, >1 = split candidate); None if no classes
    cc_max: int                # worst function's cyclomatic complexity in this module
    violations: list[str] = field(default_factory=list)
    # rule IDs that fired, e.g. ["SDP", "GOD_MODULE", "CYCLE"]


@dataclass
class GraphSnapshot:
    root: str                              # absolute repo path that was analyzed
    modules: dict[str, ModuleMetrics]      # keyed by ModuleMetrics.module
    edges: list[tuple[str, str]]           # (importer_module, importee_module)


class Analyzer(Protocol):
    """The contract every analyzer must satisfy.

    Sync only. No async — keeps MCP tool wiring simple.
    """

    def analyze(self, repo_path: str) -> GraphSnapshot:
        """Full analysis of a repo. May be cached internally by the implementation."""
        ...

    def incremental_check(self, repo_path: str, files: list[str]) -> dict:
        """Re-analyze only the given files and return before/after deltas.

        Return shape (agreed with formatter):
            {
                "changed": [{"module": str, "before": ModuleMetrics, "after": ModuleMetrics}, ...],
                "new_violations": list[str],
                "resolved_violations": list[str],
                "verdict": "green" | "yellow" | "red",
            }
        """
        ...
