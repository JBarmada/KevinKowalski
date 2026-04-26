"""Pure functions that turn analyzer output into Markdown for the agent.

Snapshot in, string out. No I/O, no analyzer calls. Keeps the MCP tools
thin and these functions independently unit-testable.

Length budget: aim for under ~500 tokens (~2000 chars) per response so we
don't blow the agent's context. Truncate lists rather than verbose prose.
"""

import json
import re
from pathlib import Path

from contract import GraphSnapshot, ModuleMetrics


_VIOLATION_BLURBS = {
    "SDP": "Stable Dependencies Principle violation: an unstable module is depended on by a stable one.",
    "GOD_MODULE": "God module: too many responsibilities (high LCOM, high coupling).",
    "HIGH_CC": "Cyclomatic complexity hotspot: at least one function is hard to reason about.",
    "CYCLE": "Import cycle: modules transitively depend on each other.",
}


def _violation_explainer(rule_id: str) -> str:
    return _VIOLATION_BLURBS.get(rule_id, f"Rule {rule_id}.")


def format_analyze_repo(snap: GraphSnapshot) -> str:
    """Top-level summary: counts, worst offenders, overall instability."""
    n_modules = len(snap.modules)
    n_edges = len(snap.edges)
    avg_instability = (
        sum(m.instability for m in snap.modules.values()) / n_modules if n_modules else 0.0
    )
    flagged = [m for m in snap.modules.values() if m.violations]
    flagged.sort(key=lambda m: (-len(m.violations), -m.cc_max))

    lines = [
        f"## Repo analysis — `{snap.root}`",
        "",
        f"- Modules: **{n_modules}**",
        f"- Internal edges: **{n_edges}**",
        f"- Average instability: **{avg_instability:.2f}**",
        f"- Modules with violations: **{len(flagged)}**",
        "",
    ]
    if flagged:
        lines.append("### Top offenders")
        for m in flagged[:5]:
            lines.append(
                f"- `{m.module}` — {', '.join(m.violations)} "
                f"(Ca={m.ca}, Ce={m.ce}, I={m.instability:.2f}, CC={m.cc_max})"
            )
    else:
        lines.append("No violations detected.")
    return "\n".join(lines)


def format_module_health(snap: GraphSnapshot, module: str) -> str:
    """Per-module card with metrics + plain-English interpretation."""
    m = snap.modules.get(module)
    if m is None:
        available = ", ".join(sorted(snap.modules)[:10])
        return f"Module `{module}` not found. Known modules include: {available}"

    importers = [src for src, dst in snap.edges if dst == module]
    importees = [dst for src, dst in snap.edges if src == module]

    lines = [
        f"## Module `{m.module}`",
        f"- Path: `{m.path}`",
        f"- Afferent (Ca): **{m.ca}**  Efferent (Ce): **{m.ce}**",
        f"- Instability: **{m.instability:.2f}** "
        f"({'unstable' if m.instability >= 0.7 else 'stable' if m.instability <= 0.3 else 'mixed'})",
        f"- LCOM4: **{m.lcom4}**" if m.lcom4 is not None else "- LCOM4: n/a (no classes)",
        f"- Max cyclomatic: **{m.cc_max}**",
        "",
    ]
    if m.violations:
        lines.append("### Violations")
        for v in m.violations:
            lines.append(f"- **{v}** — {_violation_explainer(v)}")
        lines.append("")
    if importers:
        lines.append(f"### Imported by ({len(importers)})")
        lines.append(", ".join(f"`{x}`" for x in importers[:8]))
    if importees:
        lines.append(f"### Imports ({len(importees)})")
        lines.append(", ".join(f"`{x}`" for x in importees[:8]))
    return "\n".join(lines)


