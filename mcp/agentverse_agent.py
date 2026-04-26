"""KevinKowalski Agentverse agent.

uAgent bridge that exposes the architectural-analysis tools to ASI:One Chat.

Architecture:
  ChatMessage --> _route()      [router LLM picks tool / chat / help]
              \\-> _run_tool()    [analyzer.analyze + formatters.format_*]
              \\-> _summarize()   [summarizer LLM writes natural-language reply]
              --> ChatMessage    [summary + horizontal rule + raw markdown]

Conversation history is persisted per-sender via ctx.storage and capped at
_HISTORY_LIMIT turns so prompts stay bounded.

Required env vars:
  ASI_ONE_API_KEY      -- https://asi1.ai/dashboard/api-keys (sk_*)
  AGENTVERSE_API_KEY   -- https://agentverse.ai (JWT, av scope)

Optional env vars:
  AGENT_SEED           -- string used to derive the agent's address (default:
                          "kevin-kowalski-arch-agent-seed-phrase-2026")
  AGENT_NAME           -- registered name on Agentverse (default "KevinKowalski")
  AGENT_NETWORK        -- "testnet" (default, auto-funded) or "mainnet"
  AGENT_PORT           -- local HTTP port (default 8001)
  AGENT_ENDPOINT       -- public endpoint URL when not using mailbox
  AGENT_MAILBOX        -- "true" (default) | "false"
  AGENT_PATCH_MAILBOX_BEARER -- "true" to apply the bearer-token mailbox patch
                          for uagents < 0.24. Default off; uagents >= 0.24
                          already sends the right header.

External binaries:
  git -- shelled out for GitHub clones.
"""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
from datetime import datetime
from pathlib import Path
from uuid import uuid4

import aiohttp
import networkx as nx
from dotenv import load_dotenv
from openai import OpenAI
from pydantic import UUID4
from uagents import Agent, Context, Protocol
from uagents.mailbox import StoredEnvelope
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

from real_analyzer import get_analyzer
from formatters import (
    format_analyze_repo,
    format_check_change,
    format_generate_graph,
    format_module_health,
    format_refactor_assistance,
    format_suggest_refactor,
    viz_html_path_from_generate_stdout,
)

load_dotenv()  # so a local .env file works without pre-exporting

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("kowalski-agent")


# ---------------------------------------------------------------------------
# Configuration helpers
# ---------------------------------------------------------------------------

def _require_env(name: str) -> str:
    """Read an env var, raise with a clear message if it's missing or empty."""
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(
            f"Missing required environment variable: {name}. "
            "See the module docstring for the full list."
        )
    return value


def _bool_env(name: str, default: bool) -> bool:
    """Parse a boolean env var. Truthy: 1/true/yes/on (case-insensitive)."""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


# ---------------------------------------------------------------------------
# ASI:One client
# ---------------------------------------------------------------------------

ASI_API_KEY = os.environ.get("ASI_ONE_API_KEY", "")
if not ASI_API_KEY:
    log.warning("ASI_ONE_API_KEY not set -- LLM routing will fail at first message")

client = OpenAI(
    base_url="https://api.asi1.ai/v1",
    api_key=ASI_API_KEY,
)

# Module-level analyzer instance. Reused across all messages -- the contract
# allows internal caching, so creating one is cheap relative to per-call.
analyzer = get_analyzer()


# ---------------------------------------------------------------------------
# Conversation memory
# Persisted via ctx.storage (uagents KeyValueStore on disk). Verified API:
# get/has/set/remove/clear; values are JSON-serialized. Capped per sender to
# bound prompt size and cost.
# ---------------------------------------------------------------------------

_HISTORY_LIMIT = 10  # combined user + assistant turns
_ROUTER_MODEL = "asi1"
_SUMMARIZER_MODEL = "asi1-ultra"


def _history_key(sender: str) -> str:
    return f"history:{sender}"


def _load_history(ctx: "Context", sender: str) -> list[dict]:
    raw = ctx.storage.get(_history_key(sender))
    return raw if isinstance(raw, list) else []


def _save_history(ctx: "Context", sender: str, history: list[dict]) -> None:
    ctx.storage.set(_history_key(sender), history[-_HISTORY_LIMIT:])


