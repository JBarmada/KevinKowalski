# Analyzer contract — what the MCP server expects

This directory contains the **metrics** library. The MCP server in `../mcp/` speaks to **RealAnalyzer** (`../mcp/real_analyzer.py`) which satisfies the contract defined in `../mcp/contract.py`.

---

## The contract — two functions

```python
def analyze(repo_path: str) -> GraphSnapshot: ...
def incremental_check(repo_path: str, files: list[str]) -> dict: ...
```

…exposed via a `get_analyzer()` accessor that returns an object satisfying the `Analyzer` protocol. Both definitions live in [`../mcp/contract.py`](../mcp/contract.py). **Read that file first — it is short and complete.**

---

## The data shapes

From `mcp/contract.py`:

```python
@dataclass
class ModuleMetrics:
    module: str          # dotted name, e.g. "handlers.user"
    path: str            # repo-relative path, e.g. "handlers/user.py"
    ca: int              # afferent coupling: how many internal modules import this one
    ce: int              # efferent coupling: how many internal modules this one imports
    instability: float   # ce / (ca + ce); 0.0 if both are zero
    lcom4: float | None  # cohesion (1 = cohesive, >1 = split candidate); None if no classes
    cc_max: int          # worst function's cyclomatic complexity in this module
    violations: list[str] = []   # rule IDs, e.g. ["SDP", "GOD_MODULE", "HIGH_CC", "CYCLE"]

@dataclass
class GraphSnapshot:
    root: str                              # absolute repo path that was analyzed
    modules: dict[str, ModuleMetrics]      # keyed by ModuleMetrics.module
    edges: list[tuple[str, str]]           # (importer_module, importee_module)
```

### Hard rules the test suite enforces

These come straight from `mcp/tests/test_contract.py` and apply to *any* analyzer:

1. `analyze()` returns a `GraphSnapshot`. `incremental_check()` returns a `dict`.
2. Every `dict` key in `modules` equals the value's `.module` field. Don't drift.
3. `ca >= 0`, `ce >= 0`, `0.0 <= instability <= 1.0`, `cc_max >= 0`.
4. Every edge endpoint must exist in `modules`. No edges to unknown / external modules.
5. `incremental_check` returns a dict with keys `changed`, `new_violations`, `resolved_violations`, `verdict`. `verdict` ∈ `{"green", "yellow", "red"}`.

---

## Conventions

| Decision | Value | Why |
|---|---|---|
| Module naming | dotted (`handlers.user`) | Matches what the MCP server passes back to itself; matches existing `metrics/graph.py` style |
| Path naming | repo-relative, forward slashes (`handlers/user.py`) | Cross-platform, what users see in their editor |
| Sync vs async | sync only | MCP tools don't need async; mixing causes pain |
| Edges | internal modules only | Skip edges to stdlib / installed packages (matches existing `graph.py` behavior) |

---

## Existing implementation

- [`mcp/real_analyzer.py`](../mcp/real_analyzer.py) — the active analyzer. Uses `metrics/graph.py`'s `parse_edges_v2()` for AST-based import resolution, `radon` for cyclomatic complexity, and a custom AST walk for LCOM4. Detects `SDP`, `GOD_MODULE`, `HIGH_CC`, and `CYCLE` violations.
- [`metrics/graph.py`](graph.py) — `parse_edges_v2()` does AST-based import resolution and skips `TYPE_CHECKING` blocks.
- `pyproject.toml` has `networkx`, `radon`, and other dependencies available.

---

## Out of scope for the analyzer

- Cross-language support — Python only.
- Producing natural-language guidance — that's the **formatter** layer's job (`mcp/formatters.py`).
- Anything to do with the MCP transport — that's handled by `mcp/mcp_server.py`.

---

## Questions / disagreements

If any of the contract feels wrong (e.g. you want richer types, you need an async API), **push back early**. Cheaper to change the contract once than to write code against the wrong shape.

The contract file is the source of truth. Edit it, run the tests, see what breaks, fix downstream.
