"""
Optional CHA-weighted function call graph (class hierarchy, weighted edges, extra scopes).

Import ``build_cha_weighted_call_graph`` when you need this behavior; ``metrics.metrics``
uses the basic Jedi-only graph unless you opt in (e.g. ``--cha`` on the CLI).
"""

from __future__ import annotations

import ast
import pathlib
from dataclasses import dataclass, field

import jedi
import networkx as nx

try:
    from .graph_ids import node_id
except ImportError:
    from graph_ids import node_id


def _filepath_to_dotted_module(rel: pathlib.Path) -> str:
    p = rel.with_suffix("")
    parts = p.parts
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _jedi_path_to_dotted_module(root: pathlib.Path, module_path: pathlib.Path | str | None) -> str | None:
    if module_path is None:
        return None
    p = pathlib.Path(module_path).resolve()
    try:
        rel = p.relative_to(root.resolve())
    except ValueError:
        return None
    return _filepath_to_dotted_module(rel)


def _c3_merge(sequences: list[list[str]]) -> list[str]:
    seqs = [list(s) for s in sequences if s]
    result: list[str] = []
    while seqs:
        nonempty = [s for s in seqs if s]
        if not nonempty:
            break
        chosen: str | None = None
        for seq in nonempty:
            h = seq[0]
            if all(h not in s[1:] for s in nonempty if len(s) > 1):
                chosen = h
                break
        if chosen is None:
            chosen = nonempty[0][0]
        result.append(chosen)
        for s in seqs:
            while chosen in s:
                s.remove(chosen)
        seqs = [s for s in seqs if s]
    return result


def _compute_mros(classes: dict[str, ClassIndexEntry]) -> dict[str, tuple[str, ...]]:
    memo: dict[str, list[str]] = {}

    def mro(qn: str, stack: frozenset[str]) -> list[str]:
        if qn in memo:
            return memo[qn]
        if qn not in classes:
            memo[qn] = [qn]
            return memo[qn]
        if qn in stack:
            memo[qn] = [qn]
            return memo[qn]
        bases_known = [b for b in classes[qn].bases_qnames if b in classes]
        if not bases_known:
            memo[qn] = [qn]
            return memo[qn]
        new_stack = stack | {qn}
        seqs = [mro(b, new_stack) for b in bases_known] + [list(bases_known)]
        tail = _c3_merge(seqs)
        memo[qn] = [qn] + tail
        return memo[qn]

    for qn in classes:
        mro(qn, frozenset())
    return {k: tuple(v) for k, v in memo.items()}


def _dispatch_target_for_class(
    class_qname: str,
    method_name: str,
    mros: dict[str, tuple[str, ...]],
    classes: dict[str, ClassIndexEntry],
) -> str | None:
    if class_qname not in mros:
        return None
    for cls in mros[class_qname]:
        if cls in classes and method_name in classes[cls].methods:
            return classes[cls].methods[method_name]
    return None


def _cha_callee_ids(
    static_type_qnames: list[str],
    method_name: str,
    mros: dict[str, tuple[str, ...]],
    classes: dict[str, ClassIndexEntry],
) -> set[str]:
    targets: set[str] = set()
    for t in static_type_qnames:
        if t not in mros:
            continue
        for s in mros:
            if t not in mros[s]:
                continue
            impl = _dispatch_target_for_class(s, method_name, mros, classes)
            if impl is not None:
                targets.add(impl)
    return targets


def _jedi_class_to_qname(d: object, root: pathlib.Path, class_qnames: set[str]) -> str | None:
    if getattr(d, "type", None) != "class":
        return None
    full_name = getattr(d, "full_name", None) or ""
    if full_name and full_name in class_qnames:
        return full_name
    mod = _jedi_path_to_dotted_module(root, getattr(d, "module_path", None))
    if not mod:
        return None
    name = getattr(d, "name", "") or ""
    cand = f"{mod}.{name}"
    if cand in class_qnames:
        return cand
    tail = name.split(".")[-1]
    matches = [q for q in class_qnames if q.startswith(mod + ".") and q.endswith("." + tail)]
    if len(matches) == 1:
        return matches[0]
    return None


