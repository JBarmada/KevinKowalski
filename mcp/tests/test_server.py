"""Tier 3: MCP server smoke tests.

Loads the FastMCP instance in-process and verifies all 5 tools register
and execute through the MCP machinery (not just as plain functions).
Analyzer-agnostic -- survives the swap.
"""

import asyncio
import json

import pytest

import mcp_server


EXPECTED_TOOLS = {
    "analyze_repo",
    "module_health",
    "suggest_refactor",
    "check_change",
    "get_metric_graph",
}


def _list_tools() -> list:
    return asyncio.run(mcp_server.mcp.list_tools())


def _call_tool(name: str, args: dict) -> str:
    """Call a tool through the MCP layer and return its text output."""
    result = asyncio.run(mcp_server.mcp._call_tool_mcp(name, args))
    # FastMCP returns a CallToolResult; content is a list of TextContent.
    parts = []
    for block in result.content:
        text = getattr(block, "text", None)
        if text is not None:
            parts.append(text)
    return "\n".join(parts)


def test_all_five_tools_register():
    names = {t.name for t in _list_tools()}
    assert names == EXPECTED_TOOLS, f"got {names}"


def test_each_tool_has_description():
    for tool in _list_tools():
        assert tool.description and len(tool.description) > 20, (
            f"{tool.name} needs a real description for the agent"
        )


def test_analyze_repo_returns_string():
    out = _call_tool("analyze_repo", {"path": "."})
    assert isinstance(out, str) and out


def test_module_health_returns_string():
    out = _call_tool("module_health", {"path": ".", "module": "handlers.user"})
    assert isinstance(out, str) and out


def test_suggest_refactor_returns_string():
    out = _call_tool("suggest_refactor", {"path": ".", "feature_description": "add audit logging"})
    assert isinstance(out, str) and out


def test_check_change_returns_string():
    out = _call_tool("check_change", {"path": ".", "files": ["handlers/user.py"]})
    assert isinstance(out, str) and out


def test_get_metric_graph_returns_valid_json():
    out = _call_tool("get_metric_graph", {"path": "."})
    parsed = json.loads(out)
    assert "nodes" in parsed and "edges" in parsed