# ---------------------------------------------------------------------------
# Agent construction
# ---------------------------------------------------------------------------

def _build_agent() -> Agent:
    """Build the uAgent from environment variables.

    Defaults are tuned for the LA Hacks demo: testnet, mailbox-driven,
    port 8001, with a stable seed so the address survives restarts.
    """
    seed = os.getenv("AGENT_SEED", "kevin-kowalski-arch-agent-seed-phrase-2026")
    name = os.getenv("AGENT_NAME", "KevinKowalski")
    network = os.getenv("AGENT_NETWORK", "testnet")
    port = int(os.getenv("AGENT_PORT", "8001"))
    endpoint = os.getenv("AGENT_ENDPOINT", "").strip()
    use_mailbox = _bool_env("AGENT_MAILBOX", True)

    kwargs: dict = {
        "name": name,
        "seed": seed,
        "port": port,
        "network": network,             # testnet Almanac is auto-funded
        "publish_agent_details": True,  # surface name + tags on Agentverse
    }
    if use_mailbox:
        kwargs["mailbox"] = True
    if endpoint:
        kwargs["endpoint"] = [endpoint]
    return Agent(**kwargs)


agent = _build_agent()


def _patch_mailbox_bearer(api_key: str) -> None:
    """Replace attestation-based auth with Bearer token in the mailbox client.

    Agentverse v2 mailbox API requires `Authorization: Bearer <api_key>`, but
    uagents <0.24 still sends the legacy `Agent <attestation>` header which
    returns 401. Newer uagents (we're on 0.24.2) already do the right thing,
    so this patch is opt-in via AGENT_PATCH_MAILBOX_BEARER=true.

    Borrowed from fetchai/innovation-lab-examples real-estate-search-agent.
    """
    client = agent.mailbox_client
    if client is None:
        return

    async def _check_mailbox_loop(self):
        while True:
            try:
                async with aiohttp.ClientSession() as session:
                    url = f"{self._agentverse.agents_api}/{self._identity.address}/mailbox"
                    async with session.get(
                        url, headers={"Authorization": f"Bearer {api_key}"},
                    ) as resp:
                        if resp.status == 200:
                            for item in await resp.json():
                                await self._handle_envelope(StoredEnvelope.model_validate(item))
                        elif resp.status == 404:
                            if not self._missing_mailbox_warning_logged:
                                self._logger.warning(
                                    "Agent mailbox not found on Agentverse -- "
                                    "register it first (Agent inspector URL)."
                                )
                                self._missing_mailbox_warning_logged = True
                        else:
                            self._logger.error(
                                f"Mailbox poll failed: {resp.status}: {await resp.text()}"
                            )
            except aiohttp.ClientConnectorError as ex:
                self._logger.warning(f"Mailbox connect failed: {ex}")
            except Exception as ex:
                self._logger.exception(f"Mailbox poll exception: {ex}")
            await asyncio.sleep(self._poll_interval)

    async def _delete_envelope(self, uuid: UUID4):
        try:
            async with aiohttp.ClientSession() as session:
                url = f"{self._agentverse.agents_api}/{self._identity.address}/mailbox/{uuid}"
                async with session.delete(
                    url, headers={"Authorization": f"Bearer {api_key}"},
                ) as resp:
                    if resp.status >= 300:
                        self._logger.warning(f"Envelope delete failed: {await resp.text()}")
        except aiohttp.ClientConnectorError as ex:
            self._logger.warning(f"Mailbox connect failed: {ex}")
        except Exception as ex:
            self._logger.exception(f"Envelope delete exception: {ex}")

    client._check_mailbox_loop = types.MethodType(_check_mailbox_loop, client)
    client._delete_envelope = types.MethodType(_delete_envelope, client)


if _bool_env("AGENT_PATCH_MAILBOX_BEARER", False):
    _api_key = os.getenv("AGENTVERSE_API_KEY", "").strip()
    if _api_key:
        _patch_mailbox_bearer(_api_key)
    else:
        log.warning("AGENT_PATCH_MAILBOX_BEARER set but AGENTVERSE_API_KEY missing -- skipping patch")


