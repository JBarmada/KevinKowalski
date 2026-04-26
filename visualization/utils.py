"""Parsing and metric utilities for dependency graph analysis."""

import ast
from dataclasses import dataclass
from pathlib import Path

import jedi
import networkx as nx

_SKIP_DIRS = {".venv", ".git", "__pycache__", "node_modules", ".tox", ".eggs", ".mypy_cache"}


def _iter_python_files(source_root: Path) -> list[Path]:
    """Recursively find .py files, skipping virtual environments and hidden directories."""
    results: list[Path] = []
    for item in sorted(source_root.iterdir()):
        if item.name in _SKIP_DIRS or item.name.startswith("."):
            continue
        if item.is_file() and item.suffix == ".py":
            results.append(item)
        elif item.is_dir():
            results.extend(_iter_python_files(item))
    return results


@dataclass(frozen=True)
class Edge:
    """A directed dependency edge between two modules."""

    src: str
    dst: str
    is_dynamic: bool = False


@dataclass(frozen=True)
class NodeMetrics:
    """Coupling and architectural metrics for a single node."""

    ca: int  # Afferent coupling (in-degree): how many nodes depend on this one
    ce: int  # Efferent coupling (out-degree): how many nodes this one depends on
    instability: float  # Ce / (Ca + Ce): 0=stable, 1=unstable
    impact: float  # Normalized transitive dependents count [0,1]
    susceptibility: float  # Normalized transitive dependencies count [0,1]


class _ImportVisitor(ast.NodeVisitor):
    """AST visitor that collects imports with dynamic/static classification."""

    def __init__(self) -> None:
        self.import_froms: list[tuple[ast.ImportFrom, bool]] = []
        self.imports: list[tuple[ast.Import, bool]] = []
        self._function_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._function_depth += 1
        self.generic_visit(node)
        self._function_depth -= 1

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        is_dynamic = self._function_depth > 0
        self.import_froms.append((node, is_dynamic))

    def visit_Import(self, node: ast.Import) -> None:
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


def parse_edges(source_root: Path) -> tuple[list[Edge], set[str]]:
    """Parse all Python files under source_root and extract dependency edges.

    Returns:
        (edges, all_modules): edges list and set of all discovered module names
        so that isolated files (including root-level) are still represented.
    """
    edges: list[Edge] = []

    local_modules: set[str] = set()
    module_paths: dict[str, str] = {}
    py_files = _iter_python_files(source_root)
    for filepath in py_files:
        try:
            module, _ = _module_name(filepath, source_root)
            local_modules.add(module)
            module_paths[module] = str(filepath.relative_to(source_root))
            parts = module.split(".")
            for i in range(1, len(parts)):
                local_modules.add(".".join(parts[:i]))
        except ValueError:
            continue

    for filepath in py_files:
        try:
            source_text = filepath.read_text(encoding="utf-8")
            tree = ast.parse(source_text)
        except (SyntaxError, UnicodeDecodeError):
            continue

        module, is_package = _module_name(filepath, source_root)
        tc_ids = _type_checking_guard_ids(tree)

        visitor = _ImportVisitor()
        visitor.visit(tree)

        for node, is_dynamic in visitor.import_froms:
            if id(node) in tc_ids:
                continue

            if node.level > 0:
                if module == "__init__":
                    package_parts: list[str] = []
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
            else:
                if node.module:
                    target = node.module
                    if target in local_modules and target != module:
                        edges.append(Edge(module, target, is_dynamic))
                    else:
                        for alias in node.names:
                            candidate = f"{target}.{alias.name}" if target else alias.name
                            if candidate in local_modules and candidate != module:
                                edges.append(Edge(module, candidate, is_dynamic))
                else:
                    for alias in node.names:
                        if alias.name in local_modules and alias.name != module:
                            edges.append(Edge(module, alias.name, is_dynamic))

        for node, is_dynamic in visitor.imports:
            for alias in node.names:
                target = alias.name
                if target in local_modules and target != module:
                    edges.append(Edge(module, target, is_dynamic))
                else:
                    parts = target.split(".")
                    for i in range(1, len(parts) + 1):
                        prefix = ".".join(parts[:i])
                        if prefix in local_modules and prefix != module:
                            edges.append(Edge(module, prefix, is_dynamic))
                            break

    edge_map: dict[tuple[str, str], bool] = {}
    for e in edges:
        key = (e.src, e.dst)
        if key not in edge_map:
            edge_map[key] = e.is_dynamic
        elif edge_map[key] and not e.is_dynamic:
            edge_map[key] = False

    return [Edge(k[0], k[1], v) for k, v in edge_map.items()], local_modules


