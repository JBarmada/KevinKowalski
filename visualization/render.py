"""Interactive HTML visualization using an orbital dependency graph model."""

import json
import math
import webbrowser
from pathlib import Path

import matplotlib.colors as mcolors
import networkx as nx
from pyvis.network import Network

from visualization.utils import NodeMetrics, truncate_label, format_multiline_label


def holistic_color(impact: float) -> str:
    """Map impact [0,1] — bright green (low) to bright saturated brown/amber (high).

    Both ends are vivid/saturated for contrast on dark backgrounds.
    """
    green = (0.30, 0.90, 0.30)  # bright vivid green
    brown = (0.85, 0.45, 0.10)  # bright saturated amber-brown
    r = green[0] + (brown[0] - green[0]) * impact
    g = green[1] + (brown[1] - green[1]) * impact
    b = green[2] + (brown[2] - green[2]) * impact
    return mcolors.to_hex((r, g, b))


def susceptibility_color(value: float) -> str:
    """Map susceptibility [0,1] — white (low) to saturated blue (high)."""
    low = (1.0, 1.0, 1.0)     # white
    high = (0.0, 0.45, 1.0)   # saturated bright blue
    r = low[0] + (high[0] - low[0]) * value
    g = low[1] + (high[1] - low[1]) * value
    b = low[2] + (high[2] - low[2]) * value
    return mcolors.to_hex((r, g, b))


def impact_toggle_color(value: float) -> str:
    """Map impact [0,1] — white (low) to saturated orange (high)."""
    low = (1.0, 1.0, 1.0)     # white
    high = (1.0, 0.45, 0.0)   # saturated bright orange
    r = low[0] + (high[0] - low[0]) * value
    g = low[1] + (high[1] - low[1]) * value
    b = low[2] + (high[2] - low[2]) * value
    return mcolors.to_hex((r, g, b))