def format_suggest_refactor(snap: GraphSnapshot, feature_description: str) -> str:
    """Ranked list of decouplings to do *before* implementing the feature."""
    # Score: violations weighted by severity, then CC, then keyword overlap.
    keywords = {w.lower() for w in feature_description.split() if len(w) > 3}

    def score(m: ModuleMetrics) -> tuple[int, int, int]:
        violation_weight = sum(2 if v == "SDP" else 1 for v in m.violations)
        keyword_hits = sum(1 for kw in keywords if kw in m.module.lower() or kw in m.path.lower())
        return (-violation_weight, -m.cc_max, -keyword_hits)

    ranked = sorted(snap.modules.values(), key=score)
    candidates = [m for m in ranked if m.violations][:3]

    lines = [
        f"## Pre-feature decoupling advice",
        f"_Feature: {feature_description.strip()[:120]}_",
        "",
    ]
    if not candidates:
        lines.append("No structural blockers detected. Proceed with the feature.")
        return "\n".join(lines)

    lines.append("Address these **before** writing the feature — each one would amplify spaghetti otherwise:\n")
    for i, m in enumerate(candidates, 1):
        lines.append(
            f"{i}. **`{m.module}`** (Ca={m.ca}, Ce={m.ce}, I={m.instability:.2f}, CC={m.cc_max})"
        )
        for v in m.violations:
            lines.append(f"   - {_violation_explainer(v)}")
    return "\n".join(lines)


def format_check_change(check_result: dict) -> str:
    """Before/after delta with green/yellow/red verdict."""
    verdict = check_result.get("verdict", "unknown")
    tag = {"green": "[OK]", "yellow": "[WARN]", "red": "[FAIL]"}.get(verdict, "[?]")
    lines = [f"## Change check: {tag} **{verdict.upper()}**", ""]

    for change in check_result.get("changed", []):
        before: ModuleMetrics = change["before"]
        after: ModuleMetrics = change["after"]
        lines.append(f"### `{change['module']}`")
        lines.append(
            f"- Instability: {before.instability:.2f} -> **{after.instability:.2f}**"
        )
        lines.append(f"- Max CC: {before.cc_max} -> **{after.cc_max}**")
        if before.lcom4 is not None and after.lcom4 is not None:
            lines.append(f"- LCOM4: {before.lcom4} -> **{after.lcom4}**")
        lines.append("")

    new_v = check_result.get("new_violations", [])
    resolved_v = check_result.get("resolved_violations", [])
    if resolved_v:
        lines.append(f"**Resolved:** {', '.join(resolved_v)}")
    if new_v:
        lines.append(f"**Newly introduced:** {', '.join(new_v)}")
    return "\n".join(lines)


_REFACTOR_DIRECTION = (
    "**How to use these metrics in refactors**\n"
    "- **Susceptible** nodes (high *susceptibility*, driven by Ce-style fan-out through the graph) "
    "are fragile integration hubs. Prefer **reducing how many high-*impact* dependents** they have "
    "(predecessors in the graph): fewer modules/functions should transitively lean on them; break "
    "chains with interfaces, facades, or by moving stable logic outward.\n"
    "- **High-impact** nodes (high *impact*, driven by Ca-style fan-in) are expensive to change. "
    "Prefer **reducing how many high-*susceptibility* dependencies** they pull in (successors in the "
    "graph): narrow imports/calls, split responsibilities, or delegate volatile work behind stable seams.\n"
    "\n"
    "_Graphs and `compute_metrics` match the **visualization** pipeline (`generate_graph`): "
    "**package** = imports collapsed to top-level packages, **file** = dotted module / file graph, "
    "**function** = Jedi-resolved caller→callee edges._\n"
)