@agent.on_event("startup")
async def _on_startup(ctx: Context):
    """Log the bits you'll need to debug or share -- address, network, port."""
    ctx.logger.info(f"Agent name:    {agent.name}")
    ctx.logger.info(f"Agent address: {agent.address}")
    ctx.logger.info(f"Network:       {os.getenv('AGENT_NETWORK', 'testnet')}")
    ctx.logger.info(f"Mailbox:       {_bool_env('AGENT_MAILBOX', True)}")
    if not ASI_API_KEY:
        ctx.logger.warning("ASI_ONE_API_KEY missing -- routing/summarizing will 401")

# ---------------------------------------------------------------------------
# Chat protocol + repo resolution
# ---------------------------------------------------------------------------

protocol = Protocol(spec=chat_protocol_spec)

_GITHUB_URL_RE = re.compile(
    r"(?:https?://)?(?:www\.)?github\.com/([\w.\-]+/[\w.\-]+)(?:\.git)?/?"
)


def _resolve_repo(path_or_url: str) -> tuple[str, str | None]:
    """If path_or_url is a GitHub URL, clone it and return (local_path, tmp_dir).
    If it's a local path, return (path, None). tmp_dir should be cleaned up after use."""
    match = _GITHUB_URL_RE.search(path_or_url)
    if match:
        repo_slug = match.group(1)
        repo_slug = re.sub(r'\.git$', '', repo_slug)
        clone_url = f"https://github.com/{repo_slug}.git"
        # Embed owner_repo in the temp-dir name so concurrent clones are
        # distinguishable on disk (e.g. `kowalski_pallets_flask_xyz123`).
        slug_safe = re.sub(r"[^\w.-]", "_", repo_slug)
        tmp_dir = tempfile.mkdtemp(prefix=f"kowalski_{slug_safe}_")
        repo_dir = os.path.join(tmp_dir, "repo")
        log.info("Cloning %s into %s", clone_url, repo_dir)
        try:
            result = subprocess.run(
                ["git", "clone", "--depth", "1", clone_url, repo_dir],
                capture_output=True, text=True, timeout=120,
            )
        except Exception:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise
        if result.returncode != 0:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            raise RuntimeError(f"Failed to clone {clone_url}: {result.stderr.strip()}")
        return repo_dir, tmp_dir
    return os.path.abspath(path_or_url), None


# ---------------------------------------------------------------------------
# Two-pass LLM: cheap router classifier, then a bigger summarizer model
# ---------------------------------------------------------------------------

TOOL_DESCRIPTIONS = """You are a CLASSIFIER, not a chat assistant.

Your output is parsed by code, not shown to a user. Output exactly ONE
short line starting with one of three tags: CHAT:, TOOL:, or HELP:. Do
not write a sentence. Do not apologize. Do not explain. Do not greet the
user. Just emit the tag.

If your first three characters aren't C, T, or H, you have failed.

============================================================
RESPONSE TYPE 1 -- CHAT (use this MOST often for short messages)
============================================================
When: greetings, thanks, capability questions, conceptual questions,
clarifications, small talk -- ANY message that does not name a specific
repo/path/URL to analyze.

Format: CHAT:

Examples:
- "hi" -> CHAT:
- "what can you do?" -> CHAT:
- "what can you do for me" -> CHAT:
- "how does this work?" -> CHAT:
- "who are you?" -> CHAT:
- "thanks!" -> CHAT:
- "explain SDP to me" -> CHAT:
- "how do you compute instability?" -> CHAT:
- "what's a god module?" -> CHAT:

============================================================
RESPONSE TYPE 2 -- TOOL (only when a specific repo/path is given)
============================================================
When: the user names a GitHub URL (https://github.com/...) OR an absolute
local path (/home/..., C:\\..., etc.) AND wants something done with it.

Format: TOOL:<tool_name>|PATH:<path_or_url>|ARG:<extra_arg>

Available tools:
1. analyze_repo(path) - full architectural overview
2. module_health(path, module) - per-module card; ARG = dotted module name
3. suggest_refactor(path, feature_description) - pre-feature advice; ARG = feature
4. check_change(path, files) - delta after edits; ARG = comma-separated file list
5. refactor_assistance(path) - Ca/Ce-focused refactor brief at package, file, and function levels
6. generate_graph(path) - generate interactive HTML dependency graph

Examples:
- "analyze https://github.com/pallets/flask" -> TOOL:analyze_repo|PATH:https://github.com/pallets/flask|ARG:
- "analyze /home/user/myproject" -> TOOL:analyze_repo|PATH:/home/user/myproject|ARG:
- "check health of handlers.user in https://github.com/x/y" -> TOOL:module_health|PATH:https://github.com/x/y|ARG:handlers.user
- "I want to add auth in https://github.com/x/y" -> TOOL:suggest_refactor|PATH:https://github.com/x/y|ARG:add auth
- "check changes to handlers/user.py in /home/u/p" -> TOOL:check_change|PATH:/home/u/p|ARG:handlers/user.py
- "coupling metrics for https://github.com/x/y" -> TOOL:refactor_assistance|PATH:https://github.com/x/y|ARG:
- "graph for https://github.com/x/y" -> TOOL:generate_graph|PATH:https://github.com/x/y|ARG:

============================================================
RESPONSE TYPE 3 -- HELP (rare; user asks to analyze but gives no path)
============================================================
When: user clearly wants an analysis ("analyze my repo", "check this") but
provides no GitHub URL or absolute path.

Format: HELP:<message asking for the path>

Example:
- "analyze my repo" -> HELP:Please provide a GitHub repo URL or absolute path. e.g. analyze https://github.com/pallets/flask

============================================================
DECISION RULE
============================================================
- No URL/path mentioned + short or conversational message -> CHAT:
- URL/path mentioned -> TOOL:...
- Action-words ("analyze", "check") with NO path -> HELP:

When in doubt, choose CHAT: -- it's safer than firing a tool with empty args.
"""