def _build_tooltip(
    node: str,
    metrics: NodeMetrics,
    file_path: str | None = None,
    raw_impact: float | None = None,
    raw_susceptibility: float | None = None,
) -> str:
    """Build plain-text tooltip content for a node with denormalized metrics."""
    impact_val = raw_impact if raw_impact is not None else metrics.impact
    suscept_val = raw_susceptibility if raw_susceptibility is not None else metrics.susceptibility
    lines = [
        node,
        "─" * min(len(node), 30),
        f"Impact: {impact_val:.1f}",
        f"Susceptibility: {suscept_val:.1f}",
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
    ring_radius_step = 400

    node_idx = 0
    for ring in range(num_rings):
        if node_idx >= n:
            break

        radius = (ring + 1) * ring_radius_step if ring > 0 else 0

        if ring == 0:
            nodes_in_ring = min(1, n - node_idx)
        else:
            circumference = 2 * math.pi * radius
            max_per_ring = max(1, int(circumference / 120))
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
            "color": {"color": "#dddddd", "opacity": 0.85},
        },
        "layout": {"hierarchical": {"enabled": False}},
        "physics": {
            "enabled": True,
            "barnesHut": {
                "gravitationalConstant": -5000,
                "centralGravity": 0.05,
                "springLength": 250,
                "springConstant": 0.01,
                "damping": 0.3,
                "avoidOverlap": 0.6,
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
                node: {
                    "impact": m.impact,
                    "susceptibility": m.susceptibility,
                    "instability": m.instability,
                    "ca": m.ca,
                    "ce": m.ce,
                    "raw_impact": m.raw_impact,
                    "raw_susceptibility": m.raw_susceptibility,
                }
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
    background: rgba(20, 20, 30, 0.92);
    border: 1px solid #444;
    border-radius: 10px;
    padding: 16px 18px;
    box-shadow: 0 4px 24px rgba(0,0,0,0.5);
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
    <button id="btn-default-color" onclick="setColorMode('default')" style="padding: 6px 10px; background: linear-gradient(to right, #4de64d, #d9731a); border: 1px solid #555; color: #fff; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      Holistic
    </button>
    <button id="btn-susceptibility" onclick="setColorMode('susceptibility')" style="padding: 6px 10px; background: linear-gradient(to right, #ffffff, #0073ff); border: 1px solid #555; color: #333; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      Susceptibility
    </button>
    <button id="btn-impact" onclick="setColorMode('impact')" style="padding: 6px 10px; background: linear-gradient(to right, #ffffff, #ff7300); border: 1px solid #555; color: #333; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      Impact
    </button>
    <button id="btn-cycles" onclick="setColorMode('cycles')" style="padding: 6px 10px; background: #3a1a1a; border: 1px solid #e44; color: #f88; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      Cycles
    </button>
  </div>

  <div style="display: flex; flex-direction: column; gap: 5px; margin-bottom: 14px;">
    <button onclick="resetView()" style="padding: 6px 10px; background: #333; border: 1px solid #555; color: #ccc; border-radius: 4px; cursor: pointer; font-family: inherit; font-size: 12px; text-align: left;">
      ↺ Reset View
    </button>
  </div>

  <div style="font-size: 10px; color: #888; border-top: 1px solid #444; padding-top: 10px;">
    <div style="font-weight: 600; margin-bottom: 6px; color: #999;">Legend</div>
    <div style="display: flex; align-items: center; gap: 6px; margin: 4px 0;">
      <span style="display: inline-block; width: 20px; height: 2px; background: #999;"></span>
      <span>Static import</span>
    </div>
    <div style="display: flex; align-items: center; gap: 6px; margin: 4px 0;">
      <span style="display: inline-block; width: 20px; border-top: 2px dashed #999;"></span>
      <span>Dynamic import</span>
    </div>
    <div id="size-legend-note" style="margin-top: 6px; font-style: italic;">Node size = Susceptibility</div>
    <div id="holistic-guide" style="margin-top: 8px; line-height: 1.5; color: #aaa;">
      <span style="color: #d9731a;">&#9679;</span> Brown (central) = trunk — avoid editing<br>
      <span style="color: #4de64d;">&#9679;</span> Green (peripheral) = leaves — safe to edit
    </div>
  </div>
  <div style="font-size: 10px; color: #888; border-top: 1px solid #444; padding-top: 10px; margin-top: 8px;">
    <div style="font-weight: 600; margin-bottom: 6px; color: #999;">Controls</div>
    <div style="margin: 3px 0;">Click node → highlight neighbors</div>
    <div style="margin: 3px 0;">Ctrl+Click → open in VS Code</div>
    <div style="margin: 3px 0;">Hover → show full name &amp; metrics</div>
  </div>
</div>
"""

    nav_button_css = """
<style>
/* Override vis-network navigation button fill while preserving existing border colors */
div.vis-network div.vis-navigation div.vis-button,
div.vis-network div.vis-navigation div.vis-button:hover,
div.vis-network div.vis-navigation div.vis-button:focus,
div.vis-network div.vis-navigation div.vis-button:active {
    background-color: #ffffff !important;
}
</style>
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
    var LARGE_GRAPH_THRESHOLD = 300;
    var isLargeGraph = false;

    function lerp(a, b, t) {{
        return a + (b - a) * t;
    }}

    function holisticColor(impact) {{
        var green = [0.30, 0.90, 0.30];
        var brown = [0.85, 0.45, 0.10];
        var r = lerp(green[0], brown[0], impact);
        var g = lerp(green[1], brown[1], impact);
        var b = lerp(green[2], brown[2], impact);
        return 'rgb(' + Math.round(r*255) + ',' + Math.round(g*255) + ',' + Math.round(b*255) + ')';
    }}

    function susceptibilityColor(val) {{
        var low = [1.0, 1.0, 1.0];
        var high = [0.0, 0.45, 1.0];
        var r = lerp(low[0], high[0], val);
        var g = lerp(low[1], high[1], val);
        var b = lerp(low[2], high[2], val);
        return 'rgb(' + Math.round(r*255) + ',' + Math.round(g*255) + ',' + Math.round(b*255) + ')';
    }}

    function impactColor(val) {{
        var low = [1.0, 1.0, 1.0];
        var high = [1.0, 0.45, 0.0];
        var r = lerp(low[0], high[0], val);
        var g = lerp(low[1], high[1], val);
        var b = lerp(low[2], high[2], val);
        return 'rgb(' + Math.round(r*255) + ',' + Math.round(g*255) + ',' + Math.round(b*255) + ')';
    }}

    function getNodeColor(nodeId, mode) {{
        var m = allMetrics[currentView] && allMetrics[currentView][nodeId];
        if (!m) return '#888';
        if (mode === 'default') return holisticColor(m.impact);
        if (mode === 'susceptibility') return susceptibilityColor(m.susceptibility);
        if (mode === 'impact') return impactColor(m.impact);
        return holisticColor(m.impact);
    }}

    function getNodeSize(nodeId) {{
        var m = allMetrics[currentView] && allMetrics[currentView][nodeId];
        if (!m) return 15;
        if (currentColorMode === 'default') {{
            return 10 + m.susceptibility * 35;
        }}
        return 10 + (1 - m.instability) * 35;
    }}

    function loadView(viewName) {{
        var graphData = allGraphs[viewName];
        if (!graphData) return;

        var nodeCount = graphData.nodes.length;
        isLargeGraph = nodeCount > LARGE_GRAPH_THRESHOLD;

        if (isLargeGraph) {{
            network.setOptions({{
                physics: {{ enabled: false }},
                edges: {{ smooth: false }},
                interaction: {{ hideEdgesOnDrag: true, tooltipDelay: 300 }}
            }});
        }} else {{
            network.setOptions({{
                physics: {{
                    enabled: true,
                    barnesHut: {{
                        gravitationalConstant: -5000,
                        centralGravity: 0.05,
                        springLength: 250,
                        springConstant: 0.01,
                        damping: 0.3,
                        avoidOverlap: 0.6
                    }},
                    solver: 'barnesHut',
                    stabilization: {{ iterations: 200, fit: true }}
                }},
                edges: {{ smooth: {{ type: 'continuous', roundness: 0.2 }} }},
                interaction: {{ hideEdgesOnDrag: false, tooltipDelay: 100 }}
            }});
        }}

        var newNodes = [];
        var newEdges = [];

        graphData.nodes.forEach(function(n) {{
            var color = getNodeColor(n.id, currentColorMode);
            var size = getNodeSize(n.id);
            var nodeObj = {{
                id: n.id,
                label: n.label,
                title: n.title,
                color: {{ background: color, border: color, highlight: {{ background: color, border: '#FFD700' }}, hover: {{ background: color, border: '#FFD700' }} }},
                size: size,
                borderWidth: 1,
                font: {{ size: viewName === 'package' ? 9 : 11, color: '#ddd', face: 'monospace', multi: true, align: 'center' }}
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
                color: {{ color: e.color || '#dddddd', opacity: 0.85 }},
                width: e.width || 1.5,
                dashes: e.dashes || false,
                arrows: 'to',
                title: e.title || ''
            }});
        }});

        nodes.clear();
        edges.clear();
        nodes.add(newNodes);
        edges.add(newEdges);

        if (isLargeGraph) {{
            setTimeout(function() {{ network.fit(); }}, 100);
        }} else {{
            network.once('stabilizationIterationsDone', function() {{
                network.setOptions({{ physics: {{ enabled: false }} }});
            }});
            setTimeout(function() {{ network.fit(); }}, 500);
        }}
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

            var nodeUpdates = [];
            nodes.forEach(function(n) {{
                if (cycleNodeSet.has(n.id)) {{
                    nodeUpdates.push({{
                        id: n.id,
                        color: {{ background: '#ff4444', border: '#ff0000', highlight: {{ background: '#ff4444', border: '#FFD700' }}, hover: {{ background: '#ff6666', border: '#ff0000' }} }},
                        borderWidth: 3,
                        font: {{ color: '#ddd' }}
                    }});
                }} else {{
                    nodeUpdates.push({{
                        id: n.id,
                        color: {{ background: '#333', border: '#444' }},
                        font: {{ color: '#555' }},
                        borderWidth: 1
                    }});
                }}
            }});
            nodes.update(nodeUpdates);

            var edgeUpdates = [];
            edges.forEach(function(e) {{
                var edgeKey = e.from + '>>>' + e.to;
                if (cycleEdgeSet.has(edgeKey)) {{
                    edgeUpdates.push({{
                        id: e.id,
                        color: {{ color: '#ff4444', opacity: 1.0 }},
                        width: 2.5
                    }});
                }} else {{
                    edgeUpdates.push({{
                        id: e.id,
                        color: {{ color: '#333', opacity: 0.15 }},
                        width: 0.5
                    }});
                }}
            }});
            edges.update(edgeUpdates);
            return;
        }}

        var nodeUpdates = [];
        nodes.forEach(function(n) {{
            var color = getNodeColor(n.id, mode);
            var size = getNodeSize(n.id);
            nodeUpdates.push({{
                id: n.id,
                color: {{ background: color, border: color, highlight: {{ background: color, border: '#FFD700' }}, hover: {{ background: color, border: '#FFD700' }} }},
                size: size,
                borderWidth: 1,
                font: {{ color: '#ddd' }}
            }});
        }});
        nodes.update(nodeUpdates);

        var edgeUpdates = [];
        edges.forEach(function(e) {{
            edgeUpdates.push({{
                id: e.id,
                color: {{ color: '#dddddd', opacity: 0.85 }},
                width: 1.5
            }});
        }});
        edges.update(edgeUpdates);

        var sizeNote = document.getElementById('size-legend-note');
        if (sizeNote) {{
            sizeNote.textContent = mode === 'default' ? 'Node size = Susceptibility' : 'Node size = Stability';
        }}
        var holisticGuide = document.getElementById('holistic-guide');
        if (holisticGuide) {{
            holisticGuide.style.display = mode === 'default' ? 'block' : 'none';
        }}
    }};

    window.resetView = function() {{
        focusedNode = null;
        currentColorMode = 'default';
        network.unselectAll();
        setColorMode('default');
        network.fit();
    }};

    function clearFocus() {{
        // Clear focus without changing color mode or refitting
        focusedNode = null;
        network.unselectAll();
        setColorMode(currentColorMode);
    }}

    function focusOnNode(nodeId) {{
        focusedNode = nodeId;
        var connectedNodes = new Set(network.getConnectedNodes(nodeId));
        var connectedEdges = new Set(network.getConnectedEdges(nodeId));

        var nodeUpdates = [];
        nodes.forEach(function(n) {{
            var nodeColor = getNodeColor(n.id, currentColorMode);
            if (n.id === nodeId) {{
                nodeUpdates.push({{
                    id: n.id,
                    borderWidth: 4,
                    color: {{ background: nodeColor, border: '#FFD700', highlight: {{ background: nodeColor, border: '#FFD700' }}, hover: {{ background: nodeColor, border: '#FFD700' }} }},
                    font: {{ color: '#ddd' }}
                }});
            }} else if (connectedNodes.has(n.id)) {{
                nodeUpdates.push({{
                    id: n.id,
                    color: {{ background: nodeColor, border: nodeColor, highlight: {{ background: nodeColor, border: '#FFD700' }}, hover: {{ background: nodeColor, border: '#FFD700' }} }},
                    font: {{ color: '#ddd' }},
                    borderWidth: 1
                }});
            }} else {{
                nodeUpdates.push({{
                    id: n.id,
                    color: {{ background: '#2a2a2a', border: '#333' }},
                    font: {{ color: '#444' }},
                    borderWidth: 1
                }});
            }}
        }});
        nodes.update(nodeUpdates);

        var edgeUpdates = [];
        edges.forEach(function(e) {{
            if (connectedEdges.has(e.id)) {{
                edgeUpdates.push({{
                    id: e.id,
                    color: {{ color: '#FFD700', opacity: 1.0 }},
                    width: 3
                }});
            }} else {{
                edgeUpdates.push({{
                    id: e.id,
                    color: {{ color: '#222', opacity: 0.1 }},
                    width: 0.5
                }});
            }}
        }});
        edges.update(edgeUpdates);
    }}

    function openInVSCode(nodeId) {{
        var uri;
        if (currentView === 'function' && funcMeta[nodeId]) {{
            var meta = funcMeta[nodeId];
            var fullPath = sourceRoot + '/' + meta.file_path;
            uri = 'vscode://file/' + fullPath + ':' + meta.line;
        }} else {{
            var filePath = nodeId.replace(/\\./g, '/') + '.py';
            var fullPath = sourceRoot + '/' + filePath;
            uri = 'vscode://file/' + fullPath;
        }}
        window.location.href = uri;
    }}

    network.on('click', function(params) {{
        var srcEvent = params.event && (params.event.srcEvent || params.event);
        var isCtrl = srcEvent && (srcEvent.ctrlKey || srcEvent.metaKey);
        if (isCtrl && params.nodes.length > 0) {{
            openInVSCode(params.nodes[0]);
            return;
        }}
        if (params.nodes.length > 0) {{
            var clicked = params.nodes[0];
            if (focusedNode === clicked) {{
                clearFocus();
            }} else {{
                focusOnNode(clicked);
            }}
        }} else if (params.edges.length === 0) {{
            clearFocus();
        }}
    }});

    network.once('stabilized', function() {{
        network.setOptions({{ physics: {{ enabled: false }} }});
        network.fit();
    }});
}})();
</script>
"""

    html = html.replace("</body>", nav_button_css + control_panel + custom_js + "\n</body>")
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
        color_hex = holistic_color(m.impact)
        size = 10 + m.impact * 35

        label = format_multiline_label(node)
        if function_metadata and node in function_metadata:
            meta = function_metadata[node]
            display_name = f"{meta['label']}()\n{meta['file_path']}"
            label = display_name
            tooltip = _build_tooltip(
                f"{meta['file_path']}:{meta['label']}", m, meta["file_path"],
                raw_impact=m.raw_impact, raw_susceptibility=m.raw_susceptibility
            )
        else:
            tooltip = _build_tooltip(
                node, m,
                raw_impact=m.raw_impact, raw_susceptibility=m.raw_susceptibility
            )

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

        edge_color = "#ff4444" if is_cycle_edge else "#dddddd"
        edge_width = 2 if is_cycle_edge else 1.5

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
        color_hex = holistic_color(m.impact)
        size = 10 + m.susceptibility * 35

        node_opts = {
            "label": node_data["label"],
            "title": node_data["title"],
            "color": {
                "background": color_hex,
                "border": color_hex,
                "highlight": {"background": color_hex, "border": "#FFD700"},
                "hover": {"background": color_hex, "border": "#FFD700"},
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
