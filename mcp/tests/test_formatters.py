"""Tier 2: formatter tests.

Build synthetic GraphSnapshots in-test, pass to formatters, assert on
structure -- never exact prose. These also survive the analyzer swap
because they don't touch the analyzer at all.
"""

import json

from contract import GraphSnapshot, ModuleMetrics
from formatters import (
    format_analyze_repo,
    format_check_change,
    format_metric_graph,
    format_module_health,
    format_suggest_refactor,
)


def _mini_snapshot() -> GraphSnapshot:
    """Hand-built minimal snapshot for formatter tests."""
    a = ModuleMetrics("a", "a.py", ca=2, ce=0, instability=0.0, lcom4=1.0, cc_max=3, violations=[])
    b = ModuleMetrics("b", "b.py", ca=0, ce=2, instability=1.0, lcom4=None, cc_max=8, violations=["SDP", "HIGH_CC"])
    c = ModuleMetrics("c", "c.py", ca=1, ce=1, instability=0.5, lcom4=2.0, cc_max=4, violations=[])
    return GraphSnapshot(
        root="/x",
        modules={"a": a, "b": b, "c": c},
        edges=[("b", "a"), ("b", "c"), ("c", "a")],
    )


# Generous cap; keeps catastrophic verbosity from sneaking in.
TOKEN_BUDGET_CHARS = 4000


def test_analyze_repo_renders_summary():
    out = format_analyze_repo(_mini_snapshot())
    assert isinstance(out, str) and out
    assert len(out) <= TOKEN_BUDGET_CHARS
    # Must surface module count so the agent can ground its reasoning
    assert "3" in out


def test_analyze_repo_lists_offenders():
    out = format_analyze_repo(_mini_snapshot())
    # Module b has violations; should appear in offenders
    assert "b" in out


def test_analyze_repo_no_violations_path():
    snap = GraphSnapshot(
        root="/x",
        modules={"a": ModuleMetrics("a", "a.py", 0, 0, 0.0, None, 1, [])},
        edges=[],
    )
    out = format_analyze_repo(snap)
    assert "no violations" in out.lower() or "0" in out


def test_module_health_known_module_includes_name_and_metrics():
    out = format_module_health(_mini_snapshot(), "b")
    assert "b" in out
    # Metric labels must appear so the output is grounded
    for label in ("Ca", "Ce", "Instability", "cyclomatic"):
        assert label.lower() in out.lower()
    assert len(out) <= TOKEN_BUDGET_CHARS


def test_module_health_lists_violations():
    out = format_module_health(_mini_snapshot(), "b")
    assert "SDP" in out
    assert "HIGH_CC" in out


def test_module_health_unknown_module_returns_friendly_error():
    out = format_module_health(_mini_snapshot(), "nope")
    assert "not found" in out.lower()
    # Should suggest known names
    assert "a" in out and "b" in out


def test_suggest_refactor_ranks_violators_first():
    out = format_suggest_refactor(_mini_snapshot(), "add a logging feature")
    # b has the violations, must appear; a/c have none, may or may not
    assert "b" in out
    assert len(out) <= TOKEN_BUDGET_CHARS


def test_suggest_refactor_clean_repo_says_proceed():
    snap = GraphSnapshot(
        root="/x",
        modules={"a": ModuleMetrics("a", "a.py", 0, 0, 0.0, None, 1, [])},
        edges=[],
    )
    out = format_suggest_refactor(snap, "build a thing")
    assert "proceed" in out.lower() or "no structural" in out.lower()


def test_check_change_renders_verdict():
    before = ModuleMetrics("a", "a.py", 1, 5, 0.83, 3.0, 12, ["GOD_MODULE"])
    after = ModuleMetrics("a", "a.py", 1, 2, 0.67, 1.0, 4, [])
    result = {
        "changed": [{"module": "a", "before": before, "after": after}],
        "new_violations": [],
        "resolved_violations": ["GOD_MODULE"],
        "verdict": "green",
    }
    out = format_check_change(result)
    assert "GREEN" in out.upper()
    assert "a" in out
    assert "GOD_MODULE" in out
    assert "->" in out  # before/after arrow


def test_check_change_handles_red_verdict():
    out = format_check_change({"changed": [], "new_violations": ["CYCLE"], "resolved_violations": [], "verdict": "red"})
    assert "RED" in out.upper()
    assert "CYCLE" in out


def test_metric_graph_returns_valid_json():
    out = format_metric_graph(_mini_snapshot())
    parsed = json.loads(out)  # must not raise
    assert "nodes" in parsed and "edges" in parsed
    assert len(parsed["nodes"]) == 3
    assert len(parsed["edges"]) == 3


def test_metric_graph_node_shape():
    parsed = json.loads(format_metric_graph(_mini_snapshot()))
    node = parsed["nodes"][0]
    assert "id" in node and "metrics" in node and "violations" in node
    for k in ("ca", "ce", "instability", "lcom4", "cc_max"):
        assert k in node["metrics"]


def test_metric_graph_edge_shape():
    parsed = json.loads(format_metric_graph(_mini_snapshot()))
    edge = parsed["edges"][0]
    assert "from" in edge and "to" in edge
