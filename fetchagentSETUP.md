# Fetch Kevin (Agentverse Agent) Setup

Step-by-step guide for running KevinKowalski as a Fetch.ai uAgent registered on Agentverse, reachable through ASI:One Chat.

For the user-facing description of what the agent does once it's running, see [`fetchagentREADME.md`](fetchagentREADME.md).

## Prerequisites

- Python 3.11 or newer
- [`uv`](https://docs.astral.sh/uv/) (recommended) or `pip`
- Git installed and on PATH (the agent shells out to `git clone` for GitHub URLs)
- An ASI:One account → API key from <https://asi1.ai/dashboard/api-keys>
- An Agentverse account → API key from <https://agentverse.ai/profile/api-keys>

## Step 1 — Clone and install

```bash
git clone https://github.com/JBarmada/KevinKowalski.git
cd KevinKowalski
uv sync
```

Or with pip / venv (see [`mcpserverSETUP.md`](mcpserverSETUP.md) for the equivalent recipe).

## Step 2 — Get your API keys

### ASI:One API key

1. Sign in at <https://asi1.ai>
2. Go to **Dashboard → API Keys**
3. Create a key — it will start with `sk_`
4. Copy it; you cannot view it again later

### Agentverse API key

1. Sign in at <https://agentverse.ai>
2. Profile menu → **API Keys**
3. Create a key with `av` scope — it will be a long JWT string
4. Copy it

## Step 3 — Set the environment variables

The agent reads two environment variables at startup. A third (`AGENT_SEED`) is optional but recommended once you have one you like.

### Linux / macOS (bash / zsh)

```bash
export ASI_ONE_API_KEY="sk_..."
export AGENTVERSE_API_KEY="eyJ..."
# Optional: pin the agent's address by reusing a seed
export AGENT_SEED="my-kowalski-agent-seed-phrase"
```

### Windows PowerShell

```powershell
$env:ASI_ONE_API_KEY = "sk_..."
$env:AGENTVERSE_API_KEY = "eyJ..."
$env:AGENT_SEED = "my-kowalski-agent-seed-phrase"
```

### Windows Command Prompt

```cmd
set ASI_ONE_API_KEY=sk_...
set AGENTVERSE_API_KEY=eyJ...
set AGENT_SEED=my-kowalski-agent-seed-phrase
```

> **Note** — the `seed` is just a string; the same seed always derives the same agent address and private key. If you want a stable address across restarts, pick one and keep it. Treat it like a password.

## Step 4 — Run the agent

```bash
uv run python mcp/agentverse_agent.py
```

You should see logs like:

```
INFO:kowalski-agent:KevinKowalski Agentverse agent starting
INFO:     [KevinKowalski]: Starting agent with address: agent1q...
INFO:     [KevinKowalski]: Agent inspector available at https://agentverse.ai/inspect/?...
INFO:     [KevinKowalski]: Starting server on http://0.0.0.0:8001 (Press CTRL+C to quit)
INFO:     [KevinKowalski]: Starting mailbox client for https://agentverse.ai
```

Copy the `agent1q...` address — that's how other agents and ASI:One reach it.

## Step 5 — Register on Agentverse

The first time you run with a new seed:

1. Open the **Agent inspector URL** printed in the logs.
2. On the inspector page, click **Connect** (or **Add Mailbox**) and authorize with your Agentverse account.
3. Agentverse will fund the agent on testnet automatically — no FET tokens needed.

The agent stays registered as long as you keep `AGENT_SEED` the same.

## Step 6 — Talk to it through ASI:One

1. Open <https://asi1.ai/chat>
2. In the chat input, address your agent by its `agent1q...` address (ASI:One has a UI for picking agents — search by address or by the `KevinKowalski` name once it's published).
3. Send a message:

```
Analyze https://github.com/pallets/flask
```

The agent will:
- Clone the repo into a temp dir named like `kowalski_pallets_flask_<random>/`
- Run the architectural analysis
- Reply with a short summary, then a `---` separator, then the full Markdown report
- Clean up the temp dir

Other example prompts (see [`fetchagentREADME.md`](fetchagentREADME.md) for more):

```
Check health of flask.app in https://github.com/pallets/flask
```

```
I want to add OAuth to https://github.com/myorg/myapp — what should I refactor first?
```

```
Hi, what can you do?
```

## Step 7 — Keep it running

The agent is a long-running process. For local development just leave the terminal open. To keep it up after you log out:

### Linux (systemd)

```ini
# /etc/systemd/system/kevin-kowalski.service
[Unit]
Description=KevinKowalski Fetch.ai agent
After=network.target

[Service]
Type=simple
WorkingDirectory=/home/me/KevinKowalski
ExecStart=/home/me/KevinKowalski/.venv/bin/python mcp/agentverse_agent.py
Environment="ASI_ONE_API_KEY=sk_..."
Environment="AGENTVERSE_API_KEY=eyJ..."
Environment="AGENT_SEED=my-kowalski-agent-seed-phrase"
Restart=on-failure

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now kevin-kowalski
journalctl -u kevin-kowalski -f
```

### Windows

Use Task Scheduler, or run inside a `pm2` / `nssm` service. The simplest path is `nssm install KevinKowalski` pointing at the venv's `python.exe` with `mcp/agentverse_agent.py` as the argument.

## Configuration knobs

All in [`mcp/agentverse_agent.py`](mcp/agentverse_agent.py) near the top:

| Constant | Default | What it does |
|---|---|---|
| `_HISTORY_LIMIT` | 10 | Max stored chat turns per sender (caps prompt size) |
| `_ROUTER_MODEL` | `asi1` | Cheap classifier — picks which tool to call |
| `_SUMMARIZER_MODEL` | `asi1-ultra` | Bigger model that writes the natural-language reply |
| `port` (in `Agent(...)`) | 8001 | Local HTTP port — change if 8001 is taken |
| `mailbox` | `True` | Use Agentverse mailbox transport (no inbound public IP needed) |
| `network` | `"testnet"` | Almanac registration network (auto-funded) |

## Troubleshooting

**`ASI_ONE_API_KEY not set — LLM routing will fail`**
- Env var didn't make it into the process. In PowerShell, `$env:ASI_ONE_API_KEY` only sets it for the current session — re-export before relaunching.

**`error while attempting to bind on address ('0.0.0.0', 8001)`**
- Port 8001 is in use. Either kill the prior agent (`netstat -ano | grep :8001` → kill the PID) or change `port=8001` in `agentverse_agent.py`.

**Agent appears in inspector but never replies in ASI:One**
- Check the mailbox connection log line. If it loops on `Connecting...` your `AGENTVERSE_API_KEY` is invalid or expired.

**Agent replies "Failed to clone <url>"**
- `git` isn't on PATH inside the agent's environment, or the URL is private. The agent uses `git clone --depth 1`; private repos need credentials in your global git config.

**Tool reply is a giant wall of text instead of summary + report**
- Summarizer model failed and the agent fell back to the raw output (intentional — better the data than nothing). Check `ctx.logger` output for the underlying error; usually a transient ASI:One 5xx.

**Want to test the analyzer without going through ASI:One**
- Call `_run_tool` directly:
  ```python
  import sys; sys.path.insert(0, "mcp")
  from agentverse_agent import _run_tool
  print(_run_tool("analyze_repo", "/abs/path/to/some/repo", ""))
  ```

## What to do next

- Read the agent prompt in `agentverse_agent.py` (`TOOL_DESCRIPTIONS`, `_CHAT_SYSTEM`, `_SUMMARIZER_SYSTEM`) — those are the three places that shape personality and routing.
- The MCP server (same tools, different transport) is set up in [`mcpserverSETUP.md`](mcpserverSETUP.md).
- For the architectural overview of the whole repo, see the top-level [`README.md`](README.md).