def _parse_tool_call(llm_response: str) -> dict:
    """Parse the LLM's structured tool-call response into a dict.

    Fail-safe: if the response doesn't start with a recognized tag, treat
    as CHAT. Previously this defaulted to analyze_repo with empty path,
    which fires a useless tool call when the router goes rogue and
    produces conversational prose instead of a tag.
    """
    line = llm_response.strip().splitlines()[0].strip()

    if line.startswith("CHAT:"):
        return {"tool": "chat"}

    if line.startswith("HELP:"):
        return {"tool": "help", "message": line[5:].strip()}

    if not line.startswith("TOOL:"):
        # Router didn't follow the format -- safest fallback is CHAT
        log.warning("Router output didn't start with a tag; falling back to CHAT")
        return {"tool": "chat"}

    parts = {}
    for segment in line.split("|"):
        if ":" in segment:
            key, val = segment.split(":", 1)
            parts[key.strip()] = val.strip()

    tool = parts.get("TOOL", "")
    path = parts.get("PATH", "")
    # If TOOL was claimed but PATH is empty, the router is hallucinating --
    # don't fire an empty tool call, route to CHAT so the user gets a
    # real conversational reply instead of an "I need a path" loop.
    if not path:
        log.warning("Router emitted TOOL: with empty PATH; falling back to CHAT")
        return {"tool": "chat"}

    return {
        "tool": tool or "chat",
        "path": path,
        "arg": parts.get("ARG", ""),
    }


def _route(user_text: str, history: list[dict]) -> dict:
    """First pass: cheap router model picks which tool to call.

    Deliberately does NOT see prior conversation history. The router is a
    stateless classifier; passing history caused it to roleplay as the
    assistant and emit conversational replies instead of tags. If we ever
    need history-aware routing (e.g. 'now check db.session' resolving the
    implicit repo from earlier), do it explicitly with a separate context
    field, not by feeding it the full chat log.

    history is accepted (and ignored) so the call signature stays stable
    for handle_message; it's still used by _summarize.
    """
    del history  # intentionally unused; see docstring
    messages = [
        {"role": "system", "content": TOOL_DESCRIPTIONS},
        {"role": "user", "content": user_text},
    ]
    r = client.chat.completions.create(
        model=_ROUTER_MODEL,
        messages=messages,
        max_tokens=64,  # one tag line is well under this; tighter cap discourages prose
    )
    raw = str(r.choices[0].message.content)
    log.info("Router raw response: %r", raw[:200])
    return _parse_tool_call(raw)


