"""CLI entry point for interactive dependency visualization."""

import argparse
import sys
from pathlib import Path

import networkx as nx

from visualization.render import generate_interactive_graph
from visualization.utils import (
    aggregate_to_packages,
    compute_metrics,
    find_cycle_info,
    parse_edges,
)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an interactive dependency graph from Python source files.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m visualization.generate_graph --path ./src
  python -m visualization.generate_graph --path ./flask --granularity package
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
        "--granularity",
        choices=["file", "package"],
        default="file",
        help="Graph granularity: file-level or package-level (default: file).",
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

    print(f"Parsing {args.path}...")
    edges = parse_edges(args.path)

    if args.granularity == "package":
        edges = aggregate_to_packages(edges)
        print("Aggregated to package-level.")

    if not edges:
        print("No import edges found.")
        print("Check that your directory contains .py files with relative imports.")
        sys.exit(0)

    graph = nx.DiGraph()
    edge_types: dict[tuple[str, str], bool] = {}

    for edge in edges:
        graph.add_edge(edge.src, edge.dst)
        edge_types[(edge.src, edge.dst)] = edge.is_dynamic

    metrics = compute_metrics(graph)
    cycle_nodes, cycle_edges = find_cycle_info(graph)

    dynamic_count = sum(1 for v in edge_types.values() if v)
    print(f"Nodes: {graph.number_of_nodes()}")
    print(f"Edges: {graph.number_of_edges()} ({dynamic_count} dynamic)")

    if cycle_nodes:
        print(f"Cycles: {len(cycle_nodes)} nodes involved")

    unstable = [n for n, m in metrics.items() if m.instability > 0.7]
    if unstable:
        print(f"Unstable (I > 0.7): {len(unstable)} nodes")

    generate_interactive_graph(
        graph=graph,
        edge_types=edge_types,
        metrics=metrics,
        cycle_nodes=cycle_nodes,
        cycle_edges=cycle_edges,
        output_path=args.output,
        open_browser=not args.no_browser,
    )

    print(f"\nGenerated: {args.output}")
    if not args.no_browser:
        print("Opening in browser...")


if __name__ == "__main__":
    main()
