"""Parsing and metric utilities for dependency graph analysis."""

import ast
from dataclasses import dataclass
from pathlib import Path

import networkx as nx


@dataclass(frozen=True)
class Edge:
    """A directed dependency edge between two modules."""

    src: str
    dst: str
    is_dynamic: bool = False


@dataclass(frozen=True)
class NodeMetrics:
    """Coupling metrics for a single module."""

    ca: int  # Afferent coupling (in-degree): how many modules import this one
    ce: int  # Efferent coupling (out-degree): how many modules this one imports
    instability: float  # Ce / (Ca + Ce): 0=stable, 1=unstable


class _ImportVisitor(ast.NodeVisitor):
    """AST visitor that collects imports with dynamic/static classification."""

    def __init__(self) -> None:
        self.imports: list[tuple[ast.ImportFrom, bool]] = []
        self._function_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if node.level > 0:  # relative import only
            is_dynamic = self._function_depth > 0
            self.imports.append((node, is_dynamic))


def _module_name(filepath: Path, source_root: Path) -> tuple[str, bool]:
    """Derive a dotted module name relative to source_root.

    Returns:
        (module_name, is_package): is_package is True for __init__.py files
    """
    parts = filepath.relative_to(source_root).with_suffix("").parts
    is_package = bool(parts) and parts[-1] == "__init__"
    if is_package:
        parts = parts[:-1]
    name = ".".join(parts) if parts else "__init__"
    return name, is_package


def _type_checking_guard_ids(tree: ast.AST) -> set[int]:
    """Return node ids of ImportFrom statements guarded by TYPE_CHECKING."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if is_type_checking:
            for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if isinstance(child, ast.ImportFrom):
                    guarded.add(id(child))
    return guarded


def parse_edges(source_root: Path) -> list[Edge]:
    """Parse all Python files under source_root and extract dependency edges.

    Detects:
    - Static imports (module-level)
    - Dynamic imports (inside functions)
    - Excludes TYPE_CHECKING-guarded imports
    """
    edges: list[Edge] = []

    for filepath in sorted(source_root.rglob("*.py")):
        try:
            source_text = filepath.read_text(encoding="utf-8")
            tree = ast.parse(source_text)
        except (SyntaxError, UnicodeDecodeError):
            continue

        module, is_package = _module_name(filepath, source_root)
        tc_ids = _type_checking_guard_ids(tree)

        visitor = _ImportVisitor()
        visitor.visit(tree)

        for node, is_dynamic in visitor.imports:
            if id(node) in tc_ids:
                continue

            # For __init__.py, the module IS the package, so don't go up for level=1
            # Special case: root __init__.py has module name "__init__" but should resolve as ""
            if module == "__init__":
                package_parts = []
            else:
                package_parts = module.split(".")

            effective_level = node.level - 1 if is_package else node.level
            if effective_level <= 0:
                parent_parts = package_parts
            else:
                parent_parts = package_parts[:-effective_level] if len(package_parts) >= effective_level else []

            if node.module:
                target = ".".join(parent_parts + node.module.split("."))
                if target and target != module:
                    edges.append(Edge(module, target, is_dynamic))
            else:
                for alias in node.names:
                    target = ".".join(parent_parts + [alias.name])
                    if target and target != module:
                        edges.append(Edge(module, target, is_dynamic))

    # Deduplicate, preferring static over dynamic when both exist
    edge_map: dict[tuple[str, str], bool] = {}
    for e in edges:
        key = (e.src, e.dst)
        if key not in edge_map:
            edge_map[key] = e.is_dynamic
        elif edge_map[key] and not e.is_dynamic:
            edge_map[key] = False  # static wins

    return [Edge(k[0], k[1], v) for k, v in edge_map.items()]


def aggregate_to_packages(edges: list[Edge], depth: int = 1) -> list[Edge]:
    """Collapse file-level edges to package-level.

    Args:
        edges: File-level edges
        depth: How many dotted components to keep (1 = top-level package)
    """

    def to_package(name: str) -> str:
        parts = name.split(".")
        return ".".join(parts[:depth]) if len(parts) >= depth else name

    pkg_map: dict[tuple[str, str], bool] = {}
    for e in edges:
        src_pkg = to_package(e.src)
        dst_pkg = to_package(e.dst)
        if src_pkg != dst_pkg:
            key = (src_pkg, dst_pkg)
            if key not in pkg_map:
                pkg_map[key] = e.is_dynamic
            elif pkg_map[key] and not e.is_dynamic:
                pkg_map[key] = False

    return [Edge(k[0], k[1], v) for k, v in pkg_map.items()]


def compute_metrics(graph: nx.DiGraph) -> dict[str, NodeMetrics]:
    """Compute coupling metrics for each node in the graph."""
    metrics: dict[str, NodeMetrics] = {}
    for node in graph.nodes():
        ca = graph.in_degree(node)
        ce = graph.out_degree(node)
        total = ca + ce
        instability = ce / total if total > 0 else 0.5
        metrics[node] = NodeMetrics(ca=ca, ce=ce, instability=instability)
    return metrics


def find_cycle_info(graph: nx.DiGraph) -> tuple[set[str], set[tuple[str, str]]]:
    """Find all nodes and edges that participate in cycles.

    Returns:
        (cycle_nodes, cycle_edges): Sets of nodes and (src, dst) edges in cycles
    """
    cycle_nodes: set[str] = set()
    cycle_edges: set[tuple[str, str]] = set()

    for cycle in nx.simple_cycles(graph):
        for node in cycle:
            cycle_nodes.add(node)
        for i in range(len(cycle)):
            src = cycle[i]
            dst = cycle[(i + 1) % len(cycle)]
            cycle_edges.add((src, dst))

    return cycle_nodes, cycle_edges


def shorten_label(name: str, max_parts: int = 2) -> str:
    """Shorten a dotted module name for display.

    'flask.json.provider' -> 'json.provider'
    'flask' -> 'flask'
    """
    parts = name.split(".")
    if len(parts) <= max_parts:
        return name
    return ".".join(parts[-max_parts:])


def get_package_groups(nodes: list[str]) -> dict[str, str]:
    """Map each node to its top-level package for grouping."""
    return {node: node.split(".")[0] for node in nodes}
