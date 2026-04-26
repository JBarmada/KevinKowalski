"""Adapter: satisfies the Analyzer protocol against the metrics team's library.

Reuses `metrics.graph.parse_edges_v2` for AST-based import edges, layers
radon (cc_max) and an AST class-body walk (lcom4) on top, and emits
GraphSnapshot/ModuleMetrics matching contract.py.

TODO: when the metrics team finishes susceptibility (`sus`) and impact
(`imp`), add them here -- they will also need new fields on
ModuleMetrics in contract.py and rendering in formatters.py.
"""

import ast
import os
import sys
from pathlib import Path

import networkx as nx
from radon.complexity import cc_visit

# Make sibling `metrics/` package importable when running from mcp/.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from metrics.graph import _module_name, parse_edges_v2  # noqa: E402

from contract import Analyzer, GraphSnapshot, ModuleMetrics  # noqa: E402


_HIGH_CC_THRESHOLD = 11
_GOD_LCOM4_THRESHOLD = 4
_GOD_CE_THRESHOLD = 5
_SDP_INSTABILITY_THRESHOLD = 0.6
_STABLE_INSTABILITY_THRESHOLD = 0.3


def _instability(ca: int, ce: int) -> float:
    if ca + ce == 0:
        return 0.0
    return ce / (ca + ce)


def _absolute_edges(root: Path, known_modules: set[str]) -> list[tuple[str, str]]:
    """Edges from absolute `import x` / `from x import y` statements.

    Complements `parse_edges_v2` (which only handles relative imports). Only
    emits edges whose target is in `known_modules` -- skips stdlib and
    third-party. For `from a.b import c`, prefers `a.b.c` (submodule) when
    that exists, else falls back to `a.b`.
    """
    edges: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for filepath in sorted(root.rglob("*.py")):
        try:
            rel = filepath.relative_to(root)
        except ValueError:
            continue
        if any(part in {".venv", "venv", "__pycache__", ".git"} for part in rel.parts):
            continue
        try:
            tree = ast.parse(filepath.read_text(encoding="utf-8"))
        except (OSError, SyntaxError, UnicodeDecodeError):
            continue
        importer = _module_name(filepath, root)

        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets.extend(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                base = node.module
                for alias in node.names:
                    submodule = f"{base}.{alias.name}"
                    targets.append(submodule if submodule in known_modules else base)
            else:
                continue

            for target in targets:
                if target == importer:
                    continue
                if target in known_modules:
                    edge = (importer, target)
                    if edge not in seen:
                        seen.add(edge)
                        edges.append(edge)
    return edges


def _discover_modules(root: Path) -> dict[str, str]:
    """Return {dotted_module_name: repo_relative_path} for every .py under root."""
    modules: dict[str, str] = {}
    for filepath in sorted(root.rglob("*.py")):
        try:
            rel = filepath.relative_to(root)
        except ValueError:
            continue
        if any(part in {".venv", "venv", "__pycache__", ".git"} for part in rel.parts):
            continue
        name = _module_name(filepath, root)
        modules[name] = rel.as_posix()
    return modules


def _cc_max_for_file(source: str) -> int:
    try:
        blocks = cc_visit(source)
    except Exception:
        return 0
    return max((b.complexity for b in blocks), default=0)


def _lcom4_for_tree(tree: ast.AST) -> float | None:
    """LCOM4 averaged across classes in the module; None if module has no classes.

    Per class: build a graph where nodes are methods, edges connect methods
    that share an instance attribute. Connected components = LCOM4.
    """
    classes = [n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)]
    if not classes:
        return None

    scores: list[int] = []
    for cls in classes:
        methods = [m for m in cls.body if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))]
        if not methods:
            continue
        method_attrs: dict[str, set[str]] = {}
        for m in methods:
            attrs: set[str] = set()
            self_arg = m.args.args[0].arg if m.args.args else None
            if self_arg is None:
                method_attrs[m.name] = attrs
                continue
            for node in ast.walk(m):
                if (
                    isinstance(node, ast.Attribute)
                    and isinstance(node.value, ast.Name)
                    and node.value.id == self_arg
                ):
                    attrs.add(node.attr)
            method_attrs[m.name] = attrs

        g = nx.Graph()
        g.add_nodes_from(method_attrs)
        names = list(method_attrs)
        for i, a in enumerate(names):
            for b in names[i + 1 :]:
                if method_attrs[a] & method_attrs[b]:
                    g.add_edge(a, b)
        scores.append(nx.number_connected_components(g))

    if not scores:
        return None
    return float(sum(scores)) / len(scores)


