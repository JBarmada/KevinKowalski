"""Visualize import-level coupling across Flask's internal modules.

Parses every .py file under src/flask/ with the ast module, extracts relative
imports, and renders a directed dependency graph: an edge A -> B means module A
imports from module B.
"""

import argparse
import ast
from pathlib import Path

import matplotlib.pyplot as plt
import networkx as nx

_SKIP_DIRS = frozenset({".venv", "venv", ".git", "__pycache__", "node_modules",
                        ".tox", ".eggs", ".mypy_cache", "site-packages",
                        "dist", "build", ".nox", "htmlcov", ".pytest_cache"})


def _iter_py_files(source_root: Path) -> list[Path]:
    """Recursively find .py files, skipping virtual-env and build directories."""
    results: list[Path] = []
    for item in sorted(source_root.iterdir()):
        if item.name in _SKIP_DIRS or item.name.startswith("."):
            continue
        if item.is_file() and item.suffix == ".py":
            results.append(item)
        elif item.is_dir():
            results.extend(_iter_py_files(item))
    return results


# NOTE: [pedagogical] ast.parse gives us a full syntax tree without executing the
# code, so we can safely analyze any file — even one with side effects at import time.
def parse_edges(source_root: Path) -> list[tuple[str, str]]:
    """Walk source_root and return (importer, importee) pairs for every relative import."""
    edges = []

    for filepath in _iter_py_files(source_root):
        module_name = _module_name(filepath, source_root)
        tree = ast.parse(filepath.read_text(encoding="utf-8"))

        for node in ast.walk(tree):
            # NOTE: [pedagogical] ast.ImportFrom covers both `from . import x` and
            # `from .subpkg import x`. The `level` field counts the leading dots:
            # level=1 means `.`, level=2 means `..`, etc.
            if not isinstance(node, ast.ImportFrom) or node.level == 0:
                continue

            target = _resolve_target(module_name, node)
            if target is not None:
                edges.append((module_name, target))

    return edges


def _module_name(filepath: Path, source_root: Path) -> str:
    """Derive a short dotted module name relative to source_root."""
    parts = filepath.relative_to(source_root).with_suffix("").parts
    # NOTE: [thought process] a package's public name is the directory itself, not
    # `pkg.__init__`, so we drop the trailing `__init__` component.
    if parts[-1] == "__init__":
        parts = parts[:-1]
    # NOTE: [edge case callout] src/flask/__init__.py has no remaining parts after
    # stripping __init__, so we fall back to the literal name "__init__".
    return ".".join(parts) if parts else "__init__"


def _resolve_target(importer: str, node: ast.ImportFrom) -> str | None:
    """Turn a relative ImportFrom node into an absolute flask module name."""
    # Walk `level` dots up from the importer's package.
    # e.g. importer="json.tag", level=2 -> base package is "" (flask root)
    package_parts = importer.split(".")
    # NOTE: [edge case callout] level can exceed the package depth if the code is
    # malformed; we clamp to avoid a negative index.
    parent_parts = package_parts[: max(0, len(package_parts) - node.level)]

    if node.module:
        target_parts = parent_parts + node.module.split(".")
    else:
        # `from . import name` — each aliased name is a separate module import;
        # emit one edge per name.
        return None  # handled below in parse_edges via the names list

    return ".".join(target_parts) if target_parts else None


def _type_checking_imports(tree: ast.AST) -> set[int]:
    """Return the ids of ImportFrom nodes guarded by `if TYPE_CHECKING:`."""
    guarded: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.If):
            continue
        test = node.test
        # NOTE: [pedagogical] TYPE_CHECKING can appear as a bare name or as
        # `typing.TYPE_CHECKING` — we handle both forms here.
        is_type_checking = (isinstance(test, ast.Name) and test.id == "TYPE_CHECKING") or (
            isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"
        )
        if is_type_checking:
            for child in ast.walk(ast.Module(body=node.body, type_ignores=[])):
                if isinstance(child, ast.ImportFrom):
                    guarded.add(id(child))
    return guarded


# NOTE: [thought process] `from . import cli` has node.module=None, so _resolve_target
# returns None. We handle it here by treating each alias name as the target module.
def parse_edges_v2(source_root: Path) -> list[tuple[str, str]]:
    """Walk source_root and return all (importer, importee) edges, including bare `from . import x`."""
    edges = []

    for filepath in _iter_py_files(source_root):
        module_name = _module_name(filepath, source_root)
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
        type_checking_ids = _type_checking_imports(tree)

        for node in ast.walk(tree):
            if not isinstance(node, ast.ImportFrom) or node.level == 0:
                continue
            if id(node) in type_checking_ids:
                continue

            package_parts = module_name.split(".")
            parent_parts = package_parts[: max(0, len(package_parts) - node.level)]

            if node.module:
                # `from .submodule import X`
                target = ".".join(parent_parts + node.module.split("."))
                edges.append((module_name, target))
            else:
                # `from . import x, y` — each name is its own submodule
                for alias in node.names:
                    target = ".".join(parent_parts + [alias.name])
                    edges.append((module_name, target))

    # Deduplicate while preserving order.
    seen: set[tuple[str, str]] = set()
    unique = []
    for edge in edges:
        if edge not in seen and edge[0] != edge[1]:
            seen.add(edge)
            unique.append(edge)
    return unique


# === Main ===

def _main() -> None:
    parser = argparse.ArgumentParser(description="Visualize import-level coupling across Python modules.")
    parser.add_argument("source_dir", type=Path, help="Directory of Python source files to analyze.")
    args = parser.parse_args()

    source_root = args.source_dir
    edges = parse_edges_v2(source_root)

    graph = nx.DiGraph()
    graph.add_edges_from(edges)

    plt.figure(figsize=(14, 10))

    positions = nx.spring_layout(graph, seed=42, k=2.5)

    def instability(node: str) -> float:
        ca, ce = graph.in_degree(node), graph.out_degree(node)
        if ca + ce == 0:
            return 0.5
        return ce / (ca + ce)

    node_instabilities = [instability(node) for node in graph.nodes()]
    node_colors = plt.cm.plasma(node_instabilities)

    labels = {
        node: f"{node}\nCa:{graph.in_degree(node)}  Ce:{graph.out_degree(node)}\nI:{instability(node):.2f}"
        for node in graph.nodes()
    }

    nx.draw_networkx(
        graph,
        pos=positions,
        labels=labels,
        node_color=node_colors,
        node_size=2400,
        font_size=7,
        font_color="white",
        arrows=True,
        arrowsize=15,
        edge_color="gray",
    )

    for scc in nx.strongly_connected_components(graph):
        if len(scc) >= 2:
            print(sorted(scc))
        else:
            n = next(iter(scc))
            if graph.has_edge(n, n):
                print([n])

    folder_name = source_root.name
    plt.title(f"{folder_name} internal import graph  (A -> B means A imports from B)")
    plt.axis("off")
    plt.tight_layout()

    output_path = Path(f"output/{folder_name}_imports.png")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    plt.savefig(output_path, dpi=150, bbox_inches="tight")
    print(f"Saved to {output_path}")
    plt.show()


if __name__ == "__main__":
    _main()