_SUMMARIZER_SYSTEM = """You are KevinKowalski, an architectural analysis assistant.

The tool you just called returned a Markdown analysis. Your job: write a SHORT
conversational summary (2-4 sentences) that:
- Highlights the single most important finding
- Suggests one concrete next step the user could take
- Does NOT repeat the raw metrics verbatim -- the user will see the full
  analysis below your summary, separated by a horizontal rule.

If the tool output is an error or a "not found" message, briefly explain what
went wrong and suggest how to fix the call (e.g. provide a GitHub URL, specify
a module name).

Be direct and useful. No preamble, no "Here is a summary:", just the summary."""


_CHAT_SYSTEM = """You are KevinKowalski, a friendly architectural analysis assistant for Python repos.

The user is having a CONVERSATIONAL exchange -- not asking for a specific
analysis. Respond directly and warmly. Keep it short (2-5 sentences).

Capabilities you can talk about:
- Analyzing a Python repo's architecture: coupling (Ca/Ce), instability,
  cohesion (LCOM4), cyclomatic complexity
- Pointing out which modules to refactor BEFORE adding a new feature
- Verifying whether a change improved or worsened the architecture
- Returning the dependency graph as JSON for visualization

You accept GitHub URLs or absolute local paths. Invite them to try
"analyze https://github.com/<owner>/<repo>" if the moment is right, but
don't push -- if they're just saying hi, just say hi back.

Do NOT pretend you've already analyzed something. Do NOT make up metrics.
Do NOT use a horizontal rule (---) -- this is a plain conversational reply."""


def _summarize(
    user_text: str, tool_name: str, raw_output: str, history: list[dict]
) -> str:
    """Second pass: bigger model writes a natural-language take on the tool result.

    When tool_name == "chat", we're in conversational mode -- no tool was
    called, no raw output to summarize. Use a different system prompt and
    skip the tool-output framing.
    """
    is_chat = tool_name == "chat"
    system = _CHAT_SYSTEM if is_chat else _SUMMARIZER_SYSTEM
    messages = [{"role": "system", "content": system}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})

    if not is_chat:
        messages.append(
            {
                "role": "assistant",
                "content": f"[Called tool `{tool_name}`. Output below.]\n\n{raw_output}",
            }
        )
        messages.append(
            {
                "role": "user",
                "content": "Now write your short summary as instructed.",
            }
        )

    r = client.chat.completions.create(
        model=_SUMMARIZER_MODEL,
        messages=messages,
        max_tokens=600,
    )
    return str(r.choices[0].message.content).strip()


# ---------------------------------------------------------------------------
# Graph generation helper
# ---------------------------------------------------------------------------

_REPO_ROOT = Path(__file__).parent.parent


def _parse_viz_stdout(stdout: str, output_path: str) -> dict:
    """Parse visualization CLI stdout into the result dict for format_generate_graph."""
    result: dict = {"output_path": output_path}

    m = re.search(r"File-level:\s*(\d+)\s*nodes,\s*(\d+)\s*edges", stdout)
    result["file_nodes"] = int(m.group(1)) if m else 0
    result["file_edges"] = int(m.group(2)) if m else 0

    m = re.search(r"Package-level:\s*(\d+)\s*nodes,\s*(\d+)\s*edges", stdout)
    result["package_nodes"] = int(m.group(1)) if m else 0
    result["package_edges"] = int(m.group(2)) if m else 0

    m = re.search(r"Function-level:\s*(\d+)\s*nodes,\s*(\d+)\s*edges", stdout)
    result["function_nodes"] = int(m.group(1)) if m else 0
    result["function_edges"] = int(m.group(2)) if m else 0

    m = re.search(r"Cycles \(file\):\s*(\d+)\s*nodes", stdout)
    result["file_cycle_count"] = int(m.group(1)) if m else 0

    m = re.search(r"High impact.*?:\s*(\d+)", stdout)
    result["high_impact_count"] = int(m.group(1)) if m else 0

    m = re.search(r"High susceptibility.*?:\s*(\d+)", stdout)
    result["high_susceptibility_count"] = int(m.group(1)) if m else 0

    return result


