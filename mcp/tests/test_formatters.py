"""Tier 2: formatter tests.

Build synthetic GraphSnapshots in-test, pass to formatters, assert on
structure -- never exact prose. These also survive the analyzer swap
because they don't touch the analyzer at all.
"""

import json
from pathlib import Path

from contract import GraphSnapshot, ModuleMetrics
from formatters import (
    format_analyze_repo,
    format_check_change,
    format_generate_graph,
    format_metric_graph,
    format_module_health,
    format_refactor_assistance,
    format_suggest_refactor,
    viz_html_path_from_generate_stdout,
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


def test_suggest_refactor_rationales_are_bulleted_not_joined():
    """Each violation should be its own bullet, not semicolon-joined.

    Regression: '; '.join produced ugly 'foo.; Bar...' rendering.
    """
    out = format_suggest_refactor(_mini_snapshot(), "x")
    # "b" has two violations; should produce at least two distinct "   - " bullets
    bullet_count = out.count("   - ")
    assert bullet_count >= 2, f"expected per-violation bullets; got:\n{out}"
    # And the old joined-with-semicolon style must be gone
    assert ";" not in out or ".; " not in out


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


def test_generate_graph_renders_summary():
    result = {
        "output_path": "/tmp/graph.html",
        "file_nodes": 10,
        "file_edges": 15,
        "package_nodes": 3,
        "package_edges": 4,
        "function_nodes": 20,
        "function_edges": 25,
        "file_cycle_count": 2,
        "high_impact_count": 1,
        "high_susceptibility_count": 3,
    }
    out = format_generate_graph(result)
    assert isinstance(out, str) and out
    assert "10" in out  # file nodes
    assert "15" in out  # file edges
    assert "/tmp/graph.html" in out


def test_viz_html_path_from_generate_stdout_relative():
    stdout = "x\nGenerated: visualization/output/C_repo_stem.html\n"
    cwd = Path("/workspace/repo")
    p = viz_html_path_from_generate_stdout(stdout, cwd)
    assert p == (cwd / "visualization/output/C_repo_stem.html").resolve()


def test_viz_html_path_from_generate_stdout_absolute():
    stdout = "Generated: /tmp/abs.html\n"
    p = viz_html_path_from_generate_stdout(stdout, Path("/ignored"))
    assert p == Path("/tmp/abs.html").resolve()


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


def test_refactor_assistance_formatter_smoke():
    payload = {
        "root": "/repo",
        "levels": {
            "package": {
                "node_count": 2,
                "edge_count": 1,
                "high_susceptibility_detail": [
                    {
                        "id": "pkg_b",
                        "metrics": {
                            "ca": 1,
                            "ce": 2,
                            "instability": 0.5,
                            "impact": 0.2,
                            "susceptibility": 0.9,
                            "raw_impact": 1.0,
                            "raw_susceptibility": 9.0,
                        },
                        "high_impact_dependents": [
                            {
                                "id": "pkg_a",
                                "ca": 2,
                                "ce": 0,
                                "instability": 0.0,
                                "impact": 0.9,
                                "susceptibility": 0.1,
                                "raw_impact": 8.0,
                                "raw_susceptibility": 1.0,
                            }
                        ],
                    }
                ],
                "high_impact_detail": [
                    {
                        "id": "pkg_a",
                        "metrics": {
                            "ca": 2,
                            "ce": 0,
                            "instability": 0.0,
                            "impact": 0.9,
                            "susceptibility": 0.1,
                            "raw_impact": 8.0,
                            "raw_susceptibility": 1.0,
                        },
                        "high_susceptibility_dependencies": [
                            {
                                "id": "pkg_b",
                                "ca": 1,
                                "ce": 2,
                                "instability": 0.5,
                                "impact": 0.2,
                                "susceptibility": 0.9,
                                "raw_impact": 1.0,
                                "raw_susceptibility": 9.0,
                            }
                        ],
                    }
                ],
            },
            "file": {
                "node_count": 0,
                "edge_count": 0,
                "high_susceptibility_detail": [],
                "high_impact_detail": [],
            },
            "function": {
                "node_count": 0,
                "edge_count": 0,
                "high_susceptibility_detail": [],
                "high_impact_detail": [],
            },
        },
    }
    out = format_refactor_assistance(payload)
    assert "pkg_b" in out and "pkg_a" in out
    assert "Package level" in out and "Function level" in out


def test_generate_graph_no_issues():
    result = {
        "output_path": "/tmp/graph.html",
        "file_nodes": 5,
        "file_edges": 3,
        "package_nodes": 2,
        "package_edges": 1,
        "function_nodes": 8,
        "function_edges": 6,
        "file_cycle_count": 0,
        "high_impact_count": 0,
        "high_susceptibility_count": 0,
    }
    out = format_generate_graph(result)
    assert "no architectural concerns" in out.lower()
