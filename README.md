# KevinKowalski

Architectural-metrics tooling for Python repositories. Computes coupling (Ca/Ce/instability), cohesion (LCOM4), and cyclomatic complexity, flags rule violations (SDP, GOD_MODULE, HIGH_CC, CYCLE), and serves the results through three frontends:

- An **MCP server** that AI coding agents (Claude Code, Claude Desktop, Cursor, etc.) call before refactors.
- A **Fetch.ai uAgent** registered on Agentverse so users can chat with the analyzer through ASI:One.
- A **standalone CLI / library** (the `metrics/` package) for direct use.

For the agent-flavored, user-facing description of what KevinKowalski does as a chat assistant, see [`fetchagentREADME.md`](fetchagentREADME.md).

## Repository layout

```
.
├── metrics/           Analysis library — AST walkers, networkx graph builders
│   ├── graph.py       parse_edges_v2: import-edge extraction
│   ├── metrics.py     compute_metrics: Ca/Ce/instability from a DiGraph
│   └── function_dependency_graph.py   Function-call graph (uses jedi)
│
├── mcp/               MCP server + Agentverse agent + analyzer adapter
│   ├── contract.py            Analyzer protocol, ModuleMetrics, GraphSnapshot
│   ├── real_analyzer.py       Adapter that satisfies the protocol against metrics/
│   ├── formatters.py          GraphSnapshot -> Markdown for agent replies
│   ├── mcp_server.py          FastMCP server, 5 tools
│   ├── agentverse_agent.py    uAgent / ASI:One Chat Protocol entrypoint
│   ├── app.py + index.html    Optional FastAPI browser viewer
│   └── tests/                 30 contract/formatter/server tests
│
├── visualization/     Graph rendering helpers (matplotlib, pyvis)
├── pyproject.toml     uv-managed deps; targets Python >=3.11
└── uv.lock
```

## Quick start

```bash
git clone https://github.com/JBarmada/KevinKowalski.git
cd KevinKowalski
uv sync
```

### Run the MCP server

```bash
uv run python mcp/mcp_server.py
```

Wire it into Claude Code / Claude Desktop by adding to `~/.claude.json` or `%APPDATA%\Claude\claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "kowalski-kevin": {
      "command": "C:/absolute/path/to/python.exe",
      "args": ["C:/absolute/path/to/KevinKowalski/mcp/mcp_server.py"]
    }
  }
}
```

See [`mcp/README.md`](mcp/README.md) for the full tool reference.

### Run the Agentverse agent

```bash
export ASI_ONE_API_KEY=sk_...
export AGENTVERSE_API_KEY=eyJ...
uv run python mcp/agentverse_agent.py
```

The agent registers on Agentverse mailbox + Almanac (testnet, auto-funded) and accepts GitHub URLs / absolute paths through the Chat Protocol. Cloned repos land in `kowalski_<owner>_<repo>_<random>/` under the system temp dir.

### Use the metrics library directly

```python
from pathlib import Path
from metrics.graph import parse_edges_v2

edges = parse_edges_v2(Path("path/to/some/python/repo"))
```

Or render a static PNG of the import graph:

```bash
uv run python metrics/graph.py path/to/some/python/repo
# writes output/<repo>_imports.png
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Frontends                                                  │
│  ┌──────────────────┐   ┌────────────────────────────────┐  │
│  │ mcp_server.py    │   │ agentverse_agent.py            │  │
│  │ FastMCP / stdio  │   │ uAgent / ASI:One Chat          │  │
│  └────────┬─────────┘   └────────────┬───────────────────┘  │
│           │                          │                      │
│           └──────────┬───────────────┘                      │
│                      ▼                                      │
│           ┌──────────────────────┐                          │
│           │ formatters.py        │  GraphSnapshot -> MD     │
│           └──────────┬───────────┘                          │
│                      ▼                                      │
│           ┌──────────────────────┐                          │
│           │ contract.Analyzer    │  protocol surface        │
│           └──────────┬───────────┘                          │
│                      ▼                                      │
│           ┌──────────────────────┐                          │
│           │ real_analyzer.py     │  adapter                 │
│           └──────────┬───────────┘                          │
│                      ▼                                      │
│           ┌──────────────────────┐                          │
│           │ metrics/             │  AST + networkx + radon  │
│           └──────────────────────┘                          │
└─────────────────────────────────────────────────────────────┘
```

The frontends depend only on `contract.py` for types and `get_analyzer()` for the implementation. Swapping in a different analyzer (a remote service, a faster cached one, etc.) is a one-line import change in both `mcp_server.py` and `agentverse_agent.py`.

`real_analyzer.py` reuses the metrics team's `parse_edges_v2` (relative imports), adds an `_absolute_edges` walker for `import x` / `from x import y`, layers radon for `cc_max`, and an AST class-body walk for `lcom4`. Violations (SDP, GOD_MODULE, HIGH_CC, CYCLE) are derived from those metrics plus the dependency graph.

## Tests

```bash
uv run pytest mcp/tests
```

30 tests across three files:

- `test_contract.py` — asserts the SHAPE of analyzer output. Any analyzer satisfying the protocol must pass.
- `test_formatters.py` — Markdown rendering of GraphSnapshot.
- `test_server.py` — drives all 5 MCP tools through FastMCP.

## Constraints worth knowing

- **MCP stdio is the JSON-RPC channel.** Never `print()` from `mcp_server.py` or its imports at runtime — it corrupts the protocol. All logs go to stderr.
- **Tool functions return strings, not raise.** `@_safe_tool` traps exceptions and returns a readable error so the agent can recover.
- **Edges only point to known modules.** `test_contract.test_edges_reference_known_modules` enforces this; `real_analyzer` strips edges to stdlib / third-party.
- **`metrics/graph.py` is importable as a library.** Its CLI block lives under `_main()` / `if __name__ == "__main__":` so importing `parse_edges_v2` does not trigger argparse.

## Dependencies

Managed via `uv` (`pyproject.toml`). Notable runtime deps: `fastmcp`, `uagents`, `openai` (ASI:One client), `networkx`, `radon`, `lizard`, `matplotlib`, `pyvis`, `pydot`. Dev: `pytest`.