def _generate_graph_for_path(repo_path: str) -> str:
    """Run the visualization module to generate an interactive dependency graph."""
    cmd = [
        sys.executable, "-m", "visualization.generate_graph",
        "--path", repo_path,
        "--no-browser",
    ]
    proc = subprocess.run(
        cmd, cwd=str(_REPO_ROOT),
        capture_output=True, text=True, timeout=120,
    )

    if proc.returncode != 0:
        return f"Graph generation failed (exit {proc.returncode}): {proc.stderr.strip()}"

    output_path = viz_html_path_from_generate_stdout(proc.stdout, _REPO_ROOT)
    result = _parse_viz_stdout(proc.stdout, str(output_path))
    return format_generate_graph(result)


# ---------------------------------------------------------------------------
# Tool dispatcher
# ---------------------------------------------------------------------------

def _run_tool(tool_name: str, path_or_url: str, arg: str) -> str:
    """Execute a KevinKowalski tool and return the Markdown result."""
    if not path_or_url or path_or_url in (".", "./"):
        return (
            "I need a GitHub repo URL or absolute path to analyze. "
            "For example: 'analyze https://github.com/pallets/flask' "
            "or 'analyze /home/user/my-python-project'"
        )

    tmp_dir = None
    try:
        path, tmp_dir = _resolve_repo(path_or_url)

        if tool_name == "analyze_repo":
            snapshot = analyzer.analyze(path)
            return format_analyze_repo(snapshot)

        elif tool_name == "module_health":
            if not arg:
                return "Please specify a module name, e.g.: 'check health of handlers.user in https://github.com/user/repo'"
            snapshot = analyzer.analyze(path)
            return format_module_health(snapshot, arg)

        elif tool_name == "suggest_refactor":
            if not arg:
                arg = "general improvements"
            snapshot = analyzer.analyze(path)
            return format_suggest_refactor(snapshot, arg)

        elif tool_name == "check_change":
            files = [f.strip() for f in arg.split(",") if f.strip()] if arg else []
            if not files:
                return "Please specify which files changed, e.g.: 'check changes to handlers/user.py in https://github.com/user/repo'"
            result = analyzer.incremental_check(path, files)
            return format_check_change(result)

        elif tool_name == "refactor_assistance":
            from visualization.utils import (
                aggregate_to_packages,
                build_function_graph,
                compute_metrics,
                find_cycle_info,
                parse_edges,
            )
            source_root = Path(path)
            edges_list, all_modules = parse_edges(source_root)

            file_graph = nx.DiGraph()
            for module in all_modules:
                file_graph.add_node(module)
            for edge in edges_list:
                file_graph.add_edge(edge.src, edge.dst)
            file_metrics = compute_metrics(file_graph)

            pkg_edges = aggregate_to_packages(edges_list)
            package_graph = nx.DiGraph()
            pkg_names = {m.split(".")[0] for m in all_modules}
            for pkg in pkg_names:
                package_graph.add_node(pkg)
            for edge in pkg_edges:
                package_graph.add_edge(edge.src, edge.dst)
            package_metrics = compute_metrics(package_graph)

            try:
                function_graph, function_metadata = build_function_graph(source_root)
            except Exception:
                function_graph = nx.DiGraph()
                function_metadata = {}
            function_metrics = compute_metrics(function_graph)

            def _metrics_to_dict(nm):
                return {
                    "ca": nm.ca, "ce": nm.ce,
                    "instability": nm.instability,
                    "impact": nm.impact,
                    "susceptibility": nm.susceptibility,
                    "raw_impact": nm.raw_impact,
                    "raw_susceptibility": nm.raw_susceptibility,
                }

            def _level_block(graph, metrics, metadata=None, top_n=5, cap=3):
                if graph.number_of_nodes() == 0 or not metrics:
                    return {"node_count": 0, "edge_count": 0,
                            "high_susceptibility_detail": [], "high_impact_detail": []}
                nodes = list(graph.nodes())
                by_sus = sorted(nodes, key=lambda n: metrics[n].raw_susceptibility, reverse=True)[:top_n]
                by_imp = sorted(nodes, key=lambda n: metrics[n].raw_impact, reverse=True)[:top_n]
                sus_detail = [{"id": n, "metrics": _metrics_to_dict(metrics[n]),
                               "high_impact_dependents": [{"id": p, **_metrics_to_dict(metrics[p])}
                                   for p in sorted(list(graph.predecessors(n)),
                                       key=lambda p: metrics[p].raw_impact, reverse=True)[:cap]]}
                              for n in by_sus]
                imp_detail = [{"id": n, "metrics": _metrics_to_dict(metrics[n]),
                               "high_susceptibility_dependencies": [{"id": s, **_metrics_to_dict(metrics[s])}
                                   for s in sorted(list(graph.successors(n)),
                                       key=lambda s: metrics[s].raw_susceptibility, reverse=True)[:cap]]}
                              for n in by_imp]
                return {"node_count": graph.number_of_nodes(), "edge_count": graph.number_of_edges(),
                        "high_susceptibility_detail": sus_detail, "high_impact_detail": imp_detail}

            payload = {
                "root": path,
                "levels": {
                    "package": _level_block(package_graph, package_metrics),
                    "file": _level_block(file_graph, file_metrics),
                    "function": _level_block(function_graph, function_metrics, function_metadata),
                },
            }
            return format_refactor_assistance(payload)

        elif tool_name == "generate_graph":
            return _generate_graph_for_path(path)

        else:
            return f"Unknown tool: {tool_name}. Available: analyze_repo, module_health, suggest_refactor, check_change, refactor_assistance, generate_graph"

    except Exception as e:
        return f"Failed to process repository: {type(e).__name__}: {e}"
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Chat protocol handlers
# ---------------------------------------------------------------------------

