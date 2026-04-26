"""Compute dependency and complexity metrics for a Python codebase."""

import ast
import pathlib

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

    sum_ca_dependents: dict[str, float] = {n: 0.0 for n in graph}
    sum_ce_dependencies: dict[str, float] = {n: 0.0 for n in graph}
    for n, s in graph.edges():
        sum_ca_dependents[s] += base[n][0]
        sum_ce_dependencies[n] += base[s][1]

    metrics: dict[str, NodeMetrics] = {}
    for node in graph.nodes():
        ca, ce, instability = base[node]
        impact = (
            coef_impact_ca_node * ca
            + coef_impact_sum_ca_dependents * sum_ca_dependents[node]
        )
        susceptibility = (
            coef_susceptibility_ce_node * ce
            + coef_susceptibility_sum_ce_dependencies * sum_ce_dependencies[node]
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
    from visualization.utils import build_function_graph

    graph, _ = build_function_graph(root)
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
