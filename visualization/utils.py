"""Parsing and metric utilities for dependency graph analysis."""

import ast
import os
import re
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import jedi
import networkx as nx

# Names matched case-sensitively; include common venv layouts (``venv/`` is not ``.venv/``).
_SKIP_DIRS = frozenset(
    {
        ".venv",
        "venv",
        ".git",
        "__pycache__",
        "node_modules",
        ".tox",
        ".eggs",
        ".mypy_cache",
        "site-packages",
        "dist",
        "build",
        ".nox",
        "htmlcov",
        ".pytest_cache",
        ".ruff_cache",
    }
)


def output_stem_for_source_root(source_root: Path, *, max_len: int = 200) -> str:
    """Single filesystem-safe token from ``source_root`` (typically resolved).

    Encodes the full path using underscores instead of separators, strips
    characters illegal in Windows or POSIX file names and control characters),
    and collapses repeated underscores.
    """
    posix = source_root.resolve().as_posix()
    s = posix.replace(":", "_")
    s = re.sub(r'[<>"/\\|?*\x00-\x1f]', "_", s)
    s = s.replace("/", "_")
    s = re.sub(r"_+", "_", s).strip("._")
    if not s:
        s = "graph"
    if len(s) > max_len:
        s = s[:max_len].rstrip("_")
    return s


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
    raw_impact: float = 0.0  # Denormalized impact value
    raw_susceptibility: float = 0.0  # Denormalized susceptibility value


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


def compute_metrics(
    graph: nx.DiGraph,
    *,
    coef_impact_ca_node: float = 1.0,
    coef_impact_sum_ca_dependents: float = 1.0,
    coef_susceptibility_ce_node: float = 1.0,
    coef_susceptibility_sum_ce_dependencies: float = 1.0,
) -> dict[str, NodeMetrics]:
    """Compute Ca, Ce, instability, impact, and susceptibility for every node.

    Uses the same formula as metrics/metrics.py:
    Impact = coef * Ca(node) + coef * sum(Ca over dependents/predecessors).
    Susceptibility = coef * Ce(node) + coef * sum(Ce over dependencies/successors).

    Raw values are then normalized to [0,1] for visualization coloring.
    """
    # First pass: Ca/Ce/instability
    base: dict[str, tuple[int, int, float]] = {}
    for node in graph.nodes():
        ca = graph.in_degree(node)
        ce = graph.out_degree(node)
        instability = ce / (ca + ce) if (ca + ce) > 0 else 0.5
        base[node] = (ca, ce, instability)

    # Second pass: raw impact/susceptibility — one edge scan (O(|E|)) instead of
    # per-node predecessor/successor walks (same asymptotic but better locality).
    sum_ca_dependents: dict[str, float] = {n: 0.0 for n in graph}
    sum_ce_dependencies: dict[str, float] = {n: 0.0 for n in graph}
    for n, s in graph.edges():  # n -> s means n depends on s
        sum_ca_dependents[s] += base[n][0]
        sum_ce_dependencies[n] += base[s][1]

    raw_impact: dict[str, float] = {}
    raw_susceptibility: dict[str, float] = {}
    for node in graph.nodes():
        ca, ce, _ = base[node]
        raw_impact[node] = (
            coef_impact_ca_node * ca
            + coef_impact_sum_ca_dependents * sum_ca_dependents[node]
        )
        raw_susceptibility[node] = (
            coef_susceptibility_ce_node * ce
            + coef_susceptibility_sum_ce_dependencies * sum_ce_dependencies[node]
        )

    # Normalize to [0,1]
    max_impact = max(raw_impact.values(), default=1.0) or 1.0
    max_susceptibility = max(raw_susceptibility.values(), default=1.0) or 1.0

    metrics: dict[str, NodeMetrics] = {}
    for node in graph.nodes():
        ca, ce, instability = base[node]
        metrics[node] = NodeMetrics(
            ca=ca,
            ce=ce,
            instability=instability,
            impact=raw_impact[node] / max_impact,
            susceptibility=raw_susceptibility[node] / max_susceptibility,
            raw_impact=raw_impact[node],
            raw_susceptibility=raw_susceptibility[node],
        )
    return metrics


