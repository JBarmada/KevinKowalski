"""Compute dependency and complexity metrics for a Python codebase."""

import ast
import pathlib

import jedi
import networkx as nx
from pydantic import BaseModel
from pyvis.network import Network


class NodeMetrics(BaseModel, frozen=True):
    """Coupling metrics for a single node in a dependency graph."""

    ca: int            # afferent coupling — how many nodes depend on this one
    ce: int            # efferent coupling — how many nodes this one depends on
    instability: float  # ce / (ca + ce); 0 = maximally stable, 1 = maximally unstable


def compute_metrics(graph: nx.DiGraph) -> dict[str, NodeMetrics]:
    """Compute Ca, Ce, and instability for every node in a dependency graph."""
    metrics = {}
    for node in graph.nodes():
        ca = graph.in_degree(node)
        ce = graph.out_degree(node)
        # NOTE: [pedagogical] isolated nodes (no edges at all) get instability 0.5
        # — they are neither stable nor unstable, so we default to the midpoint
        # rather than dividing by zero.
        instability = ce / (ca + ce) if (ca + ce) > 0 else 0.5
        metrics[node] = NodeMetrics(ca=ca, ce=ce, instability=instability)
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
    graph = _get_file_dependency_graph(root)
    metrics = compute_metrics(graph)

    for node, m in sorted(metrics.items()):
        print(f"{node}: Ca={m.ca}, Ce={m.ce}, I={m.instability:.2f}")

    net = Network(directed=True)
    net.from_nx(graph)
    # NOTE: [thought process] the defaults cluster tightly because centralGravity
    # pulls hard and springLength is short. Cranking up repulsion and spring length
    # lets the graph breathe.
    net.set_options("""{
        "physics": {
            "barnesHut": {
                "gravitationalConstant": -8000,
                "centralGravity": 0.05,
                "springLength": 250,
                "springConstant": 0.01,
                "damping": 0.15
            }
        }
    }""")
    net.show("graph.html", notebook=False)
    """
    we need to output the following:
        - Ca
        - Ce
        - Instability
        - Cyclomatic complexity
        - github change frequency?

    for both files and functions.

    then we say what is likely to change.

    output as some kind of structured data; they'll handle it.

    also can pass repo-level information in addition to the dicts of function-value and file-value pairs


    we're looking at how likely changes are to propagate
    average instability of dependencies as proxy for how likely this is to change
    also look at is it actively being developed

    look at probability of receving incidental update
        - every piece of code should have lowest chance possible of being forced to change.


    focus on this stuff for files (files vs functions is kind of just a zoom in zoom out kind of thing)



    we really do want to just limit the probability of changes propagating.


    your understanding does not have to propagate either
    """


if __name__ == "__main__":
    import sys

    root = pathlib.Path(sys.argv[1])
    get_metrics(root)
