# MCP Server Setup

Step-by-step guide for running the KevinKowalski MCP server and wiring it into an AI coding agent (Claude Code or Claude Desktop).

## Prerequisites

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- Git
- Claude Code or Claude Desktop installed

## Step 1 — Clone the repository

```bash
git clone https://github.com/JBarmada/KevinKowalski.git
cd KevinKowalski
```

## Step 2 — Install dependencies

With uv (recommended):

```bash
uv sync
```

Or with pip:

```bash
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS / Linux:
source .venv/bin/activate

pip install -r mcp/requirements.txt
pip install networkx radon lizard
```

## Step 3 — Verify the install

Run the test suite — all 30 should pass:

```bash
uv run pytest mcp/tests
```

Expected output ends with `30 passed`.

## Step 4 — Find the absolute path to your Python interpreter

Claude needs an absolute path to the interpreter that owns the dependencies.

uv (project venv lives at `.venv/`):

```bash
# Windows
echo "%CD%\.venv\Scripts\python.exe"

# macOS / Linux
echo "$PWD/.venv/bin/python"
```

Save that path — you will paste it in Step 5.

## Step 5 — Wire the server into Claude

### Option A — Claude Code

Edit `~/.claude.json` (or `%USERPROFILE%\.claude.json` on Windows). Add this to the top-level `mcpServers` object (create the object if it doesn't exist):

```json
{
  "mcpServers": {
    "kowalski-kevin": {
      "command": "C:/absolute/path/to/KevinKowalski/.venv/Scripts/python.exe",
      "args": ["C:/absolute/path/to/KevinKowalski/mcp/mcp_server.py"]
    }
  }
}
```

Use forward slashes even on Windows. Both paths must be absolute.

### Option B — Claude Desktop

Edit the desktop config:

- Windows: `%APPDATA%\Claude\claude_desktop_config.json`
- macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`

Same JSON shape as above.

## Step 6 — Restart the host

- Claude Code: run `/mcp` in any session — `kowalski-kevin` should appear in the list with a green status.
- Claude Desktop: fully quit from the system tray (right-click → Quit). Relaunching does **not** reload MCP servers — you must quit first.

## Step 7 — Confirm the agent sees the tools

Ask the agent:

```
List your MCP tools.
```

You should see five tools prefixed `kowalski-kevin__`:

| Tool | What it does |
|---|---|
| `analyze_repo` | Full architectural overview |
| `module_health` | Per-module card |
| `suggest_refactor` | Pre-feature decoupling advice |
| `check_change` | Before/after delta after edits |
| `get_metric_graph` | Raw JSON nodes + edges |

## Example — using it inside a Claude agent

Once wired, you talk to the agent normally; it picks the right tool. Examples:

**Full repo overview**
```
Use the kowalski-kevin tool to analyze C:/Users/me/projects/myapp
```

**Targeted health check**
```
Run module_health on handlers.user in C:/Users/me/projects/myapp
```

**Pre-feature refactor advice**
```
I'm about to add audit logging to my Flask app at /home/me/myapp.
Use kowalski-kevin to tell me what to refactor first.
```

**Verify a change**
```
I just edited handlers/user.py and db/session.py. Use check_change
on /home/me/myapp to confirm the architecture didn't get worse.
```

The agent calls the tool, gets back Markdown, and explains it conversationally.

## Programmatic example — Anthropic SDK with MCP

If you want to call the MCP server from your own Python script using the Anthropic SDK's native MCP support:

```python
import anthropic

client = anthropic.Anthropic()

response = client.messages.create(
    model="claude-opus-4-7",
    max_tokens=4096,
    mcp_servers=[
        {
            "type": "stdio",
            "name": "kowalski-kevin",
            "command": "C:/absolute/path/to/KevinKowalski/.venv/Scripts/python.exe",
            "args": ["C:/absolute/path/to/KevinKowalski/mcp/mcp_server.py"],
        }
    ],
    messages=[
        {
            "role": "user",
            "content": "Analyze C:/path/to/some/python/repo and tell me the top 3 modules to refactor.",
        }
    ],
)

for block in response.content:
    if block.type == "text":
        print(block.text)
```

The SDK launches the server as a subprocess, surfaces its tools to the model, executes any tool calls the model emits, and feeds results back automatically.

## Troubleshooting

**"Server failed to start" / red status in `/mcp`**
- The `command` path must be absolute and point at the python.exe / python that has fastmcp installed. Run that interpreter manually with `--version` to confirm it works.
- The `args` path must point at `mcp/mcp_server.py`, not the `mcp/` directory.

**Tools list empty**
- Check Claude's MCP logs: Claude Code prints them to its developer console; Claude Desktop logs to `%APPDATA%\Claude\logs\mcp-server-kowalski-kevin.log` (Windows) or `~/Library/Logs/Claude/mcp-server-kowalski-kevin.log` (macOS).
- Common cause: `print()` somewhere in the import chain corrupts the JSON-RPC stream. The codebase deliberately routes all logs to stderr — don't add `print()` calls.

**Tool returns "path must be an absolute path"**
- The server rejects `.` and empty strings on purpose: when launched by Claude, its CWD is the host's launch directory (often `C:\WINDOWS\System32`), not your repo. Pass the absolute path you want analyzed.

**`ModuleNotFoundError: No module named 'radon'` (or `lizard`, `networkx`)**
- The `command` path points at the wrong interpreter. Either pin `command` to the project venv's python, or `uv pip install radon lizard networkx` into whatever interpreter you're using.

## What to do next

- Read [`mcp/README.md`](mcp/README.md) for the full tool reference.
- Read the top-level [`README.md`](README.md) for repository architecture.
- For the Fetch.ai / Agentverse front end (chat with KevinKowalski via ASI:One), see [`fetchagentSETUP.md`](fetchagentSETUP.md).