@protocol.on_message(ChatMessage)
async def handle_message(ctx: Context, sender: str, msg: ChatMessage):
    ctx.logger.info(f"Received message from {sender}")

    await ctx.send(
        sender,
        ChatAcknowledgement(timestamp=datetime.now(), acknowledged_msg_id=msg.msg_id),
    )

    text = ""
    for item in msg.content:
        if isinstance(item, TextContent):
            text += item.text

    if not text.strip():
        response_text = "Please send a message describing what you'd like to analyze."
    else:
        history = _load_history(ctx, sender)
        try:
            # Pass 1: route to a tool (uses history so follow-ups resolve correctly)
            parsed = _route(text, history)
            ctx.logger.info(f"Routed to: {parsed}")

            if parsed["tool"] == "help":
                response_text = parsed["message"]
            elif parsed["tool"] == "chat":
                # Conversational reply -- no tool, no raw output appendix.
                # Single LLM call instead of two; ~half the latency.
                response_text = _summarize(text, "chat", "", history)
            else:
                # Run the tool (raw Markdown)
                raw = _run_tool(parsed["tool"], parsed["path"], parsed["arg"])

                # Pass 2: bigger model summarizes for the user
                try:
                    summary = _summarize(text, parsed["tool"], raw, history)
                    response_text = f"{summary}\n\n---\n\n{raw}"
                except Exception as e:
                    # If summarizer fails, still return the raw tool output --
                    # better to ship the data than nothing.
                    ctx.logger.exception("Summarizer failed; returning raw output")
                    response_text = raw

            # Update history with the user's turn and a compact assistant turn.
            # Store only the summary (not the raw Markdown) to keep prompts small.
            assistant_record = (
                response_text.split("\n\n---\n\n", 1)[0]
                if "\n\n---\n\n" in response_text
                else response_text
            )
            history.append({"role": "user", "content": text})
            history.append({"role": "assistant", "content": assistant_record})
            _save_history(ctx, sender, history)

        except Exception as e:
            ctx.logger.exception("Error processing message")
            response_text = f"Sorry, I encountered an error: {type(e).__name__}: {e}"

    await ctx.send(
        sender,
        ChatMessage(
            timestamp=datetime.now(),
            msg_id=uuid4(),
            content=[
                TextContent(type="text", text=response_text),
                EndSessionContent(type="end-session"),
            ],
        ),
    )


@protocol.on_message(ChatAcknowledgement)
async def handle_ack(_ctx: Context, _sender: str, _msg: ChatAcknowledgement):
    pass  # acks from ASI:One are informational only -- nothing to do


agent.include(protocol, publish_manifest=True)

if __name__ == "__main__":
    log.info("KevinKowalski Agentverse agent starting")
    agent.run()
