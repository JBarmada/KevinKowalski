"""Pure functions that turn analyzer output into Markdown for the agent.

Snapshot in, string out. No I/O, no analyzer calls. Keeps the MCP tools
thin and these functions independently unit-testable.

Length budget: aim for under ~500 tokens (~2000 chars) per response so we
don't blow the agent's context. Truncate lists rather than verbose prose.
"""

import json

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