def aggregate_to_packages(edges: list[Edge], depth: int = 1) -> list[Edge]:
    """Collapse file-level edges to package-level."""

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
    """Compute coupling metrics including impact and susceptibility for each node."""
    reverse_graph = graph.reverse()

    transitive_dependents: dict[str, int] = {}
    transitive_dependencies: dict[str, int] = {}

    for node in graph.nodes():
        dependents = len(nx.descendants(reverse_graph, node))
        transitive_dependents[node] = dependents

        dependencies = len(nx.descendants(graph, node))
        transitive_dependencies[node] = dependencies

    max_dependents = max(transitive_dependents.values(), default=1) or 1
    max_dependencies = max(transitive_dependencies.values(), default=1) or 1

    metrics: dict[str, NodeMetrics] = {}
    for node in graph.nodes():
        ca = graph.in_degree(node)
        ce = graph.out_degree(node)
        total = ca + ce
        instability = ce / total if total > 0 else 0.5
        impact = transitive_dependents[node] / max_dependents
        susceptibility = transitive_dependencies[node] / max_dependencies

        metrics[node] = NodeMetrics(
            ca=ca,
            ce=ce,
            instability=instability,
            impact=impact,
            susceptibility=susceptibility,
        )
    return metrics


def find_cycle_info(graph: nx.DiGraph) -> tuple[set[str], set[tuple[str, str]]]:
    """Find all nodes and edges that participate in cycles."""
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


def truncate_label(name: str, max_chars: int = 18) -> str:
    """Truncate a label with ellipsis if it exceeds max_chars.

    Full name is shown via hover tooltip.
    """
    if len(name) <= max_chars:
        return name
    return name[: max_chars - 3] + "..."


def shorten_label(name: str, max_parts: int = 2) -> str:
    """Shorten a dotted module name for display.

    'flask.json.provider' -> 'json.provider'
    """
    parts = name.split(".")
    if len(parts) <= max_parts:
        return name
    return ".".join(parts[-max_parts:])


def get_package_groups(nodes: list[str]) -> dict[str, str]:
    """Map each node to its top-level package for grouping."""
    return {node: node.split(".")[0] for node in nodes}


def _node_id(name: str, module_path: Path, line: int) -> str:
    """Return a unique string identifier for a function definition."""
    return f"{module_path.stem}__{name}__{line}"


def build_function_graph(source_root: Path) -> tuple[nx.DiGraph, dict[str, dict]]:
    """Build a function-level dependency graph using Jedi for cross-module resolution.

    Returns:
        (graph, node_metadata): graph with function nodes and call edges,
        and metadata mapping node_id -> {label, file_path, line}.
    """
    graph = nx.DiGraph()
    metadata: dict[str, dict] = {}
    project = jedi.Project(source_root)
    files = _iter_python_files(source_root)

    for filepath in files:
        try:
            source = filepath.read_text(encoding="utf-8")
            tree = ast.parse(source)
        except (SyntaxError, UnicodeDecodeError):
            continue

        script = jedi.Script(path=str(filepath), project=project)
        rel_path = str(filepath.relative_to(source_root))

        for function in ast.walk(tree):
            if not isinstance(function, ast.FunctionDef):
                continue

            caller_id = _node_id(function.name, filepath, function.lineno)
            graph.add_node(caller_id, label=function.name)
            metadata[caller_id] = {
                "label": function.name,
                "file_path": rel_path,
                "line": function.lineno,
            }

            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue

                call_line = node.func.end_lineno
                call_col = node.func.end_col_offset
                if call_line is None or call_col is None:
                    continue
                source_lines = source.splitlines()
                if call_line - 1 < len(source_lines):
                    call_col = min(call_col, len(source_lines[call_line - 1]))
                try:
                    definitions = script.goto(line=call_line, column=call_col)
                except Exception:
                    continue

                for definition in definitions:
                    if definition.type != "function":
                        continue
                    if definition.module_path is None:
                        continue
                    def_path = Path(definition.module_path).resolve()
                    if not str(def_path).startswith(str(source_root)):
                        continue
                    # Skip definitions inside .venv or hidden dirs
                    try:
                        callee_rel = str(def_path.relative_to(source_root))
                    except ValueError:
                        continue
                    if any(part in _SKIP_DIRS or part.startswith(".") for part in Path(callee_rel).parts):
                        continue

                    callee_id = _node_id(
                        definition.name, def_path, definition.line
                    )
                    graph.add_node(callee_id, label=definition.name)
                    if callee_id not in metadata:
                        metadata[callee_id] = {
                            "label": definition.name,
                            "file_path": callee_rel,
                            "line": definition.line,
                        }
                    graph.add_edge(caller_id, callee_id)

    return graph, metadata
