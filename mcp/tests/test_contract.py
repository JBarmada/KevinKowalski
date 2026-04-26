"""Tier 1: contract conformance.

Asserts the SHAPE of analyzer output, never specific values. Must pass
with any analyzer that satisfies the protocol -- this is the test file
that survives the swap from fake to real.
"""

from contract import GraphSnapshot, ModuleMetrics


def test_analyze_returns_graph_snapshot(snapshot):
    assert isinstance(snapshot, GraphSnapshot)
    assert isinstance(snapshot.root, str)
    assert isinstance(snapshot.modules, dict)
    assert isinstance(snapshot.edges, list)


def test_module_keys_match_module_field(snapshot):
    for key, m in snapshot.modules.items():
        assert key == m.module, f"dict key {key!r} != ModuleMetrics.module {m.module!r}"


def test_module_metrics_field_types(snapshot):
    for m in snapshot.modules.values():
        assert isinstance(m, ModuleMetrics)
        assert isinstance(m.module, str) and m.module
        assert isinstance(m.path, str) and m.path
        assert isinstance(m.ca, int)
        assert isinstance(m.ce, int)
        assert isinstance(m.instability, float)
        assert m.lcom4 is None or isinstance(m.lcom4, (int, float))
        assert isinstance(m.cc_max, int)
        assert isinstance(m.violations, list)
        assert all(isinstance(v, str) for v in m.violations)


def test_metric_value_ranges(snapshot):
    for m in snapshot.modules.values():
        assert m.ca >= 0, f"{m.module}: Ca must be non-negative"
        assert m.ce >= 0, f"{m.module}: Ce must be non-negative"
        assert 0.0 <= m.instability <= 1.0, f"{m.module}: I out of range"
        assert m.cc_max >= 0, f"{m.module}: CC must be non-negative"
        if m.lcom4 is not None:
            assert m.lcom4 >= 0


def test_edges_reference_known_modules(snapshot):
    """Edges may only point to modules listed in the snapshot.

    NOTE: this is a strong contract. If the real analyzer ever emits
    edges to external/unknown modules, relax to a warning rather than
    silently dropping the assertion.
    """
    known = set(snapshot.modules)
    for src, dst in snapshot.edges:
        assert src in known, f"edge source {src!r} not in modules"
        assert dst in known, f"edge target {dst!r} not in modules"


def test_incremental_check_shape(analyzer):
    result = analyzer.incremental_check("/fake/repo", ["any/file.py"])
    assert isinstance(result, dict)
    for key in ("changed", "new_violations", "resolved_violations", "verdict"):
        assert key in result, f"missing key: {key}"
    assert result["verdict"] in ("green", "yellow", "red")
    assert isinstance(result["changed"], list)
    for change in result["changed"]:
        assert "module" in change and "before" in change and "after" in change
        assert isinstance(change["before"], ModuleMetrics)
        assert isinstance(change["after"], ModuleMetrics)