class RealAnalyzer:
    def __init__(self) -> None:
        self._cache: dict[str, GraphSnapshot] = {}

    def analyze(self, repo_path: str) -> GraphSnapshot:
        root = Path(repo_path).resolve()
        snap = self._build_snapshot(root)
        self._cache[str(root)] = snap
        return snap

    def incremental_check(self, repo_path: str, files: list[str]) -> dict:
        root = Path(repo_path).resolve()
        before = self._cache.get(str(root)) or self._build_snapshot(root)
        after = self._build_snapshot(root)
        self._cache[str(root)] = after

        touched_modules = self._modules_for_files(before, after, files)
        changed = []
        for mod in touched_modules:
            b = before.modules.get(mod)
            a = after.modules.get(mod)
            if b is None or a is None:
                continue
            if (b.ca, b.ce, b.instability, b.lcom4, b.cc_max, tuple(b.violations)) != (
                a.ca, a.ce, a.instability, a.lcom4, a.cc_max, tuple(a.violations)
            ):
                changed.append({"module": mod, "before": b, "after": a})

        before_v = _violation_set(before)
        after_v = _violation_set(after)
        new_violations = sorted(after_v - before_v)
        resolved_violations = sorted(before_v - after_v)

        if new_violations:
            verdict = "red"
        elif resolved_violations:
            verdict = "green"
        else:
            verdict = "yellow"

        return {
            "changed": changed,
            "new_violations": new_violations,
            "resolved_violations": resolved_violations,
            "verdict": verdict,
        }

    def _build_snapshot(self, root: Path) -> GraphSnapshot:
        modules_paths = _discover_modules(root) if root.exists() else {}
        relative_edges = parse_edges_v2(root) if root.exists() else []
        abs_edges = (
            _absolute_edges(root, set(modules_paths)) if root.exists() else []
        )
        seen: set[tuple[str, str]] = set()
        edges: list[tuple[str, str]] = []
        for e in list(relative_edges) + abs_edges:
            if e not in seen:
                seen.add(e)
                edges.append(e)

        graph = nx.DiGraph()
        graph.add_nodes_from(modules_paths)
        graph.add_edges_from(edges)

        cc_by_module: dict[str, int] = {}
        lcom_by_module: dict[str, float | None] = {}
        for mod, rel in modules_paths.items():
            try:
                source = (root / rel).read_text(encoding="utf-8")
                tree = ast.parse(source)
            except (OSError, SyntaxError, UnicodeDecodeError):
                cc_by_module[mod] = 0
                lcom_by_module[mod] = None
                continue
            cc_by_module[mod] = _cc_max_for_file(source)
            lcom_by_module[mod] = _lcom4_for_tree(tree)

        modules: dict[str, ModuleMetrics] = {}
        for mod, rel in modules_paths.items():
            ca = graph.in_degree(mod)
            ce = graph.out_degree(mod)
            modules[mod] = ModuleMetrics(
                module=mod,
                path=rel,
                ca=int(ca),
                ce=int(ce),
                instability=_instability(int(ca), int(ce)),
                lcom4=lcom_by_module.get(mod),
                cc_max=cc_by_module.get(mod, 0),
                violations=[],
            )

        _attach_violations(modules, graph)

        clean_edges = [(s, d) for s, d in edges if s in modules and d in modules]
        return GraphSnapshot(root=str(root), modules=modules, edges=clean_edges)

    def _modules_for_files(
        self, before: GraphSnapshot, after: GraphSnapshot, files: list[str]
    ) -> set[str]:
        wanted = {f.replace("\\", "/") for f in files}
        out: set[str] = set()
        for snap in (before, after):
            for m in snap.modules.values():
                if m.path in wanted:
                    out.add(m.module)
        return out


def _violation_set(snap: GraphSnapshot) -> set[str]:
    return {f"{m.module}:{v}" for m in snap.modules.values() for v in m.violations}


def _attach_violations(modules: dict[str, ModuleMetrics], graph: nx.DiGraph) -> None:
    for m in modules.values():
        if m.cc_max >= _HIGH_CC_THRESHOLD:
            m.violations.append("HIGH_CC")
        if (
            m.lcom4 is not None
            and m.lcom4 >= _GOD_LCOM4_THRESHOLD
            and m.ce >= _GOD_CE_THRESHOLD
        ):
            m.violations.append("GOD_MODULE")

    for src, dst in graph.edges():
        if src not in modules or dst not in modules:
            continue
        importer = modules[src]
        importee = modules[dst]
        if (
            importee.instability >= _SDP_INSTABILITY_THRESHOLD
            and importer.instability <= _STABLE_INSTABILITY_THRESHOLD
            and "SDP" not in importee.violations
        ):
            importee.violations.append("SDP")

    in_cycle: set[str] = set()
    for scc in nx.strongly_connected_components(graph):
        if len(scc) >= 2:
            in_cycle.update(scc)
        else:
            n = next(iter(scc))
            if graph.has_edge(n, n):
                in_cycle.add(n)
    for mod in in_cycle:
        if mod in modules and "CYCLE" not in modules[mod].violations:
            modules[mod].violations.append("CYCLE")


_analyzer: Analyzer = RealAnalyzer()


def get_analyzer() -> Analyzer:
    return _analyzer
