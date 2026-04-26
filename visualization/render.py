"""Interactive HTML visualization using PyVis."""

import json
import shutil
import subprocess
import webbrowser
from pathlib import Path

import matplotlib.cm as cm
import matplotlib.colors as mcolors
import networkx as nx
from pyvis.network import Network

from visualization.utils import NodeMetrics, shorten_label


def instability_to_color(value: float) -> str:
    """Map instability [0,1] to hex color using plasma colormap."""
    rgba = cm.plasma(value)
    return mcolors.to_hex(rgba)


def _build_tooltip(node: str, metrics: NodeMetrics) -> str:
    """Build HTML tooltip content for a node."""
    return (
        f"<b>{node}</b><br>"
        f"<hr style='margin:4px 0'>"
        f"Ca (dependents): {metrics.ca}<br>"
        f"Ce (dependencies): {metrics.ce}<br>"
        f"Instability: {metrics.instability:.2f}"
    )


def _try_graphviz_layout(graph: nx.DiGraph) -> dict[str, tuple[float, float]] | None:
    """Try to compute hierarchical layout using graphviz if available."""
    if shutil.which("dot") is None:
        return None

    try:
        from networkx.drawing.nx_pydot import graphviz_layout

        positions = graphviz_layout(graph, prog="dot")
        if positions:
            max_y = max(y for _, y in positions.values())
            return {node: (x, max_y - y) for node, (x, y) in positions.items()}
    except Exception:
        pass

    return None


def _get_vis_options(use_graphviz_positions: bool) -> dict:
    """Return vis.js configuration options."""
    if use_graphviz_positions:
        return {
            "nodes": {"shape": "dot", "font": {"size": 12, "face": "monospace"}},
            "edges": {
                "arrows": {"to": {"enabled": True, "scaleFactor": 0.5}},
                "smooth": {"type": "cubicBezier", "forceDirection": "vertical", "roundness": 0.4},
            },
            "layout": {"hierarchical": {"enabled": False}},
            "physics": {"enabled": False},
            "interaction": {
                "hover": True,
                "navigationButtons": True,
                "keyboard": True,
                "tooltipDelay": 100,
            },
        }

    return {
        "nodes": {"shape": "dot", "font": {"size": 12, "face": "monospace"}},
        "edges": {
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.5}},
            "smooth": {"type": "cubicBezier", "forceDirection": "vertical", "roundness": 0.4},
        },
        "layout": {
            "hierarchical": {
                "enabled": True,
                "direction": "UD",
                "sortMethod": "directed",
                "levelSeparation": 180,
                "nodeSpacing": 140,
                "treeSpacing": 200,
                "blockShifting": True,
                "edgeMinimization": True,
                "parentCentralization": True,
            }
        },
        "physics": {
            "enabled": False,
            "hierarchicalRepulsion": {
                "centralGravity": 0.0,
                "springLength": 150,
                "nodeDistance": 150,
            },
            "solver": "hierarchicalRepulsion",
        },
        "interaction": {
            "hover": True,
            "navigationButtons": True,
            "keyboard": True,
            "tooltipDelay": 100,
        },
    }


