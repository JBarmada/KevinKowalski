"""KevinKowalski Agentverse agent.

Wraps the KevinKowalski architectural analysis tools as a uAgent registered
on Agentverse with the Chat Protocol, making them accessible via ASI:One.

Users send natural-language queries through ASI:One Chat; this agent parses
the intent, runs the appropriate analysis tool, and returns the Markdown
result. Accepts GitHub repo URLs — repos are cloned automatically.

Requires:
    - ASI_ONE_API_KEY env var (get one at https://asi1.ai/dashboard/api-keys)
    - An Agentverse account (https://agentverse.ai)
    - uagents library (`uv add uagents`)
    - git (for cloning repos from GitHub URLs)
"""

import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from uuid import uuid4

from openai import OpenAI
from uagents import Agent, Context, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    EndSessionContent,
    TextContent,
    chat_protocol_spec,
)

from fake_analyzer import get_analyzer
from formatters import (
    format_analyze_repo,
    format_check_change,
    format_metric_graph,
    format_module_health,
    format_suggest_refactor,
)

logging.basicConfig(
    stream=sys.stderr,
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("kowalski-agent")

ASI_API_KEY = os.environ.get("ASI_ONE_API_KEY", "")
if not ASI_API_KEY:
    log.warning("ASI_ONE_API_KEY not set — LLM routing will fail")

client = OpenAI(
    base_url="https://api.asi1.ai/v1",
    api_key=ASI_API_KEY,
)

analyzer = get_analyzer()

# Conversation memory: persisted via ctx.storage (KeyValueStore on disk).
# Verified API: get/has/set/remove/clear; values are JSON-serialized.
# We cap total stored turns per sender to bound prompt size and cost.
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

agent = Agent(
    name="KevinKowalski",
    seed=os.environ.get("AGENT_SEED", "kevin-kowalski-arch-agent-seed-phrase-2026"),
    port=8001,
    mailbox=True,
    publish_agent_details=True,
    network="testnet",  # use testnet Almanac contract; auto-funded, no FET needed
)

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
        tmp_dir = tempfile.mkdtemp(prefix="kowalski_")
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


TOOL_DESCRIPTIONS = """You are a router for KevinKowalski, an architectural
analysis assistant. Your only job: classify the user message into ONE of
three response types. Output exactly ONE line, nothing else.

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
5. get_metric_graph(path) - JSON nodes+edges for viz

Examples:
- "analyze https://github.com/pallets/flask" -> TOOL:analyze_repo|PATH:https://github.com/pallets/flask|ARG:
- "analyze /home/user/myproject" -> TOOL:analyze_repo|PATH:/home/user/myproject|ARG:
- "check health of handlers.user in https://github.com/x/y" -> TOOL:module_health|PATH:https://github.com/x/y|ARG:handlers.user
- "I want to add auth in https://github.com/x/y" -> TOOL:suggest_refactor|PATH:https://github.com/x/y|ARG:add auth
- "check changes to handlers/user.py in /home/u/p" -> TOOL:check_change|PATH:/home/u/p|ARG:handlers/user.py
- "graph for https://github.com/x/y" -> TOOL:get_metric_graph|PATH:https://github.com/x/y|ARG:

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
    """Parse the LLM's structured tool-call response into a dict."""
    line = llm_response.strip().splitlines()[0].strip()

    if line.startswith("CHAT:"):
        return {"tool": "chat"}

    if line.startswith("HELP:"):
        return {"tool": "help", "message": line[5:].strip()}

    parts = {}
    for segment in line.split("|"):
        if ":" in segment:
            key, val = segment.split(":", 1)
            parts[key.strip()] = val.strip()

    return {
        "tool": parts.get("TOOL", "analyze_repo"),
        "path": parts.get("PATH", ""),
        "arg": parts.get("ARG", ""),
    }


def _route(user_text: str, history: list[dict]) -> dict:
    """First pass: cheap router model picks which tool to call.

    Sees prior conversation history so follow-ups like 'now check db.session'
    can resolve the implicit repo path from earlier turns.
    """
    messages = [{"role": "system", "content": TOOL_DESCRIPTIONS}]
    messages.extend(history)
    messages.append({"role": "user", "content": user_text})
    r = client.chat.completions.create(
        model=_ROUTER_MODEL,
        messages=messages,
        max_tokens=128,
    )
    raw = str(r.choices[0].message.content)
    log.info("Router raw response: %r", raw[:200])  # so we can debug bad routes
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

        elif tool_name == "get_metric_graph":
            snapshot = analyzer.analyze(path)
            return format_metric_graph(snapshot)

        else:
            return f"Unknown tool: {tool_name}. Available: analyze_repo, module_health, suggest_refactor, check_change, get_metric_graph"

    except Exception as e:
        return f"Failed to process repository: {type(e).__name__}: {e}"
    finally:
        if tmp_dir:
            shutil.rmtree(tmp_dir, ignore_errors=True)


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
async def handle_ack(ctx: Context, sender: str, msg: ChatAcknowledgement):
    pass


agent.include(protocol, publish_manifest=True)

if __name__ == "__main__":
    log.info("KevinKowalski Agentverse agent starting")
    agent.run()
