"""Interactive HTML visualization using an orbital dependency graph model."""

import json
import math
import webbrowser
from pathlib import Path

import matplotlib.colors as mcolors
import networkx as nx
from pyvis.network import Network

from visualization.utils import NodeMetrics, truncate_label


def default_color(impact: float) -> str:
    """Map impact [0,1] to a brown→green gradient (tree metaphor).

    High impact (1.0) = deep brown (trunk), low impact (0.0) = green (leaves).
    """
    brown = (0.36, 0.25, 0.22)
    green = (0.30, 0.69, 0.31)
    r = brown[0] + (green[0] - brown[0]) * (1 - impact)
    g = brown[1] + (green[1] - brown[1]) * (1 - impact)
    b = brown[2] + (green[2] - brown[2]) * (1 - impact)
    return mcolors.to_hex((r, g, b))


def susceptibility_color(value: float) -> str:
    """Map susceptibility [0,1] to a light-blue→deep-purple gradient."""
    light = (0.91, 0.92, 0.96)
    deep = (0.19, 0.11, 0.57)
    r = light[0] + (deep[0] - light[0]) * value
    g = light[1] + (deep[1] - light[1]) * value
    b = light[2] + (deep[2] - light[2]) * value
    return mcolors.to_hex((r, g, b))


def impact_toggle_color(value: float) -> str:
    """Map impact [0,1] to a light-yellow→deep-orange gradient."""
    light = (1.0, 0.97, 0.88)
    deep = (0.90, 0.32, 0.0)
    r = light[0] + (deep[0] - light[0]) * value
    g = light[1] + (deep[1] - light[1]) * value
    b = light[2] + (deep[2] - light[2]) * value
    return mcolors.to_hex((r, g, b))


def _build_tooltip(node: str, metrics: NodeMetrics, file_path: str | None = None) -> str:
    """Build plain-text tooltip content for a node."""
    lines = [
        node,
        "─" * min(len(node), 30),
        f"Impact: {metrics.impact:.2f}",
        f"Susceptibility: {metrics.susceptibility:.2f}",
        "",
        f"Ca (dependents): {metrics.ca}",
        f"Ce (dependencies): {metrics.ce}",
        f"Instability: {metrics.instability:.2f}",
    ]
    if file_path:
        lines.extend(["", f"File: {file_path}"])
    return "\n".join(lines)