def _infer_static_class_qnames(
    script: jedi.Script,
    root: pathlib.Path,
    class_qnames: set[str],
    recv: ast.expr,
    lines: list[str],
) -> list[str]:
    rl = getattr(recv, "end_lineno", None) or recv.lineno
    rc = getattr(recv, "end_col_offset", None)
    if rc is None:
        return []
    line = lines[rl - 1]
    rc = min(rc, len(line))
    found: list[str] = []
    seen: set[str] = set()

    def add_from_defs(defs: list) -> None:
        for d in defs:
            qn = _jedi_class_to_qname(d, root, class_qnames)
            if qn and qn not in seen:
                seen.add(qn)
                found.append(qn)

    try:
        add_from_defs(script.infer(line=rl, column=rc))
    except Exception:
        pass
    if not found:
        try:
            add_from_defs(script.goto(line=rl, column=rc))
        except Exception:
            pass
    return found


def _owning_class_for_function_goto(
    definition: object,
    method_loc_to_class: dict[tuple[str, int], str],
) -> str | None:
    mp = getattr(definition, "module_path", None)
    line = getattr(definition, "line", None)
    if mp is None or line is None:
        return None
    key = (str(pathlib.Path(mp).resolve()), int(line))
    return method_loc_to_class.get(key)


def _goto_function_definitions(script: jedi.Script, call_node: ast.Call, lines: list[str]):
    call_line = call_node.func.end_lineno
    call_col = call_node.func.end_col_offset
    source_line = lines[call_line - 1]
    call_col = min(call_col, len(source_line))
    try:
        return script.goto(line=call_line, column=call_col)
    except Exception:
        return []


_MODULE_SCOPE_CALLER_NAME = "__module_scope__"
_MODULE_SCOPE_CALLER_LINE = 0


def _module_scope_caller_id(filepath: pathlib.Path) -> str:
    return node_id(_MODULE_SCOPE_CALLER_NAME, filepath, _MODULE_SCOPE_CALLER_LINE)


def _class_body_scope_caller_id(filepath: pathlib.Path, class_qname: str) -> str:
    slug = "".join(ch if ch.isalnum() or ch in "_" else "_" for ch in class_qname)
    slug = slug.replace(".", "_")
    return node_id(f"_cls_{slug}", filepath, _MODULE_SCOPE_CALLER_LINE)


