"""Compute dependency and complexity metrics for a Python codebase."""

import ast
import pathlib

import jedi
import networkx as nx
from pydantic import BaseModel
from pyvis.network import Network

try:
    from .graph_ids import node_id
except ImportError:
    from graph_ids import node_id


class NodeMetrics(BaseModel, frozen=True):
    """Coupling metrics for a single node in a dependency graph."""

    ca: float  # weighted afferent coupling (sum of incoming edge weights; 1 per edge if unweighted)
    ce: float  # weighted efferent coupling (sum of outgoing edge weights)
    instability: float  # ce / (ca + ce); 0 = maximally stable, 1 = maximally unstable
    impact: float
    susceptibility: float


def _edge_weight(graph: nx.DiGraph, u: str, v: str) -> float:
    return float(graph[u][v].get("weight", 1.0))


def _weighted_in_degree(graph: nx.DiGraph, n: str) -> float:
    return sum(_edge_weight(graph, p, n) for p in graph.predecessors(n))


def _weighted_out_degree(graph: nx.DiGraph, n: str) -> float:
    return sum(_edge_weight(graph, n, s) for s in graph.successors(n))


def compute_metrics(
    graph: nx.DiGraph,
    *,
    coef_impact_ca_node: float = 1.0,
    coef_impact_sum_ca_dependents: float = 1.0,
    coef_susceptibility_ce_node: float = 1.0,
    coef_susceptibility_sum_ce_dependencies: float = 1.0,
) -> dict[str, NodeMetrics]:
    """Compute Ca, Ce, instability, impact, and susceptibility for every node.

    Edges may carry a ``weight`` (default 1). Ca and Ce sum incoming/outgoing
    weights instead of counting each edge as 1.
    """
    base: dict[str, tuple[float, float, float]] = {}
    for node in graph.nodes():
        ca = _weighted_in_degree(graph, node)
        ce = _weighted_out_degree(graph, node)
        instability = ce / (ca + ce) if (ca + ce) > 0 else 0.5
        base[node] = (ca, ce, instability)

    metrics: dict[str, NodeMetrics] = {}
    for node in graph.nodes():
        ca, ce, instability = base[node]
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


def _get_basic_function_dependency_graph(root: pathlib.Path) -> nx.DiGraph:
    """Minimal call graph: ``def`` bodies only, Jedi ``goto``, unweighted edges."""
    graph = nx.DiGraph()
    project = jedi.Project(root)
    files = sorted(root.rglob("*.py"))
    root_s = str(root.resolve())

    for filepath in files:
        source = filepath.read_text(encoding="utf-8")
        lines = source.splitlines()
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

                call_line = node.func.end_lineno
                call_col = node.func.end_col_offset
                source_line = lines[call_line - 1]
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
                    if not str(definition.module_path).startswith(root_s):
                        continue

                    callee_id = node_id(
                        definition.name, pathlib.Path(definition.module_path), definition.line
                    )
                    graph.add_node(callee_id, label=definition.name)
                    graph.add_edge(caller_id, callee_id)
                    break

    return graph


def _get_file_dependency_graph(root: pathlib.Path) -> nx.DiGraph:
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


def get_function_dependency_graph(root: pathlib.Path, *, cha: bool = False) -> nx.DiGraph:
    """Return a function-level call graph; set ``cha=True`` for CHA-weighted analysis."""
    if cha:
        try:
            from .call_graph_cha import build_cha_weighted_call_graph
        except ImportError:
            from call_graph_cha import build_cha_weighted_call_graph

        return build_cha_weighted_call_graph(root)
    return _get_basic_function_dependency_graph(root)


def get_metrics(root: pathlib.Path, *, cha: bool = False) -> None:
    """Build graph, print metrics, write ``graph.html``. Pass ``cha=True`` to use optional CHA builder."""
    graph = get_function_dependency_graph(root, cha=cha)
    metrics = compute_metrics(graph)

    for node, m in sorted(metrics.items()):
        print(
            f"{node}: Ca={m.ca:.2f}, Ce={m.ce:.2f}, I={m.instability:.2f}, "
            f"Impact={m.impact:.2f}, Susceptibility={m.susceptibility:.2f}"
        )

    net = Network(directed=True, height="100vh", width="100%", cdn_resources="remote")
    net.from_nx(graph)
    print(graph.number_of_edges())
    output_path = pathlib.Path("graph.html")
    output_path.write_text(net.generate_html())


if __name__ == "__main__":
    import sys

    args = [a for a in sys.argv[1:] if a != "--cha"]
    cha = "--cha" in sys.argv[1:]
    if not args:
        raise SystemExit("usage: python -m metrics.metrics <project_root> [--cha]")
    root = pathlib.Path(args[0]).resolve()
    get_metrics(root, cha=cha)
