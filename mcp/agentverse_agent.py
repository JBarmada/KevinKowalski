"""KevinKowalski Agentverse agent.

Wraps the KevinKowalski architectural analysis tools as a uAgent registered
on Agentverse with the Chat Protocol, making them accessible via ASI:One.

Users send natural-language queries through ASI:One Chat; this agent parses
the intent, runs the appropriate analysis tool, and returns the Markdown
result.

Requires:
    - ASI_ONE_API_KEY env var (get one at https://asi1.ai/dashboard/api-keys)
    - An Agentverse account (https://agentverse.ai)
    - uagents library (`uv add uagents`)
"""

import logging
import os
import sys
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

TOOL_DESCRIPTIONS = """You are KevinKowalski, an architectural analysis assistant for Python repos.
You have 5 tools available:

1. analyze_repo(path) - Full architectural analysis. Returns module count, edges, instability, violations.
2. module_health(path, module) - Health card for a specific module. Returns Ca/Ce/instability/LCOM/CC.
3. suggest_refactor(path, feature_description) - Pre-feature decoupling advice. Lists modules to fix first.
4. check_change(path, files) - Re-analyze modified files. Returns before/after metrics with verdict.
5. get_metric_graph(path) - Raw JSON graph data (nodes + edges) for visualization.

Given the user's message, respond with EXACTLY one line in this format:
TOOL:<tool_name>|PATH:<path>|ARG:<extra_arg>

Examples:
- "analyze my repo at /home/user/myproject" -> TOOL:analyze_repo|PATH:/home/user/myproject|ARG:
- "check health of handlers.user in /home/user/myproject" -> TOOL:module_health|PATH:/home/user/myproject|ARG:handlers.user
- "I want to add auth, what should I refactor in /tmp/app" -> TOOL:suggest_refactor|PATH:/tmp/app|ARG:add authentication feature
- "check changes to handlers/user.py in /home/user/proj" -> TOOL:check_change|PATH:/home/user/proj|ARG:handlers/user.py
- "get the dependency graph for /home/user/myproject" -> TOOL:get_metric_graph|PATH:/home/user/myproject|ARG:

If the user's message doesn't clearly map to a tool, or is a general question about architecture, respond with:
TOOL:analyze_repo|PATH:<best_guess_path>|ARG:

If you cannot determine a path at all, respond with:
HELP:Please provide the absolute path to the Python repo you want to analyze. For example: "analyze /home/user/my-python-project"
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


def _run_tool(tool_name: str, path: str, arg: str) -> str:
    """Execute a KevinKowalski tool and return the Markdown result."""
    if not path or path in (".", "./"):
        return (
            "I need an absolute path to the repo to analyze. "
            "Please provide one, e.g.: 'analyze /home/user/my-python-project'"
        )

    path = os.path.abspath(path)

    if tool_name == "analyze_repo":
        snapshot = analyzer.analyze(path)
        return format_analyze_repo(snapshot)

    elif tool_name == "module_health":
        if not arg:
            return "Please specify a module name, e.g.: 'check health of handlers.user in /path/to/repo'"
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
            return "Please specify which files changed, e.g.: 'check changes to handlers/user.py in /path/to/repo'"
        result = analyzer.incremental_check(path, files)
        return format_check_change(result)

    elif tool_name == "get_metric_graph":
        snapshot = analyzer.analyze(path)
        return format_metric_graph(snapshot)

    else:
        return f"Unknown tool: {tool_name}. Available: analyze_repo, module_health, suggest_refactor, check_change, get_metric_graph"


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