def _compute_orbital_positions(
    graph: nx.DiGraph, metrics: dict[str, NodeMetrics]
) -> dict[str, tuple[float, float]]:
    """Place nodes in concentric rings by impact.

    High-impact nodes near center, low-impact at periphery.
    """
    if not metrics:
        return {}

    sorted_nodes = sorted(metrics.keys(), key=lambda n: metrics[n].impact, reverse=True)
    n = len(sorted_nodes)
    positions: dict[str, tuple[float, float]] = {}

    if n == 0:
        return positions

    num_rings = max(1, int(math.ceil(math.sqrt(n))))
    ring_radius_step = 250

    node_idx = 0
    for ring in range(num_rings):
        if node_idx >= n:
            break

        radius = (ring + 1) * ring_radius_step if ring > 0 else 0

        if ring == 0:
            nodes_in_ring = min(1, n - node_idx)
        else:
            circumference = 2 * math.pi * radius
            max_per_ring = max(1, int(circumference / 80))
            remaining = n - node_idx
            rings_left = num_rings - ring
            nodes_in_ring = min(max_per_ring, remaining, max(1, remaining // rings_left + 1))

        for i in range(nodes_in_ring):
            if node_idx >= n:
                break
            node = sorted_nodes[node_idx]
            if radius == 0:
                positions[node] = (0.0, 0.0)
            else:
                angle = (2 * math.pi * i) / nodes_in_ring
                jitter = (hash(node) % 100 - 50) * 0.3
                x = radius * math.cos(angle) + jitter
                y = radius * math.sin(angle) + jitter
                positions[node] = (x, y)
            node_idx += 1

    return positions


def _get_orbital_vis_options() -> dict:
    """Return vis.js configuration for the orbital layout."""
    return {
        "nodes": {
            "shape": "dot",
            "font": {"size": 11, "face": "monospace", "color": "#ddd"},
        },
        "edges": {
            "arrows": {"to": {"enabled": True, "scaleFactor": 0.5}},
            "smooth": {"type": "continuous", "roundness": 0.2},
            "color": {"color": "#555555", "opacity": 0.6},
        },
        "layout": {"hierarchical": {"enabled": False}},
        "physics": {
            "enabled": True,
            "barnesHut": {
                "gravitationalConstant": -3000,
                "centralGravity": 0.5,
                "springLength": 150,
                "springConstant": 0.02,
                "damping": 0.3,
                "avoidOverlap": 0.3,
            },
            "solver": "barnesHut",
            "stabilization": {"iterations": 200, "fit": True},
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
    all_graphs: dict,
    all_metrics: dict,
    all_cycles: dict,
    function_metadata: dict[str, dict],
    source_root_str: str,
) -> str:
    """Inject control panel and custom JavaScript for the orbital visualization."""

    graphs_json = json.dumps(all_graphs)
    metrics_json = json.dumps(
        {
            view: {
                node: {"impact": m.impact, "susceptibility": m.susceptibility, "ca": m.ca, "ce": m.ce}
                for node, m in mets.items()
            }
            for view, mets in all_metrics.items()
        }
    )
    cycles_json = json.dumps(
        {
            view: {"nodes": list(cn), "edges": [list(e) for e in ce]}
            for view, (cn, ce) in all_cycles.items()
        }
    )
    func_meta_json = json.dumps(function_metadata)

    control_panel = f"""
<div id="control-panel" style="
    position: fixed;
    top: 15px;
    right: 15px;
    z-index: 1000;
    background: rgba(30, 30, 35, 0.95);
    border: 1px solid #444;
    border-radius: 10px;
    padding: 16px 18px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.4);
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', monospace;
    min-width: 220px;
    color: #ccc;
    font-size: 13px;
    backdrop-filter: blur(10px);
">
  <div style="font-size: 15px; font-weight: 600; margin-bottom: 12px; padding-bottom: 8px; border-bottom: 1px solid #555; color: #eee;">
    Dependency Explorer
  </div>

  <div style="font-size: 11px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px;">
    Granularity
  </div>
  <div style="display: flex; gap: 4px; margin-bottom: 14px;">
    <button id="btn-package" onclick="switchView('package')" style="flex:1; padding: 6px 8px; background: #333; border: 1px solid #555; color: #ccc; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 11px;">
      Package
    </button>
    <button id="btn-file" onclick="switchView('file')" style="flex:1; padding: 6px 8px; background: #555; border: 1px solid #888; color: #fff; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 11px;">
      File
    </button>
    <button id="btn-function" onclick="switchView('function')" style="flex:1; padding: 6px 8px; background: #333; border: 1px solid #555; color: #ccc; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 11px;">
      Function
    </button>
  </div>

  <div style="font-size: 11px; font-weight: 600; color: #999; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px;">
    Color Mode
  </div>
  <div style="display: flex; flex-direction: column; gap: 5px; margin-bottom: 14px;">
    <button id="btn-default-color" onclick="setColorMode('default')" style="padding: 6px 10px; background: linear-gradient(to right, #4CAF50, #5D4037); border: 1px solid #555; color: #fff; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      Default (Impact: Green→Brown)
    </button>
    <button id="btn-susceptibility" onclick="setColorMode('susceptibility')" style="padding: 6px 10px; background: linear-gradient(to right, #E8EAF6, #311B92); border: 1px solid #555; color: #333; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      Susceptibility (Blue→Purple)
    </button>
    <button id="btn-impact" onclick="setColorMode('impact')" style="padding: 6px 10px; background: linear-gradient(to right, #FFF8E1, #E65100); border: 1px solid #555; color: #333; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      Impact (Yellow→Orange)
    </button>
    <button id="btn-cycles" onclick="setColorMode('cycles')" style="padding: 6px 10px; background: #3a1a1a; border: 1px solid #e44; color: #f88; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      Cycles (Red Highlight)
    </button>
  </div>

  <div style="display: flex; flex-direction: column; gap: 5px; margin-bottom: 14px;">
    <button onclick="resetView()" style="padding: 6px 10px; background: #333; border: 1px solid #555; color: #ccc; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      ↺ Reset View
    </button>
  </div>

  <div style="font-size: 10px; color: #888; border-top: 1px solid #444; padding-top: 10px;">
    <div style="font-weight: 600; margin-bottom: 6px; color: #999;">Controls</div>
    <div style="margin: 3px 0;">Click node → highlight edges in blue</div>
    <div style="margin: 3px 0;">Ctrl+Click → open in VS Code</div>
    <div style="margin: 3px 0;">Hover → show full name &amp; metrics</div>
  </div>
</div>
"""

    custom_js = f"""
<script type="text/javascript">
(function() {{
    var allGraphs = {graphs_json};
    var allMetrics = {metrics_json};
    var allCycles = {cycles_json};
    var funcMeta = {func_meta_json};
    var sourceRoot = {json.dumps(source_root_str)};

    var currentView = 'file';
    var currentColorMode = 'default';
    var focusedNode = null;
    var originalStyles = {{}};

    function lerp(a, b, t) {{
        return a + (b - a) * t;
    }}

    function defaultColor(impact) {{
        var brown = [0.36, 0.25, 0.22];
        var green = [0.30, 0.69, 0.31];
        var r = lerp(brown[0], green[0], 1 - impact);
        var g = lerp(brown[1], green[1], 1 - impact);
        var b = lerp(brown[2], green[2], 1 - impact);
        return 'rgb(' + Math.round(r*255) + ',' + Math.round(g*255) + ',' + Math.round(b*255) + ')';
    }}

    function susceptibilityColor(val) {{
        var light = [0.91, 0.92, 0.96];
        var deep = [0.19, 0.11, 0.57];
        var r = lerp(light[0], deep[0], val);
        var g = lerp(light[1], deep[1], val);
        var b = lerp(light[2], deep[2], val);
        return 'rgb(' + Math.round(r*255) + ',' + Math.round(g*255) + ',' + Math.round(b*255) + ')';
    }}

    function impactColor(val) {{
        var light = [1.0, 0.97, 0.88];
        var deep = [0.90, 0.32, 0.0];
        var r = lerp(light[0], deep[0], val);
        var g = lerp(light[1], deep[1], val);
        var b = lerp(light[2], deep[2], val);
        return 'rgb(' + Math.round(r*255) + ',' + Math.round(g*255) + ',' + Math.round(b*255) + ')';
    }}

    function getNodeColor(nodeId, mode) {{
        var m = allMetrics[currentView] && allMetrics[currentView][nodeId];
        if (!m) return '#888';
        if (mode === 'default') return defaultColor(m.impact);
        if (mode === 'susceptibility') return susceptibilityColor(m.susceptibility);
        if (mode === 'impact') return impactColor(m.impact);
        return defaultColor(m.impact);
    }}

    function getNodeSize(nodeId) {{
        var m = allMetrics[currentView] && allMetrics[currentView][nodeId];
        if (!m) return 15;
        return 10 + m.impact * 35;
    }}

    function storeOriginalStyles() {{
        originalStyles = {{}};
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

    function loadView(viewName) {{
        var graphData = allGraphs[viewName];
        if (!graphData) return;

        var newNodes = [];
        var newEdges = [];

        graphData.nodes.forEach(function(n) {{
            var color = getNodeColor(n.id, currentColorMode);
            var size = getNodeSize(n.id);
            var nodeObj = {{
                id: n.id,
                label: n.label,
                title: n.title,
                color: {{ background: color, border: color, highlight: {{ background: '#fff', border: '#f80' }}, hover: {{ background: '#ffc', border: '#f80' }} }},
                size: size,
                borderWidth: 1,
                font: {{ size: viewName === 'package' ? 9 : 11, color: '#ddd', face: 'monospace' }}
            }};
            if (n.x !== undefined) {{
                nodeObj.x = n.x;
                nodeObj.y = n.y;
            }}
            newNodes.push(nodeObj);
        }});

        graphData.edges.forEach(function(e) {{
            newEdges.push({{
                from: e.from,
                to: e.to,
                color: {{ color: e.color || '#555555', opacity: 0.6 }},
                width: e.width || 1,
                dashes: e.dashes || false,
                arrows: 'to',
                title: e.title || ''
            }});
        }});

        nodes.clear();
        edges.clear();
        nodes.add(newNodes);
        edges.add(newEdges);

        setTimeout(function() {{
            storeOriginalStyles();
            network.fit();
        }}, 500);
    }}

    function updateButtonStyles(viewName) {{
        ['package', 'file', 'function'].forEach(function(v) {{
            var btn = document.getElementById('btn-' + v);
            if (btn) {{
                if (v === viewName) {{
                    btn.style.background = '#555';
                    btn.style.borderColor = '#888';
                    btn.style.color = '#fff';
                }} else {{
                    btn.style.background = '#333';
                    btn.style.borderColor = '#555';
                    btn.style.color = '#ccc';
                }}
            }}
        }});
    }}

    window.switchView = function(viewName) {{
        currentView = viewName;
        focusedNode = null;
        updateButtonStyles(viewName);
        loadView(viewName);
    }};

    window.setColorMode = function(mode) {{
        currentColorMode = mode;
        focusedNode = null;

        if (mode === 'cycles') {{
            var cycleData = allCycles[currentView];
            var cycleNodeSet = new Set(cycleData ? cycleData.nodes : []);
            var cycleEdgeSet = new Set((cycleData ? cycleData.edges : []).map(function(e) {{ return e[0] + '>>>' + e[1]; }}));

            if (cycleNodeSet.size === 0) {{
                alert('No cycles detected in this view.');
                return;
            }}

            nodes.forEach(function(n) {{
                if (cycleNodeSet.has(n.id)) {{
                    nodes.update({{
                        id: n.id,
                        color: {{ background: '#e44444', border: '#ff6666', highlight: {{ background: '#ff8888', border: '#ffaaaa' }}, hover: {{ background: '#ff6666', border: '#ff8888' }} }},
                        borderWidth: 3
                    }});
                }} else {{
                    nodes.update({{
                        id: n.id,
                        color: {{ background: '#2a2a2a', border: '#444' }},
                        font: {{ color: '#666' }},
                        borderWidth: 1
                    }});
                }}
            }});

            edges.forEach(function(e) {{
                var edgeKey = e.from + '>>>' + e.to;
                if (cycleEdgeSet.has(edgeKey)) {{
                    edges.update({{
                        id: e.id,
                        color: {{ color: '#e44444', opacity: 1.0 }},
                        width: 2.5
                    }});
                }} else {{
                    edges.update({{
                        id: e.id,
                        color: {{ color: '#333', opacity: 0.2 }},
                        width: 0.5
                    }});
                }}
            }});
            storeOriginalStyles();
            return;
        }}

        nodes.forEach(function(n) {{
            var color = getNodeColor(n.id, mode);
            var size = getNodeSize(n.id);
            nodes.update({{
                id: n.id,
                color: {{ background: color, border: color, highlight: {{ background: '#fff', border: '#f80' }}, hover: {{ background: '#ffc', border: '#f80' }} }},
                size: size,
                borderWidth: 1,
                font: {{ color: '#ddd' }}
            }});
        }});

        edges.forEach(function(e) {{
            edges.update({{
                id: e.id,
                color: {{ color: '#555555', opacity: 0.6 }},
                width: 1
            }});
        }});
        storeOriginalStyles();
    }};

    window.resetView = function() {{
        focusedNode = null;
        nodes.forEach(function(n) {{
            var orig = originalStyles[n.id];
            if (orig) {{
                nodes.update({{
                    id: n.id,
                    color: orig.color,
                    borderWidth: orig.borderWidth,
                    font: orig.font,
                    size: orig.size
                }});
            }}
        }});
        edges.forEach(function(e) {{
            var orig = originalStyles['edge_' + e.id];
            if (orig) {{
                edges.update({{
                    id: e.id,
                    color: orig.color,
                    width: orig.width,
                    dashes: orig.dashes
                }});
            }}
        }});
    }};

    function focusOnNode(nodeId) {{
        focusedNode = nodeId;
        var connectedNodes = new Set(network.getConnectedNodes(nodeId));
        var connectedEdges = new Set(network.getConnectedEdges(nodeId));

        nodes.forEach(function(n) {{
            if (n.id === nodeId) {{
                nodes.update({{ id: n.id, borderWidth: 4, color: {{ border: '#2196F3' }} }});
            }} else if (!connectedNodes.has(n.id)) {{
                nodes.update({{
                    id: n.id,
                    color: {{ background: '#2a2a2a', border: '#444' }},
                    font: {{ color: '#555' }}
                }});
            }}
        }});

        edges.forEach(function(e) {{
            if (connectedEdges.has(e.id)) {{
                edges.update({{
                    id: e.id,
                    color: {{ color: '#2196F3', opacity: 1.0 }},
                    width: 2.5
                }});
            }} else {{
                edges.update({{
                    id: e.id,
                    color: {{ color: '#333', opacity: 0.15 }},
                    width: 0.5
                }});
            }}
        }});
    }}

    function openInVSCode(nodeId) {{
        if (currentView === 'function' && funcMeta[nodeId]) {{
            var meta = funcMeta[nodeId];
            var fullPath = sourceRoot + '/' + meta.file_path;
            window.open('vscode://file/' + fullPath + ':' + meta.line, '_blank');
        }} else {{
            var filePath = nodeId.replace(/\\./g, '/') + '.py';
            var fullPath = sourceRoot + '/' + filePath;
            window.open('vscode://file/' + fullPath, '_blank');
        }}
    }}

    network.on('click', function(params) {{
        if (params.event && params.event.srcEvent && params.event.srcEvent.ctrlKey && params.nodes.length > 0) {{
            openInVSCode(params.nodes[0]);
            return;
        }}
        if (params.nodes.length > 0) {{
            var clicked = params.nodes[0];
            if (focusedNode === clicked) {{
                resetView();
            }} else {{
                resetView();
                focusOnNode(clicked);
            }}
        }} else if (params.edges.length === 0) {{
            resetView();
        }}
    }});

    setTimeout(function() {{
        storeOriginalStyles();
    }}, 500);
    network.once('stabilized', storeOriginalStyles);
}})();
</script>
"""

    html = html.replace("</body>", control_panel + custom_js + "\n</body>")
    return html


def _build_graph_json(
    graph: nx.DiGraph,
    metrics: dict[str, NodeMetrics],
    edge_types: dict[tuple[str, str], bool] | None,
    cycle_edges: set[tuple[str, str]],
    positions: dict[str, tuple[float, float]],
    view_name: str,
    function_metadata: dict[str, dict] | None = None,
) -> dict:
    """Build a JSON-serializable graph representation for a single view."""
    max_ca = max((m.ca for m in metrics.values()), default=1) or 1
    nodes_json = []
    for node in graph.nodes():
        m = metrics.get(node)
        if not m:
            continue
        color_hex = default_color(m.impact)
        size = 10 + m.impact * 35

        label = truncate_label(node)
        if function_metadata and node in function_metadata:
            meta = function_metadata[node]
            display_name = f"{meta['file_path']}:{meta['label']}"
            label = truncate_label(display_name)
            tooltip = _build_tooltip(display_name, m, meta["file_path"])
        else:
            tooltip = _build_tooltip(node, m)

        node_data: dict = {
            "id": node,
            "label": label,
            "title": tooltip,
        }

        if node in positions:
            node_data["x"] = positions[node][0]
            node_data["y"] = positions[node][1]

        nodes_json.append(node_data)

    edges_json = []
    for src, dst in graph.edges():
        is_dynamic = edge_types.get((src, dst), False) if edge_types else False
        is_cycle_edge = (src, dst) in cycle_edges

        edge_color = "#e44444" if is_cycle_edge else "#555555"
        edge_width = 1.5 if is_cycle_edge else 1

        edges_json.append({
            "from": src,
            "to": dst,
            "color": edge_color,
            "width": edge_width,
            "dashes": is_dynamic,
            "title": "dynamic import" if is_dynamic else "",
        })

    return {"nodes": nodes_json, "edges": edges_json}


def generate_interactive_graph(
    package_graph: nx.DiGraph,
    file_graph: nx.DiGraph,
    function_graph: nx.DiGraph,
    file_edge_types: dict[tuple[str, str], bool],
    package_metrics: dict[str, NodeMetrics],
    file_metrics: dict[str, NodeMetrics],
    function_metrics: dict[str, NodeMetrics],
    file_cycle_nodes: set[str],
    file_cycle_edges: set[tuple[str, str]],
    package_cycle_nodes: set[str],
    package_cycle_edges: set[tuple[str, str]],
    function_cycle_nodes: set[str],
    function_cycle_edges: set[tuple[str, str]],
    function_metadata: dict[str, dict],
    source_root: Path,
    output_path: Path,
    open_browser: bool = True,
) -> None:
    """Generate an orbital interactive HTML visualization with 3 granularity levels."""

    package_positions = _compute_orbital_positions(package_graph, package_metrics)
    file_positions = _compute_orbital_positions(file_graph, file_metrics)
    function_positions = _compute_orbital_positions(function_graph, function_metrics)

    all_graphs = {
        "package": _build_graph_json(
            package_graph, package_metrics, None, package_cycle_edges,
            package_positions, "package",
        ),
        "file": _build_graph_json(
            file_graph, file_metrics, file_edge_types, file_cycle_edges,
            file_positions, "file",
        ),
        "function": _build_graph_json(
            function_graph, function_metrics, None, function_cycle_edges,
            function_positions, "function", function_metadata,
        ),
    }

    all_metrics_map = {
        "package": package_metrics,
        "file": file_metrics,
        "function": function_metrics,
    }

    all_cycles = {
        "package": (package_cycle_nodes, package_cycle_edges),
        "file": (file_cycle_nodes, file_cycle_edges),
        "function": (function_cycle_nodes, function_cycle_edges),
    }

    net = Network(
        height="100vh",
        width="100%",
        directed=True,
        bgcolor="#1a1a2e",
        font_color="#ddd",
        cdn_resources="in_line",
    )

    for node_data in all_graphs["file"]["nodes"]:
        m = file_metrics.get(node_data["id"])
        if not m:
            continue
        color_hex = default_color(m.impact)
        size = 10 + m.impact * 35

        node_opts = {
            "label": node_data["label"],
            "title": node_data["title"],
            "color": {
                "background": color_hex,
                "border": color_hex,
                "highlight": {"background": "#fff", "border": "#f80"},
                "hover": {"background": "#ffc", "border": "#f80"},
            },
            "size": size,
            "borderWidth": 1,
            "font": {"size": 11, "color": "#ddd", "face": "monospace"},
        }

        if node_data["id"] in file_positions:
            node_opts["x"] = file_positions[node_data["id"]][0]
            node_opts["y"] = file_positions[node_data["id"]][1]

        net.add_node(node_data["id"], **node_opts)

    for edge_data in all_graphs["file"]["edges"]:
        net.add_edge(
            edge_data["from"],
            edge_data["to"],
            color=edge_data["color"],
            width=edge_data["width"],
            dashes=edge_data["dashes"],
            arrows="to",
            title=edge_data["title"],
        )

    options = _get_orbital_vis_options()
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

    html = _inject_enhancements(
        html,
        all_graphs,
        all_metrics_map,
        all_cycles,
        function_metadata,
        str(source_root.absolute()),
    )
    output_path.write_text(html, encoding="utf-8")

    if open_browser:
        webbrowser.open(f"file://{output_path.absolute()}")