class _ScopedBodyCallCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return None

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return None

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return None

    def visit_Call(self, node: ast.Call) -> None:
        self.calls.append(node)
        self.generic_visit(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self.generic_visit(node)


def _collect_calls_in_suite(stmts: list[ast.stmt]) -> list[ast.Call]:
    collector = _ScopedBodyCallCollector()
    for stmt in stmts:
        collector.visit(stmt)
    return collector.calls


def _emit_class_body_level_calls(
    graph: nx.DiGraph,
    class_node: ast.ClassDef,
    mod: str,
    outer: tuple[str, ...],
    filepath: pathlib.Path,
    script: jedi.Script,
    root_r: pathlib.Path,
    root_s: str,
    class_qnames: set[str],
    mros: dict[str, tuple[str, ...]],
    classes: dict[str, ClassIndexEntry],
    method_loc_to_class: dict[tuple[str, int], str],
    lines: list[str],
) -> None:
    qn = ".".join((mod,) + outer + (class_node.name,))
    calls = _collect_calls_in_suite(class_node.body)
    if calls:
        cid = _class_body_scope_caller_id(filepath, qn)
        graph.add_node(cid, label=f"<class {class_node.name}>")
        for call_node in calls:
            _add_weighted_call_edge(
                graph,
                cid,
                call_node,
                script,
                root_r,
                root_s,
                class_qnames,
                mros,
                classes,
                method_loc_to_class,
                lines,
            )
    for stmt in class_node.body:
        if isinstance(stmt, ast.ClassDef):
            _emit_class_body_level_calls(
                graph,
                stmt,
                mod,
                outer + (class_node.name,),
                filepath,
                script,
                root_r,
                root_s,
                class_qnames,
                mros,
                classes,
                method_loc_to_class,
                lines,
            )


@dataclass
class ClassIndexEntry:
    qname: str
    bases_qnames: list[str] = field(default_factory=list)
    methods: dict[str, str] = field(default_factory=dict)


def _resolve_base_name(
    base_id: str,
    mod: str,
    enclosing: tuple[str, ...],
    class_qnames: set[str],
) -> str | None:
    if enclosing and base_id == enclosing[-1]:
        qn = ".".join((mod,) + enclosing)
        if qn in class_qnames:
            return qn
    for k in range(len(enclosing), -1, -1):
        qn = ".".join((mod,) + enclosing[:k] + (base_id,))
        if qn in class_qnames:
            return qn
    matches = [q for q in class_qnames if q.startswith(mod + ".") and q.endswith("." + base_id)]
    if len(matches) == 1:
        return matches[0]
    return None


def _build_class_index(
    root: pathlib.Path,
) -> tuple[dict[str, ClassIndexEntry], dict[tuple[str, int], str], set[str]]:
    classes: dict[str, ClassIndexEntry] = {}
    method_loc_to_class: dict[tuple[str, int], str] = {}
    files = sorted(root.rglob("*.py"))
    root_r = root.resolve()

    def register_qnames(class_node: ast.ClassDef, mod: str, outer: tuple[str, ...]) -> None:
        qn = ".".join((mod,) + outer + (class_node.name,))
        classes[qn] = ClassIndexEntry(qname=qn)
        for stmt in class_node.body:
            if isinstance(stmt, ast.ClassDef):
                register_qnames(stmt, mod, outer + (class_node.name,))

    def fill_class(class_node: ast.ClassDef, mod: str, filepath: pathlib.Path, outer: tuple[str, ...]) -> None:
        name = class_node.name
        qn = ".".join((mod,) + outer + (name,))
        entry = classes[qn]
        all_q = set(classes)
        bases_qnames: list[str] = []
        for base in class_node.bases:
            if isinstance(base, ast.Name):
                rq = _resolve_base_name(base.id, mod, outer, all_q)
                if rq:
                    bases_qnames.append(rq)
            elif isinstance(base, ast.Attribute) and isinstance(base.value, ast.Name):
                left = base.value.id
                chain = f"{left}.{base.attr}"
                if chain in classes:
                    bases_qnames.append(chain)
                else:
                    fq = f"{mod}.{chain}"
                    if fq in classes:
                        bases_qnames.append(fq)
        entry.bases_qnames = bases_qnames
        abs_path = str(filepath.resolve())
        for stmt in class_node.body:
            if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                cid = node_id(stmt.name, filepath, stmt.lineno)
                entry.methods[stmt.name] = cid
                method_loc_to_class[(abs_path, stmt.lineno)] = qn
            elif isinstance(stmt, ast.ClassDef):
                fill_class(stmt, mod, filepath, outer + (name,))

    for filepath in files:
        mod = _filepath_to_dotted_module(filepath.relative_to(root_r))
        try:
            tree = ast.parse(filepath.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                register_qnames(node, mod, ())

    for filepath in files:
        mod = _filepath_to_dotted_module(filepath.relative_to(root_r))
        try:
            tree = ast.parse(filepath.read_text(encoding="utf-8"))
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                fill_class(node, mod, filepath, ())

    return classes, method_loc_to_class, set(classes)


def _accumulate_edge(graph: nx.DiGraph, u: str, v: str, weight: float) -> None:
    if weight <= 0:
        return
    if graph.has_edge(u, v):
        graph[u][v]["weight"] = graph[u][v].get("weight", 1.0) + weight
    else:
        graph.add_edge(u, v, weight=weight)


def _add_weighted_call_edge(
    graph: nx.DiGraph,
    caller_id: str,
    call_node: ast.Call,
    script: jedi.Script,
    root: pathlib.Path,
    root_s: str,
    class_qnames: set[str],
    mros: dict[str, tuple[str, ...]],
    classes: dict[str, ClassIndexEntry],
    method_loc_to_class: dict[tuple[str, int], str],
    lines: list[str],
) -> None:
    func_expr = call_node.func
    cha_ids: set[str] = set()
    goto_callee_id: str | None = None

    if isinstance(func_expr, ast.Attribute):
        method_name = func_expr.attr
        recv = func_expr.value
        static_types = _infer_static_class_qnames(
            script, root.resolve(), class_qnames, recv, lines
        )
        if static_types:
            cha_ids = _cha_callee_ids(static_types, method_name, mros, classes)
        for definition in _goto_function_definitions(script, call_node, lines):
            if definition.type != "function":
                continue
            if definition.module_path is None:
                continue
            if not str(definition.module_path).startswith(root_s):
                continue
            goto_callee_id = node_id(
                definition.name,
                pathlib.Path(definition.module_path),
                definition.line,
            )
            if not cha_ids:
                owning = _owning_class_for_function_goto(definition, method_loc_to_class)
                if owning is not None:
                    cha_ids = _cha_callee_ids([owning], method_name, mros, classes)
            break
    else:
        for definition in _goto_function_definitions(script, call_node, lines):
            if definition.type != "function":
                continue
            if definition.module_path is None:
                continue
            if not str(definition.module_path).startswith(root_s):
                continue
            goto_callee_id = node_id(
                definition.name,
                pathlib.Path(definition.module_path),
                definition.line,
            )
            break

    if isinstance(func_expr, ast.Attribute):
        weight = float(len(cha_ids)) if cha_ids else 0.0
        if weight <= 0 and goto_callee_id:
            weight = 1.0
        primary = goto_callee_id
        if primary is None and cha_ids:
            primary = min(cha_ids)
            weight = float(len(cha_ids))
        if primary is None or weight <= 0:
            return
        parts = primary.rsplit("__", 2)
        lbl = parts[1] if len(parts) == 3 else primary
        if primary not in graph:
            graph.add_node(primary, label=lbl)
        _accumulate_edge(graph, caller_id, primary, weight)
    else:
        if goto_callee_id is None:
            return
        parts = goto_callee_id.rsplit("__", 2)
        lbl = parts[1] if len(parts) == 3 else goto_callee_id
        if goto_callee_id not in graph:
            graph.add_node(goto_callee_id, label=lbl)
        _accumulate_edge(graph, caller_id, goto_callee_id, 1.0)


def build_cha_weighted_call_graph(root: pathlib.Path) -> nx.DiGraph:
    """Function call graph: CHA as edge weights, module/class-body scopes, ``async def``."""
    graph = nx.DiGraph()
    classes, method_loc_to_class, class_qnames = _build_class_index(root)
    mros = _compute_mros(classes) if classes else {}
    project = jedi.Project(root)
    files = sorted(root.rglob("*.py"))
    root_s = str(root.resolve())
    root_r = root.resolve()

    for filepath in files:
        source = filepath.read_text(encoding="utf-8")
        lines = source.splitlines()
        tree = ast.parse(source)
        script = jedi.Script(path=str(filepath), project=project)
        mod = _filepath_to_dotted_module(filepath.relative_to(root_r))

        for function in ast.walk(tree):
            if not isinstance(function, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            caller_id = node_id(function.name, filepath, function.lineno)
            graph.add_node(caller_id, label=function.name)
            for node in ast.walk(function):
                if not isinstance(node, ast.Call):
                    continue
                _add_weighted_call_edge(
                    graph,
                    caller_id,
                    node,
                    script,
                    root_r,
                    root_s,
                    class_qnames,
                    mros,
                    classes,
                    method_loc_to_class,
                    lines,
                )

        mod_calls = _collect_calls_in_suite(tree.body)
        if mod_calls:
            mod_caller = _module_scope_caller_id(filepath)
            graph.add_node(mod_caller, label="<module>")
            for node in mod_calls:
                _add_weighted_call_edge(
                    graph,
                    mod_caller,
                    node,
                    script,
                    root_r,
                    root_s,
                    class_qnames,
                    mros,
                    classes,
                    method_loc_to_class,
                    lines,
                )

        for node in tree.body:
            if isinstance(node, ast.ClassDef):
                _emit_class_body_level_calls(
                    graph,
                    node,
                    mod,
                    (),
                    filepath,
                    script,
                    root_r,
                    root_s,
                    class_qnames,
                    mros,
                    classes,
                    method_loc_to_class,
                    lines,
                )

    return graph
