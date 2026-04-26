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

agent = Agent(
    name="KevinKowalski",
    seed=os.environ.get("AGENT_SEED", "kevin-kowalski-arch-agent-seed-phrase-2026"),
    port=8001,
    mailbox=True,
    publish_agent_details=True,
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


TOOL_DESCRIPTIONS = """You are KevinKowalski, an architectural analysis assistant for Python repos.
You have 5 tools available:

1. analyze_repo(path) - Full architectural analysis. Returns module count, edges, instability, violations.
2. module_health(path, module) - Health card for a specific module. Returns Ca/Ce/instability/LCOM/CC.
3. suggest_refactor(path, feature_description) - Pre-feature decoupling advice. Lists modules to fix first.
4. check_change(path, files) - Re-analyze modified files. Returns before/after metrics with verdict.
5. get_metric_graph(path) - Raw JSON graph data (nodes + edges) for visualization.

The user can provide either:
- A GitHub URL (e.g. https://github.com/user/repo)
- An absolute local path (e.g. /home/user/myproject)

Given the user's message, respond with EXACTLY one line in this format:
TOOL:<tool_name>|PATH:<path_or_github_url>|ARG:<extra_arg>

Examples:
- "analyze https://github.com/pallets/flask" -> TOOL:analyze_repo|PATH:https://github.com/pallets/flask|ARG:
- "analyze my repo at /home/user/myproject" -> TOOL:analyze_repo|PATH:/home/user/myproject|ARG:
- "check health of handlers.user in https://github.com/user/repo" -> TOOL:module_health|PATH:https://github.com/user/repo|ARG:handlers.user
- "I want to add auth, what should I refactor in https://github.com/user/repo" -> TOOL:suggest_refactor|PATH:https://github.com/user/repo|ARG:add authentication feature
- "check changes to handlers/user.py in /home/user/proj" -> TOOL:check_change|PATH:/home/user/proj|ARG:handlers/user.py
- "get the dependency graph for https://github.com/user/repo" -> TOOL:get_metric_graph|PATH:https://github.com/user/repo|ARG:

If the user's message doesn't clearly map to a tool, or is a general question about architecture, respond with:
TOOL:analyze_repo|PATH:<best_guess_path_or_url>|ARG:

If you cannot determine a path or URL at all, respond with:
HELP:Please provide a GitHub repo URL or absolute path to analyze. For example: \"analyze https://github.com/pallets/flask\" or \"analyze /home/user/my-project\"
"""


def _parse_tool_call(llm_response: str) -> dict:
    """Parse the LLM's structured tool-call response."""
    line = llm_response.strip().splitlines()[0].strip()

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
        try:
            r = client.chat.completions.create(
                model="asi1",
                messages=[
                    {"role": "system", "content": TOOL_DESCRIPTIONS},
                    {"role": "user", "content": text},
                ],
                max_tokens=256,
            )
            llm_output = str(r.choices[0].message.content)
            ctx.logger.info(f"LLM routing: {llm_output}")

            parsed = _parse_tool_call(llm_output)

            if parsed["tool"] == "help":
                response_text = parsed["message"]
            else:
                response_text = _run_tool(
                    parsed["tool"], parsed["path"], parsed["arg"]
                )
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