def _inject_enhancements(
    html: str,
    cycle_nodes: set[str],
    cycle_edges: set[tuple[str, str]],
    metrics: dict[str, NodeMetrics],
) -> str:
    """Inject control panel and custom JavaScript into the HTML."""
    cycle_nodes_js = json.dumps(list(cycle_nodes))
    unstable_nodes_js = json.dumps([n for n, m in metrics.items() if m.instability > 0.7])
    cycle_count = len(cycle_nodes)
    unstable_count = len([n for n, m in metrics.items() if m.instability > 0.7])

    control_panel = f"""
<div id="control-panel" style="
    position: fixed;
    top: 15px;
    right: 15px;
    z-index: 1000;
    background: rgba(255, 255, 255, 0.97);
    border: 1px solid #ddd;
    border-radius: 8px;
    padding: 14px 16px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.15);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', monospace;
    min-width: 200px;
    color: #333;
    font-size: 13px;
">
  <div style="font-size: 14px; font-weight: 600; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #eee;">
    Dependency Explorer
  </div>
  <div style="display: flex; flex-direction: column; gap: 6px; margin-bottom: 14px;">
    <button onclick="resetFocus()" style="padding: 6px 10px; background: #f5f5f5; border: 1px solid #ccc; color: #333; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      ↺ Reset View
    </button>
    <button onclick="showCyclesOnly()" style="padding: 6px 10px; background: #fff5f5; border: 1px solid #e88; color: #c44; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      ⚠ Cycles Only ({cycle_count})
    </button>
    <button onclick="showUnstableOnly()" style="padding: 6px 10px; background: #fffbeb; border: 1px solid #d9a; color: #a70; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      ⚡ Unstable Only ({unstable_count})
    </button>
    <button onclick="togglePhysics()" style="padding: 6px 10px; background: #f5f5f5; border: 1px solid #ccc; color: #333; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      ⚙ Toggle Physics
    </button>
  </div>
  <div style="font-size: 11px; color: #666; border-top: 1px solid #eee; padding-top: 10px;">
    <div style="font-weight: 600; margin-bottom: 6px; color: #444;">Legend</div>
    <div style="display: flex; align-items: center; gap: 8px; margin: 4px 0;">
      <span style="display: inline-block; width: 40px; height: 10px; background: linear-gradient(to right, #440154, #cc4778, #f0f921); border-radius: 2px;"></span>
      <span>Instability 0→1</span>
    </div>
    <div style="display: flex; align-items: center; gap: 8px; margin: 4px 0;">
      <span style="display: inline-block; width: 12px; height: 12px; border: 3px solid #e44; border-radius: 50%; background: transparent;"></span>
      <span>In cycle</span>
    </div>
    <div style="display: flex; align-items: center; gap: 8px; margin: 4px 0;">
      <span style="display: inline-block; width: 24px; height: 2px; background: #888;"></span>
      <span>Static import</span>
    </div>
    <div style="display: flex; align-items: center; gap: 8px; margin: 4px 0;">
      <span style="display: inline-block; width: 24px; border-top: 2px dashed #888;"></span>
      <span>Dynamic import</span>
    </div>
    <div style="margin-top: 10px; padding-top: 8px; border-top: 1px solid #eee; color: #888; font-size: 10px;">
      Click node to focus on its dependencies
    </div>
  </div>
</div>
"""

    custom_js = f"""
<script type="text/javascript">
(function() {{
    var cycleNodes = {cycle_nodes_js};
    var unstableNodes = {unstable_nodes_js};
    var originalStyles = {{}};
    var focusedNode = null;
    var physicsEnabled = false;

    function storeOriginalStyles() {{
        nodes.forEach(function(n) {{
            originalStyles[n.id] = {{
                color: JSON.parse(JSON.stringify(n.color || {{}})),
                borderWidth: n.borderWidth || 1,
                font: JSON.parse(JSON.stringify(n.font || {{}})),
                size: n.size
            }};
        }});
        edges.forEach(function(e) {{
            originalStyles['edge_' + e.id] = {{
                color: e.color,
                width: e.width || 1,
                dashes: e.dashes
            }};
        }});
    }}

    setTimeout(storeOriginalStyles, 300);
    network.once('stabilized', storeOriginalStyles);

    function fadeNode(nodeId) {{
        nodes.update({{
            id: nodeId,
            color: {{ background: '#e8e8e8', border: '#ccc' }},
            font: {{ color: '#bbb' }}
        }});
    }}

    function restoreNode(nodeId) {{
        var orig = originalStyles[nodeId];
        if (orig) {{
            nodes.update({{
                id: nodeId,
                color: orig.color,
                borderWidth: orig.borderWidth,
                font: orig.font,
                size: orig.size
            }});
        }}
    }}

    function fadeEdge(edgeId) {{
        edges.update({{
            id: edgeId,
            color: {{ color: '#eee', opacity: 0.3 }},
            width: 0.5
        }});
    }}

    function restoreEdge(edgeId) {{
        var orig = originalStyles['edge_' + edgeId];
        if (orig) {{
            edges.update({{
                id: edgeId,
                color: orig.color,
                width: orig.width,
                dashes: orig.dashes
            }});
        }}
    }}

    window.focusOnNode = function(nodeId) {{
        focusedNode = nodeId;
        var connectedNodes = new Set(network.getConnectedNodes(nodeId));
        var connectedEdges = new Set(network.getConnectedEdges(nodeId));

        nodes.forEach(function(n) {{
            if (n.id === nodeId) {{
                nodes.update({{ id: n.id, borderWidth: 4 }});
            }} else if (!connectedNodes.has(n.id)) {{
                fadeNode(n.id);
            }}
        }});

        edges.forEach(function(e) {{
            if (!connectedEdges.has(e.id)) {{
                fadeEdge(e.id);
            }} else {{
                edges.update({{ id: e.id, width: 2.5 }});
            }}
        }});
    }};

    window.resetFocus = function() {{
        focusedNode = null;
        nodes.forEach(function(n) {{ restoreNode(n.id); }});
        edges.forEach(function(e) {{ restoreEdge(e.id); }});
    }};

    window.showCyclesOnly = function() {{
        if (cycleNodes.length === 0) {{
            alert('No cycles detected in this codebase.');
            return;
        }}
        resetFocus();
        var cycleSet = new Set(cycleNodes);
        nodes.forEach(function(n) {{
            if (!cycleSet.has(n.id)) fadeNode(n.id);
        }});
    }};

    window.showUnstableOnly = function() {{
        if (unstableNodes.length === 0) {{
            alert('No highly unstable modules (I > 0.7) found.');
            return;
        }}
        resetFocus();
        var unstableSet = new Set(unstableNodes);
        nodes.forEach(function(n) {{
            if (!unstableSet.has(n.id)) fadeNode(n.id);
        }});
    }};

    window.togglePhysics = function() {{
        physicsEnabled = !physicsEnabled;
        network.setOptions({{ physics: {{ enabled: physicsEnabled }} }});
    }};

    network.on('click', function(params) {{
        if (params.nodes.length > 0) {{
            var clicked = params.nodes[0];
            if (focusedNode === clicked) {{
                resetFocus();
            }} else {{
                resetFocus();
                focusOnNode(clicked);
            }}
        }} else if (params.edges.length === 0) {{
            resetFocus();
        }}
    }});
}})();
</script>
"""

    html = html.replace("</body>", control_panel + custom_js + "\n</body>")
    return html


