"""Compute dependency and complexity metrics for a Python codebase."""

import ast
import pathlib

import jedi
import networkx as nx
from pydantic import BaseModel
from pyvis.network import Network


class NodeMetrics(BaseModel, frozen=True):
    """Coupling metrics for a single node in a dependency graph."""

    ca: int  # afferent coupling — how many nodes depend on this one
    ce: int  # efferent coupling — how many nodes this one depends on
    instability: float  # ce / (ca + ce); 0 = maximally stable, 1 = maximally unstable
    # Impact: weighted Ca of this node plus weighted sum of Ca over dependents
    # (nodes with an edge into this node; in a caller→callee graph, predecessors).
    impact: float
    # Susceptibility: weighted Ce of this node plus weighted sum of Ce over dependencies
    # (nodes this node has an edge to; successors).
    susceptibility: float


def compute_metrics(
    graph: nx.DiGraph,
    *,
    coef_impact_ca_node: float = 1.0,
    coef_impact_sum_ca_dependents: float = 1.0,
    coef_susceptibility_ce_node: float = 1.0,
    coef_susceptibility_sum_ce_dependencies: float = 1.0,
) -> dict[str, NodeMetrics]:
    """Compute Ca, Ce, instability, impact, and susceptibility for every node.

    With an edge A → B meaning A depends on B (e.g. A calls B), B's dependents are
    its predecessors and B's dependencies are its successors.

    Impact = coef_impact_ca_node * Ca(node)
           + coef_impact_sum_ca_dependents * (sum of Ca over dependents).

    Susceptibility = coef_susceptibility_ce_node * Ce(node)
                   + coef_susceptibility_sum_ce_dependencies * (sum of Ce over dependencies).

    Each ``coef_*`` scales one whole term; summations are unweighted inside the sum.
    """
    # First pass: Ca/Ce/instability (impact/susceptibility need neighbors' Ca/Ce).
    base: dict[str, tuple[int, int, float]] = {}
    for node in graph.nodes():
        ca = graph.in_degree(node)
        ce = graph.out_degree(node)
        # NOTE: [pedagogical] isolated nodes (no edges at all) get instability 0.5
        # — they are neither stable nor unstable, so we default to the midpoint
        # rather than dividing by zero.
        instability = ce / (ca + ce) if (ca + ce) > 0 else 0.5
        base[node] = (ca, ce, instability)

    metrics: dict[str, NodeMetrics] = {}
    for node in graph.nodes():
        ca, ce, instability = base[node]
        # Raw sums (no coefficients applied inside the summation).
        sum_ca_dependents = sum(base[p][0] for p in graph.predecessors(node))
        sum_ce_dependencies = sum(base[s][1] for s in graph.successors(node))
        impact = coef_impact_ca_node * ca + coef_impact_sum_ca_dependents * sum_ca_dependents
        susceptibility = (
            coef_susceptibility_ce_node * ce
            + coef_susceptibility_sum_ce_dependencies * sum_ce_dependencies
        )
        metrics[node] = NodeMetrics(
            ca=ca,
            ce=ce,
            instability=instability,
            impact=impact,
            susceptibility=susceptibility,
        )
    return metrics


def node_id(name: str, module_path: pathlib.Path, line: int) -> str:
    """Return a unique string identifier for a function definition."""
    # NOTE: [thought process] dots and colons in node IDs confuse pyvis's edge
    # serialization, so we use only underscores.
    return f"{module_path.stem}__{name}__{line}"


def _get_file_dependency_graph(root: pathlib.Path) -> nx.DiGraph:
    """Build a directed graph where nodes are files and edges are import relationships."""
    graph = nx.DiGraph()
    files = sorted(root.rglob("*.py"))

    for filepath in files:
        graph.add_node(str(filepath.relative_to(root)))
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)

        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                if node.module is None:
                    continue
                modules = [node.module]
            else:
                continue

            for module in modules:
                # Convert dotted module name to a candidate file path within root.
                # NOTE: [edge case callout] bare relative imports (from . import x)
                # have node.module == None and are skipped above.
                module_as_path = module.replace(".", "/")
                candidates = [
                    root / (module_as_path + ".py"),
                    root / module_as_path / "__init__.py",
                ]
                for candidate in candidates:
                    if candidate.exists():
                        graph.add_node(str(candidate.relative_to(root)))
                        graph.add_edge(
                            str(filepath.relative_to(root)),
                            str(candidate.relative_to(root)),
                        )
                        break

    return graph


def _get_function_dependency_graph(root: pathlib.Path) -> nx.DiGraph:
    """Build a directed graph where nodes are functions and edges are call relationships."""
    graph = nx.DiGraph()
    project = jedi.Project(root)
    files = sorted(root.rglob("*.py"))

    for filepath in files:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)
        script = jedi.Script(path=str(filepath), project=project)

        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue

            caller_id = node_id(function.name, filepath, function.lineno)
            graph.add_node(caller_id, label=function.name)

            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue

                # NOTE: [pedagogical] jedi.goto() resolves a name at a given
                # (line, column) to its definition, potentially in another file.
                # We point at the end of the func expression so jedi sees the
                # full dotted name rather than an intermediate attribute.
                call_line = node.func.end_lineno
                call_col = node.func.end_col_offset
                # NOTE: [edge case callout] for multi-line expressions, end_col_offset
                # can exceed the length of end_lineno's line — clamp it to be safe.
                source_line = source.splitlines()[call_line - 1]
                call_col = min(call_col, len(source_line))
                try:
                    definitions = script.goto(line=call_line, column=call_col)
                except Exception:
                    continue

                for definition in definitions:
                    if definition.type != "function":
                        continue
                    if definition.module_path is None:
                        continue

                    # NOTE: [thought process] skip definitions outside our source
                    # dir — stdlib and third-party calls aren't what we want to map.
                    if not str(definition.module_path).startswith(str(root)):
                        continue

                    callee_id = node_id(
                        definition.name, definition.module_path, definition.line
                    )
                    graph.add_node(callee_id, label=definition.name)
                    graph.add_edge(caller_id, callee_id)

    return graph


def get_metrics(root: pathlib.Path):
    graph = _get_function_dependency_graph(root)
    # graph = _get_file_dependency_graph(root)
    metrics = compute_metrics(graph)

    for node, m in sorted(metrics.items()):
        print(
            f"{node}: Ca={m.ca}, Ce={m.ce}, I={m.instability:.2f}, "
            f"Impact={m.impact:.2f}, Susceptibility={m.susceptibility:.2f}"
        )

    net = Network(directed=True, height="100vh", width="100%", cdn_resources="remote")
    net.from_nx(graph)
    print(graph.number_of_edges())
    output_path = pathlib.Path("graph.html")
    output_path.write_text(net.generate_html())
    # net.show("graph.html", notebook=False)


if __name__ == "__main__":
    import sys

    root = pathlib.Path(sys.argv[1]).resolve()
    get_metrics(root)
