# Analyzer team — what to build, what the MCP expects

This directory is where the **real analyzer** lives. The MCP server in `../mcp/` already speaks to a **fake analyzer** built against a fixed contract. Your job is to satisfy that contract with real metrics computed from a real Python repo.

When you do, the MCP server picks you up with a one-line import change. No coordination meeting required.

---

## TL;DR — implement two functions

```python
def analyze(repo_path: str) -> GraphSnapshot: ...
def incremental_check(repo_path: str, files: list[str]) -> dict: ...
```

…and expose a `get_analyzer()` accessor that returns an object satisfying the `Analyzer` protocol. Both definitions live in [`../mcp/contract.py`](../mcp/contract.py). **Read that file first — it is short and complete.**

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
4. Every edge endpoint must exist in `modules`. No edges to unknown / external modules. *(If you discover this is too strict for real-world Python — e.g. you want edges to stdlib — say so and we'll relax.)*
5. `incremental_check` returns a dict with keys `changed`, `new_violations`, `resolved_violations`, `verdict`. `verdict` ∈ `{"green", "yellow", "red"}`.

Run `pytest -m "not fake_only"` after wiring your analyzer in. All 30 tests should still pass without modification — that's the contract working.

---

## Conventions already locked

| Decision | Value | Why |
|---|---|---|
| Module naming | dotted (`handlers.user`) | Matches what the MCP server passes back to itself; matches existing `metrics/graph.py` style |
| Path naming | repo-relative, forward slashes (`handlers/user.py`) | Cross-platform, what users see in their editor |
| Sync vs async | sync only | MCP tools don't need async; mixing causes pain |
| Edges | internal modules only | Skip edges to stdlib / installed packages (matches existing `graph.py` behavior of ignoring absolute imports outside the package) |

---

## Existing groundwork

- [`metrics/graph.py`](graph.py) — Kevin's earlier sketch. `parse_edges_v2()` already does AST-based import resolution and skips `TYPE_CHECKING` blocks. Reusable; just needs to become a library function (not run rendering at import time) and to compute per-module metrics, not just edges.
- `pyproject.toml` already has `networkx`, `matplotlib`, `pydot`, `pyqt6` available.
- The MCP server uses `radon` and `lizard` per the plan but **neither is installed yet** — add them when you need them.

---

## How the MCP server will pick you up

In `mcp/mcp_server.py`, line 21 currently reads:

```python
from fake_analyzer import get_analyzer
```

When your analyzer is ready, that becomes:

```python
from real_analyzer import get_analyzer    # or wherever your accessor lives
```

`get_analyzer()` should return an instance of a class satisfying the protocol. That's the entire integration. After the swap:

- Delete `mcp/fake_analyzer.py`
- Delete `mcp/tests/test_fake_analyzer_only.py` (a banner warns when these run; they will fail post-swap and that is **expected** — the file's docstring tells you to delete, not fix)
- The remaining 30 tests should pass against your real analyzer

---

## Suggested order

1. Read `mcp/contract.py` (it is 60 lines).
2. Read `mcp/fake_analyzer.py` to see what a working implementation looks like.
3. Build `analyze()` first using `metrics/graph.py`'s existing `parse_edges_v2`. Get edges + Ca/Ce/instability working with the existing networkx code. Return placeholder `0` for `lcom4` and `cc_max`.
4. Run `pytest -m "not fake_only"` against your stub-with-real-edges. It should pass.
5. Layer in `cc_max` via `radon.complexity.cc_visit`.
6. Layer in `lcom4` via your own AST walk over class bodies.
7. Add violation detection (`SDP`, `GOD_MODULE`, `HIGH_CC`, `CYCLE`).
8. Implement `incremental_check` last — it can naively re-`analyze()` and diff against a cached snapshot for v1.

---

## Out of scope for the analyzer

- Caching — nice-to-have but not required for v1. The fake analyzer doesn't cache.
- Cross-language support — Python only.
- Producing the natural-language guidance — that's the **advisor** layer's job (separate person, separate file).
- Anything to do with the MCP transport — that's already done.

---

## Questions / disagreements

If any of the contract feels wrong (e.g. you want richer types, you can't compute LCOM cheaply, you need an async API), **push back early**. Cheaper to change the contract once than to write code against the wrong shape.

The contract file is the source of truth. Edit it, run the tests, see what breaks, fix downstream.
