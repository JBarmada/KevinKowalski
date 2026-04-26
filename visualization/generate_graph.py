"""CLI entry point for interactive dependency visualization."""

import argparse
import sys
from pathlib import Path

import networkx as nx

from visualization.render import generate_interactive_graph
from visualization.utils import (
    aggregate_to_packages,
    build_function_graph,
    compute_metrics,
    find_cycle_info,
    parse_edges,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an interactive orbital dependency graph from Python source files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m visualization.generate_graph --path ./src
  python -m visualization.generate_graph --path ./myproject --output deps.html
        """,
    )
    parser.add_argument(
        "--path",
        type=Path,
        required=True,
        help="Root directory of Python source files to analyze.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("visualization/output/graph.html"),
        help="Output HTML file path (default: visualization/output/graph.html).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Don't open the browser automatically.",
    )
    args = parser.parse_args()

    if not args.path.exists():
        print(f"Error: {args.path} does not exist.", file=sys.stderr)
        sys.exit(1)

    if not args.path.is_dir():
        print(f"Error: {args.path} is not a directory.", file=sys.stderr)
        sys.exit(1)

    source_root = args.path.resolve()

    # --- File-level graph ---
    print(f"Parsing {source_root}...")
    edges, all_modules = parse_edges(source_root)

    file_graph = nx.DiGraph()
    file_edge_types: dict[tuple[str, str], bool] = {}

    for module in all_modules:
        file_graph.add_node(module)

    for edge in edges:
        file_graph.add_edge(edge.src, edge.dst)
        file_edge_types[(edge.src, edge.dst)] = edge.is_dynamic

    file_metrics = compute_metrics(file_graph)
    file_cycle_nodes, file_cycle_edges = find_cycle_info(file_graph)

    # --- Package-level graph ---
    print("Building package-level graph...")
    pkg_edges = aggregate_to_packages(edges)
    package_graph = nx.DiGraph()

    pkg_names = {m.split(".")[0] for m in all_modules}
    for pkg in pkg_names:
        package_graph.add_node(pkg)

    for edge in pkg_edges:
        package_graph.add_edge(edge.src, edge.dst)

    package_metrics = compute_metrics(package_graph)
    package_cycle_nodes, package_cycle_edges = find_cycle_info(package_graph)

    # --- Function-level graph ---
    print("Building function-level graph (this may take a moment)...")
    try:
        function_graph, function_metadata = build_function_graph(source_root)
    except Exception as e:
        print(f"Warning: Function graph generation failed: {e}", file=sys.stderr)
        function_graph = nx.DiGraph()
        function_metadata = {}

    function_metrics = compute_metrics(function_graph)
    function_cycle_nodes, function_cycle_edges = find_cycle_info(function_graph)

    # --- Summary ---
    dynamic_count = sum(1 for v in file_edge_types.values() if v)
    print(f"\nFile-level: {file_graph.number_of_nodes()} nodes, "
          f"{file_graph.number_of_edges()} edges ({dynamic_count} dynamic)")
    print(f"Package-level: {package_graph.number_of_nodes()} nodes, "
          f"{package_graph.number_of_edges()} edges")
    print(f"Function-level: {function_graph.number_of_nodes()} nodes, "
          f"{function_graph.number_of_edges()} edges")

    if file_cycle_nodes:
        print(f"Cycles (file): {len(file_cycle_nodes)} nodes involved")

    high_impact = [n for n, m in file_metrics.items() if m.impact > 0.7]
    high_suscept = [n for n, m in file_metrics.items() if m.susceptibility > 0.7]
    if high_impact:
        print(f"High impact (>0.7): {len(high_impact)} nodes")
    if high_suscept:
        print(f"High susceptibility (>0.7): {len(high_suscept)} nodes")

    # --- Generate visualization ---
    generate_interactive_graph(
        package_graph=package_graph,
        file_graph=file_graph,
        function_graph=function_graph,
        file_edge_types=file_edge_types,
        package_metrics=package_metrics,
        file_metrics=file_metrics,
        function_metrics=function_metrics,
        file_cycle_nodes=file_cycle_nodes,
        file_cycle_edges=file_cycle_edges,
        package_cycle_nodes=package_cycle_nodes,
        package_cycle_edges=package_cycle_edges,
        function_cycle_nodes=function_cycle_nodes,
        function_cycle_edges=function_cycle_edges,
        function_metadata=function_metadata,
        source_root=source_root,
        output_path=args.output,
        open_browser=not args.no_browser,
    )

    print(f"\nGenerated: {args.output}")
    if not args.no_browser:
        print("Opening in browser...")


if __name__ == "__main__":
    main()