def format_refactor_assistance(payload: dict) -> str:
    """Markdown: Ca/Ce, instability, impact/susceptibility at package, file, and function levels."""
    lines = [
        "## Refactor assistance — coupling metrics",
        "",
        f"_Repo: `{payload.get('root', '')}`_",
        "",
        _REFACTOR_DIRECTION,
        "",
    ]

    for level_key, title in (
        ("package", "### Package level (collapsed import graph)"),
        ("file", "### File / module level (import graph)"),
        ("function", "### Function level (call graph)"),
    ):
        block = payload.get("levels", {}).get(level_key) or {}
        lines.append(title)
        lines.append(
            f"- Nodes: **{block.get('node_count', 0)}**, edges: **{block.get('edge_count', 0)}**"
        )
        if not block.get("high_susceptibility_detail") and not block.get("high_impact_detail"):
            lines.append("- _(No nodes or metrics available for this layer.)_")
            lines.append("")
            continue

        lines.append("")
        lines.append("#### High susceptibility — sample dependents (especially watch **high impact**)")
        for row in block.get("high_susceptibility_detail", [])[:6]:
            mid = row.get("id", "?")
            m = row.get("metrics", {})
            lines.append(
                f"- **`{mid}`** — Ca={m.get('ca', 0)}, Ce={m.get('ce', 0)}, "
                f"I={float(m.get('instability', 0)):.2f}, "
                f"impact={float(m.get('impact', 0)):.2f} (raw {float(m.get('raw_impact', 0)):.1f}), "
                f"susceptibility={float(m.get('susceptibility', 0)):.2f} "
                f"(raw {float(m.get('raw_susceptibility', 0)):.1f})"
            )
            for dep in row.get("high_impact_dependents", [])[:3]:
                did = dep.get("id", "?")
                lines.append(
                    f"  - depends-on-it: **`{did}`** — Ca={dep.get('ca')}, Ce={dep.get('ce')}, "
                    f"I={float(dep.get('instability', 0)):.2f}, raw impact={float(dep.get('raw_impact', 0)):.1f}"
                )

        lines.append("")
        lines.append("#### High impact — sample dependencies (especially watch **high susceptibility**)")
        for row in block.get("high_impact_detail", [])[:6]:
            mid = row.get("id", "?")
            m = row.get("metrics", {})
            lines.append(
                f"- **`{mid}`** — Ca={m.get('ca', 0)}, Ce={m.get('ce', 0)}, "
                f"I={float(m.get('instability', 0)):.2f}, "
                f"impact={float(m.get('impact', 0)):.2f} (raw {float(m.get('raw_impact', 0)):.1f}), "
                f"susceptibility={float(m.get('susceptibility', 0)):.2f} "
                f"(raw {float(m.get('raw_susceptibility', 0)):.1f})"
            )
            for dep in row.get("high_susceptibility_dependencies", [])[:3]:
                did = dep.get("id", "?")
                lines.append(
                    f"  - depends-on: **`{did}`** — Ce={dep.get('ce')}, Ca={dep.get('ca')}, "
                    f"I={float(dep.get('instability', 0)):.2f}, "
                    f"raw susceptibility={float(dep.get('raw_susceptibility', 0)):.1f}"
                )
        lines.append("")

    return "\n".join(lines)


def viz_html_path_from_generate_stdout(stdout: str, cwd: Path) -> Path:
    """Resolve the HTML path from ``visualization.generate_graph`` stdout (``Generated:`` line)."""
    m = re.search(r"^Generated:\s*(.+)$", stdout, re.MULTILINE)
    if not m:
        raise ValueError("visualization.generate_graph did not print a Generated: line")
    p = Path(m.group(1).strip())
    if p.is_absolute():
        return p.resolve()
    return (cwd / p).resolve()


def format_generate_graph(result: dict) -> str:
    """Format the result of interactive graph generation."""
    lines = [
        f"## Dependency Graph Generated",
        "",
        f"- **Output:** `{result['output_path']}`",
        f"- **File-level:** {result['file_nodes']} nodes, {result['file_edges']} edges",
        f"- **Package-level:** {result['package_nodes']} nodes, {result['package_edges']} edges",
        f"- **Function-level:** {result['function_nodes']} nodes, {result['function_edges']} edges",
        "",
    ]
    if result.get("file_cycle_count", 0) > 0:
        lines.append(f"**Cycles detected:** {result['file_cycle_count']} nodes involved in cycles")

    high_impact = result.get("high_impact_count", 0)
    high_suscept = result.get("high_susceptibility_count", 0)
    if high_impact > 0:
        lines.append(f"**High impact modules (>0.7):** {high_impact}")
    if high_suscept > 0:
        lines.append(f"**High susceptibility modules (>0.7):** {high_suscept}")

    if not result.get("file_cycle_count") and not high_impact and not high_suscept:
        lines.append("No architectural concerns detected.")

    return "\n".join(lines)


def format_metric_graph(snap: GraphSnapshot) -> str:
    """JSON node/edge dump from the analyzer ``GraphSnapshot`` (module graph only)."""
    payload = {
        "root": snap.root,
        "nodes": [
            {
                "id": m.module,
                "path": m.path,
                "metrics": {
                    "ca": m.ca,
                    "ce": m.ce,
                    "instability": m.instability,
                    "lcom4": m.lcom4,
                    "cc_max": m.cc_max,
                },
                "violations": m.violations,
            }
            for m in snap.modules.values()
        ],
        "edges": [{"from": s, "to": d} for s, d in snap.edges],
    }
    return json.dumps(payload, indent=2)