def find_cycle_info(graph: nx.DiGraph) -> tuple[set[str], set[tuple[str, str]]]:
    """Find all nodes and edges that participate in at least one directed cycle.

    Uses strongly connected components (linear in |V|+|E|) instead of enumerating
    ``nx.simple_cycles``, which is exponential and can hang on dense cyclic graphs.
    Nodes in an SCC of size >= 2, or with a self-loop, lie on a cycle; every edge
    internal to such an SCC lies on some simple cycle.
    """
    cycle_nodes: set[str] = set()
    cycle_edges: set[tuple[str, str]] = set()

    for scc in nx.strongly_connected_components(graph):
        if len(scc) >= 2:
            cycle_nodes.update(scc)
            cycle_edges.update(graph.subgraph(scc).edges())
        else:
            n = next(iter(scc))
            if graph.has_edge(n, n):
                cycle_nodes.add(n)
                cycle_edges.add((n, n))

    return cycle_nodes, cycle_edges


def truncate_label(name: str, max_chars: int = 18) -> str:
    """Truncate a label with ellipsis if it exceeds max_chars.

    Full name is shown via hover tooltip.
    """
    if len(name) <= max_chars:
        return name
    return name[: max_chars - 3] + "..."


def format_multiline_label(name: str, max_name_len: int = 18) -> str:
    """Format a module name as 2 rows for readability.

    Row 1: unique file/function name (truncated if needed)
    Row 2: parent module path (smaller)

    'flask.json.provider' -> 'provider\\nflask.json'
    'utils' -> 'utils'
    """
    parts = name.split(".")
    if len(parts) == 1:
        display_name = parts[0]
        if len(display_name) > max_name_len:
            display_name = display_name[: max_name_len - 2] + ".."
        return display_name

    unique_name = parts[-1]
    module_path = ".".join(parts[:-1])

    if len(unique_name) > max_name_len:
        unique_name = unique_name[: max_name_len - 2] + ".."

    if len(module_path) > max_name_len:
        module_path = ".." + module_path[-(max_name_len - 2):]

    return f"{unique_name}\n{module_path}"


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


