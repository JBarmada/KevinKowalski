# Kowalski-Kevin MCP Server

An MCP server that exposes architectural metrics (coupling, cohesion, complexity) to AI coding agents so they can advise on refactors *before* writing spaghetti.

Backed by **RealAnalyzer** (`real_analyzer.py`) which performs actual AST-based static analysis of the target Python repo — computing Ca/Ce, instability, LCOM4, cyclomatic complexity, and violation detection.

## Tools

| Tool | Args | Returns |
|---|---|---|
| `analyze_repo` | `path: str` | Markdown summary of the repo: counts, average instability, top offenders |
| `module_health` | `path, module` | Per-module card: Ca/Ce/I/LCOM/CC, violations, importers/importees |
| `suggest_refactor` | `path, feature_description` | Ranked decouplings to do **before** implementing the feature |
| `check_change` | `path, files: list[str]` | Before/after metric delta + green/yellow/red verdict |
| `refactor_assistance` | `path: str` | Ca/Ce-focused refactor brief at package, file, and function levels |
| `generate_graph` | `path: str, output: str = ""` | Interactive HTML dependency graph with three views |

All paths must be absolute — `.` and empty strings are rejected because the MCP server's CWD is the host's launch directory, not the user's project.

## Setup

```bash
cd KevinKowalski
uv sync
```

Or with plain pip:

```bash
pip install fastmcp
```

## Wire into Claude Code

Edit `~/.claude.json` (Claude Code) or `%APPDATA%\Claude\claude_desktop_config.json` (Claude Desktop):

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

Restart the host (fully quit Claude Desktop from the system tray; for Claude Code, `/mcp` should re-list). Ask the agent: *"List your MCP tools"* — you should see all 6 prefixed `kowalski-kevin__*`.

## Try it

```
Call analyze_repo with path "/path/to/my/project"
```

Then:

```
Use suggest_refactor to figure out what to clean up before adding audit logging.
```

## Tests

```bash
# all tests
uv run pytest mcp/tests

# or with plain pytest
pytest mcp/tests
```

Test layout:

- **`test_contract.py`** — asserts SHAPE of analyzer output. Analyzer-agnostic.
- **`test_formatters.py`** — asserts SHAPE of formatter Markdown. Analyzer-agnostic.
- **`test_server.py`** — drives the 6 tools through FastMCP machinery. Analyzer-agnostic.

## Architecture

```
mcp_server.py        -- FastMCP wiring, 6 @mcp.tool() functions
  |
  +-- formatters.py  -- pure functions: GraphSnapshot -> Markdown
  |
  +-- real_analyzer.py  -- AST-based static analysis (satisfies Analyzer protocol)
        |
        +-- contract.py  -- ModuleMetrics, GraphSnapshot, Analyzer protocol
        |
        +-- metrics/graph.py  -- parse_edges_v2 for import resolution
```

The MCP server depends only on `contract.py` for types and `get_analyzer()` from `real_analyzer.py` for the implementation.

## Important constraints

- **Stdout is the JSON-RPC channel.** Never `print()` from this module or anything it imports at runtime — it corrupts the protocol. All logs go to stderr (configured in `mcp_server.py`).
- **Tools must return strings, not raise.** Every tool is wrapped with `@_safe_tool` which traps exceptions and returns a readable error string. A raised exception surfaces as an opaque protocol error on the agent side.
- **ASCII output.** Formatters avoid emojis and Unicode arrows so output renders on any console (Windows cp1252 included). Agents read the JSON-encoded UTF-8 fine either way, but local debugging stays painless.

## Out of scope (for now)

- `app.py` and `index.html` — a separate FastAPI browser viewer, not on the MCP path.
- Cross-language support — Python only.