def generate_interactive_graph(
    graph: nx.DiGraph,
    edge_types: dict[tuple[str, str], bool],
    metrics: dict[str, NodeMetrics],
    cycle_nodes: set[str],
    cycle_edges: set[tuple[str, str]],
    output_path: Path,
    open_browser: bool = True,
) -> None:
    """Generate an interactive HTML visualization of the dependency graph."""
    graphviz_positions = _try_graphviz_layout(graph)
    use_graphviz = graphviz_positions is not None

    net = Network(
        height="100vh",
        width="100%",
        directed=True,
        bgcolor="#fafafa",
        font_color="#333",
        cdn_resources="in_line",
    )

    max_ca = max((m.ca for m in metrics.values()), default=1) or 1

    for node in graph.nodes():
        m = metrics[node]
        color_hex = instability_to_color(m.instability)
        size = 12 + (m.ca / max_ca) * 28

        in_cycle = node in cycle_nodes
        border_color = "#e44444" if in_cycle else color_hex
        border_width = 3 if in_cycle else 1

        node_opts = {
            "label": shorten_label(node),
            "title": _build_tooltip(node, m),
            "color": {
                "background": color_hex,
                "border": border_color,
                "highlight": {"background": "#fff", "border": "#f80"},
                "hover": {"background": "#ffc", "border": "#f80"},
            },
            "size": size,
            "borderWidth": border_width,
            "font": {"size": 11, "color": "#333", "face": "monospace"},
        }

        if use_graphviz and node in graphviz_positions:
            x, y = graphviz_positions[node]
            node_opts["x"] = x
            node_opts["y"] = y
            node_opts["physics"] = False

        net.add_node(node, **node_opts)

    for src, dst in graph.edges():
        is_dynamic = edge_types.get((src, dst), False)
        is_cycle_edge = (src, dst) in cycle_edges

        edge_color = "#e44444" if is_cycle_edge else "#888888"
        edge_width = 1.5 if is_cycle_edge else 1

        net.add_edge(
            src,
            dst,
            color=edge_color,
            width=edge_width,
            dashes=is_dynamic,
            arrows="to",
            title="dynamic import" if is_dynamic else "static import",
        )

    options = _get_vis_options(use_graphviz)
    net.set_options(json.dumps(options))

    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        html = net.generate_html()
    except AttributeError:
        import tempfile

        with tempfile.NamedTemporaryFile(suffix=".html", delete=False) as f:
            tmp = Path(f.name)
        net.show(str(tmp), notebook=False)
        html = tmp.read_text(encoding="utf-8")
        tmp.unlink()

    html = _inject_enhancements(html, cycle_nodes, cycle_edges, metrics)
    output_path.write_text(html, encoding="utf-8")

    if open_browser:
        webbrowser.open(f"file://{output_path.absolute()}")