class _FunctionCallCollector(ast.NodeVisitor):
    """Single AST walk: each Call is attributed to the innermost enclosing function.

    Avoids ``ast.walk(tree)`` × ``ast.walk(function)``, which revisits nested subtrees
    many times (superlinear in module size) and mis-attributes calls inside nested
    function bodies to the outer function.
    """

    __slots__ = ("filepath", "rel_path", "stack", "calls", "function_metas")

    def __init__(self, filepath: Path, rel_path: str) -> None:
        self.filepath = filepath
        self.rel_path = rel_path
        self.stack: list[str] = []
        self.calls: list[tuple[str, int, int]] = []
        self.function_metas: dict[str, dict] = {}

    def _enter_function(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        caller_id = _node_id(node.name, self.filepath, node.lineno)
        self.function_metas[caller_id] = {
            "label": node.name,
            "file_path": self.rel_path,
            "line": node.lineno,
        }
        self.stack.append(caller_id)

    def _exit_function(self) -> None:
        self.stack.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._enter_function(node)
        self.generic_visit(node)
        self._exit_function()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._enter_function(node)
        self.generic_visit(node)
        self._exit_function()

    def visit_Call(self, node: ast.Call) -> None:
        if self.stack:
            el = node.func.end_lineno
            ec = node.func.end_col_offset
            if el is not None and ec is not None:
                self.calls.append((self.stack[-1], el, ec))
        self.generic_visit(node)


def _function_graph_worker(args: tuple[str, str]) -> tuple[list[tuple[str, str]], dict[str, dict]]:
    """Analyze one file for function call edges. Top-level for multiprocessing pickling."""
    source_root_str, filepath_str = args
    source_root = Path(source_root_str).resolve()
    filepath = Path(filepath_str).resolve()
    edges: list[tuple[str, str]] = []
    metadata: dict[str, dict] = {}

    try:
        source = filepath.read_text(encoding="utf-8")
        tree = ast.parse(source)
    except (SyntaxError, UnicodeDecodeError, OSError):
        return edges, metadata

    rel_path = str(filepath.relative_to(source_root))
    source_lines = source.splitlines()
    project = jedi.Project(source_root)
    script = jedi.Script(path=str(filepath), project=project)

    collector = _FunctionCallCollector(filepath, rel_path)
    collector.visit(tree)
    metadata.update(collector.function_metas)

    root_prefix = str(source_root)
    goto_cache: dict[tuple[int, int], list] = {}
    for caller_id, call_line, call_col in collector.calls:
        li = call_line - 1
        if 0 <= li < len(source_lines):
            col = min(call_col, len(source_lines[li]))
        else:
            col = call_col
        gkey = (call_line, col)
        if gkey not in goto_cache:
            try:
                goto_cache[gkey] = script.goto(line=call_line, column=col)
            except Exception:
                goto_cache[gkey] = []
        definitions = goto_cache[gkey]

        for definition in definitions:
            if definition.type != "function":
                continue
            if definition.module_path is None:
                continue
            def_path = Path(definition.module_path).resolve()
            if not str(def_path).startswith(root_prefix):
                continue
            try:
                callee_rel = str(def_path.relative_to(source_root))
            except ValueError:
                continue
            if any(part in _SKIP_DIRS or part.startswith(".") for part in Path(callee_rel).parts):
                continue

            callee_id = _node_id(definition.name, def_path, definition.line)
            edges.append((caller_id, callee_id))
            if callee_id not in metadata:
                metadata[callee_id] = {
                    "label": definition.name,
                    "file_path": callee_rel,
                    "line": definition.line,
                }

    return edges, metadata


def build_function_graph(source_root: Path) -> tuple[nx.DiGraph, dict[str, dict]]:
    """Build a function-level dependency graph using Jedi for cross-module resolution.

    Uses one AST pass per file (no nested ``ast.walk`` over every function body),
    optional process parallelism across files (Jedi is isolated per worker), and
    bulk NetworkX construction. Set ``KOWALSKI_FUNCTION_GRAPH_SERIAL=1`` to force
    single-process mode.

    Returns:
        (graph, node_metadata): graph with function nodes and call edges,
        and metadata mapping node_id -> {label, file_path, line}.
    """
    source_root_res = source_root.resolve()
    files = _iter_python_files(source_root_res)
    root_str = str(source_root_res)
    tasks = [(root_str, str(f.resolve())) for f in files]

    serial = os.environ.get("KOWALSKI_FUNCTION_GRAPH_SERIAL", "").lower() in (
        "1",
        "true",
        "yes",
    )
    n_cpu = os.cpu_count() or 1
    use_parallel = not serial and len(tasks) >= 8 and n_cpu > 1

    if use_parallel:
        max_workers = min(len(tasks), max(1, n_cpu), 16)
        chunksize = max(1, len(tasks) // (max_workers * 4))
        with ProcessPoolExecutor(max_workers=max_workers) as ex:
            chunks = list(ex.map(_function_graph_worker, tasks, chunksize=chunksize))
    else:
        chunks = [_function_graph_worker(t) for t in tasks]

    all_edges: list[tuple[str, str]] = []
    metadata: dict[str, dict] = {}
    for e, m in chunks:
        all_edges.extend(e)
        for k, v in m.items():
            if k not in metadata:
                metadata[k] = v

    graph = nx.DiGraph()
    graph.add_nodes_from((nid, {"label": meta["label"]}) for nid, meta in metadata.items())
    graph.add_edges_from(all_edges)
    return graph, metadata
