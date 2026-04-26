"""Kowalski-Kevin MCP server.

Exposes 5 tools that let an AI coding agent query architectural metrics
about a Python repo before/while making changes. Built against the
Analyzer protocol in contract.py; today backed by fake_analyzer, later
swapped for the real one with a one-line import change.

CRITICAL: This process speaks JSON-RPC over stdout. Anything that prints
to stdout corrupts the protocol stream. All logging goes to stderr.
Never use print() in this module or anything it imports at runtime.
"""

import logging
import os
import sys
import traceback
from functools import wraps

from fastmcp import FastMCP

from real_analyzer import get_analyzer
from formatters import (
    format_analyze_repo,
    format_check_change,
    format_metric_graph,
    format_module_health,
    format_suggest_refactor,
)


logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("kowalski-kevin")

mcp = FastMCP("KowalskiKevin")
_analyzer = get_analyzer()


def _safe_tool(fn):
    """Decorator: trap exceptions, log to stderr, return readable string to agent.

    A raised exception inside an MCP tool surfaces as an opaque protocol error
    on the agent side. Returning a string lets the agent reason about the
    failure (and, often, fix its own arguments and retry).
    """
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:
            log.exception("tool %s failed", fn.__name__)
            return (
                f"Error in `{fn.__name__}`: {type(e).__name__}: {e}\n"
                "(Server-side traceback was logged to stderr.)"
            )
    return wrapper


def _resolve_path(path: str) -> str:
    """Normalize the path arg.

    `.` and empty strings are rejected: when the MCP server is launched by a
    host like Claude Code, os.getcwd() is the host's launch directory (often
    C:\\WINDOWS\\System32), not the user's repo. Forcing an explicit path
    eliminates a whole class of "I analyzed the wrong directory" bugs.
    """
    if not path or path in (".", "./"):
        raise ValueError(
            "path must be an absolute path to the repo to analyze. "
            "'.' is rejected because the MCP server's CWD is the host's "
            "launch directory (e.g. System32), not your project. "
            "Pass the absolute repo path explicitly."
        )
    return os.path.abspath(path)


@mcp.tool()
@_safe_tool
def analyze_repo(path: str) -> str:
    """Run a full architectural analysis of a Python repo.

    Returns a Markdown summary: module count, edge count, average instability,
    and the top modules by violation severity. Call this first to ground any
    refactoring or feature work.

    Args:
        path: Absolute filesystem path to the repo root. Required -- '.' and
            empty strings are rejected because the MCP server's CWD is the
            host's launch dir, not the user's project.
    """
    repo = _resolve_path(path)
    log.info("analyze_repo: %s", repo)
    snapshot = _analyzer.analyze(repo)
    return format_analyze_repo(snapshot)


@mcp.tool()
@_safe_tool
def module_health(path: str, module: str) -> str:
    """Get a per-module health card.

    Returns Markdown with the module's Ca/Ce/instability/LCOM/CC, any rule
    violations with plain-English explanations, and which modules import or
    are imported by it.

    Args:
        path: Filesystem path to the repo root.
        module: Dotted module name, e.g. "handlers.user".
    """
    repo = _resolve_path(path)
    log.info("module_health: %s in %s", module, repo)
    snapshot = _analyzer.analyze(repo)
    return format_module_health(snapshot, module)


@mcp.tool()
@_safe_tool
def suggest_refactor(path: str, feature_description: str) -> str:
    """Given a feature you're about to implement, list decouplings to do FIRST.

    Identifies modules whose existing structural problems would be amplified by
    the planned change, ranked by severity. Each entry includes a rationale.

    Args:
        path: Filesystem path to the repo root.
        feature_description: Natural-language description of the feature being added.
    """
    repo = _resolve_path(path)
    log.info("suggest_refactor: %s -- %r", repo, feature_description[:80])
    snapshot = _analyzer.analyze(repo)
    return format_suggest_refactor(snapshot, feature_description)


@mcp.tool()
@_safe_tool
def check_change(path: str, files: list[str]) -> str:
    """Re-analyze recently-modified files and report metric deltas.

    Use after making edits to confirm the change improved (or didn't worsen)
    the architecture. Returns before/after metrics and a green/yellow/red
    verdict.

    Args:
        path: Filesystem path to the repo root.
        files: Repo-relative paths of files just modified.
    """
    repo = _resolve_path(path)
    log.info("check_change: %s files=%s", repo, files)
    result = _analyzer.incremental_check(repo, files)
    return format_check_change(result)


@mcp.tool()
@_safe_tool
def get_metric_graph(path: str) -> str:
    """Return the metric graph as JSON (nodes + edges) for visualization.

    Same shape consumed by the upcoming graph viewer. Useful when an agent or
    UI wants raw data instead of formatted prose.

    Args:
        path: Absolute filesystem path to the repo root. Required -- '.' and
            empty strings are rejected.
    """
    repo = _resolve_path(path)
    log.info("get_metric_graph: %s", repo)
    snapshot = _analyzer.analyze(repo)
    return format_metric_graph(snapshot)


if __name__ == "__main__":
    log.info("Kowalski-Kevin MCP server starting (stdio transport)")
    mcp.run()
