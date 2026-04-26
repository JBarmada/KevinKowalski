# Kowalski-Kevin MCP Server

An MCP server that exposes architectural metrics (coupling, cohesion, complexity) to AI coding agents so they can advise on refactors *before* writing spaghetti.

Currently backed by a **fake analyzer** that returns a fixed snapshot of an imaginary 6-module web app — enough for the MCP plumbing to be exercised end-to-end while the real analyzer is under construction. Swap is one line in `mcp_server.py`.

## Tools

| Tool | Args | Returns |
|---|---|---|
| `analyze_repo` | `path: str = "."` | Markdown summary of the repo: counts, average instability, top offenders |
| `module_health` | `path, module` | Per-module card: Ca/Ce/I/LCOM/CC, violations, importers/importees |
| `suggest_refactor` | `path, feature_description` | Ranked decouplings to do **before** implementing the feature |
| `check_change` | `path, files: list[str]` | Before/after metric delta + green/yellow/red verdict |
| `get_metric_graph` | `path: str = "."` | JSON `{nodes, edges}` for the visualization |

All paths are normalized: `.` or empty → server CWD, otherwise absolutized.

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

Restart the host (fully quit Claude Desktop from the system tray; for Claude Code, `/mcp` should re-list). Ask the agent: *"List your MCP tools"* — you should see all 5 prefixed `kowalski-kevin__*`.

## Try it

```
Call analyze_repo with path "."
```

Then:

```
Use suggest_refactor to figure out what to clean up before adding audit logging.
```

## Tests

```bash
# all tests
pytest

# only the swap-safe slice (skips fake-analyzer-pinned tests)
pytest -m "not fake_only"
```

Test layout:

- **`test_contract.py`** — asserts SHAPE of analyzer output. Survives the swap.
- **`test_formatters.py`** — asserts SHAPE of formatter Markdown. Survives the swap.
- **`test_server.py`** — drives the 5 tools through FastMCP machinery. Survives the swap.
- **`test_fake_analyzer_only.py`** — pins specific behavior of the fake analyzer. **Will fail after the analyzer swap. Delete the file at that point — do not "fix" the assertions.** A loud banner prints whenever these are collected.

## Architecture

```
mcp_server.py        -- FastMCP wiring, 5 @mcp.tool() functions
  |
  +-- formatters.py  -- pure functions: GraphSnapshot -> Markdown
  |
  +-- get_analyzer() -- swap point
        |
        +-- fake_analyzer.py  (today)
        +-- real analyzer     (later, satisfies Analyzer protocol)
              |
              +-- contract.py  -- ModuleMetrics, GraphSnapshot, Analyzer
```

The MCP server depends only on `contract.py` for types and `get_analyzer()` for the implementation. To swap analyzers, change the import in `mcp_server.py` and the return value of `get_analyzer()` in the chosen module.

## Important constraints

- **Stdout is the JSON-RPC channel.** Never `print()` from this module or anything it imports at runtime — it corrupts the protocol. All logs go to stderr (configured in `mcp_server.py`).
- **Tools must return strings, not raise.** Every tool is wrapped with `@_safe_tool` which traps exceptions and returns a readable error string. A raised exception surfaces as an opaque protocol error on the agent side.
- **ASCII output.** Formatters avoid emojis and Unicode arrows so output renders on any console (Windows cp1252 included). Agents read the JSON-encoded UTF-8 fine either way, but local debugging stays painless.

## For the analyzer team

Implement the `Analyzer` protocol from `mcp/contract.py`:

```python
class Analyzer(Protocol):
    def analyze(self, repo_path: str) -> GraphSnapshot: ...
    def incremental_check(self, repo_path: str, files: list[str]) -> dict: ...
```

Then change two things in `mcp/mcp_server.py`:

```python
from your_module import get_analyzer  # was: fake_analyzer
```

…and delete `mcp/fake_analyzer.py` plus `mcp/tests/test_fake_analyzer_only.py`. The remaining 27 tests should still pass.

## Out of scope (for now)

- `app.py` and `index.html` — a separate FastAPI browser viewer, not on the MCP path.
- Real graph analysis (analyzer team).
- Visualization frontend (Phase 6).
