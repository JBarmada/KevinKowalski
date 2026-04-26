"""Hardcoded analyzer used while the real one is under construction.

Returns a plausible GraphSnapshot for an imaginary 6-module web app with one
SDP violation, one god module, and varied instability scores. Just enough
shape that formatters produce convincing output for demos and integration
tests.

Replace with the real analyzer by swapping the import in mcp_server.py.
This file should be deleted (along with test_fake_analyzer_only.py) after
the swap.
"""

from contract import Analyzer, GraphSnapshot, ModuleMetrics


_FAKE_MODULES: dict[str, ModuleMetrics] = {
    "handlers.user": ModuleMetrics(
        module="handlers.user",
        path="handlers/user.py",
        ca=8,
        ce=12,
        instability=0.60,
        lcom4=4.0,
        cc_max=21,
        violations=["GOD_MODULE", "HIGH_CC"],
    ),
    "handlers.billing": ModuleMetrics(
        module="handlers.billing",
        path="handlers/billing.py",
        ca=2,
        ce=8,
        instability=0.80,
        lcom4=2.0,
        cc_max=9,
        violations=[],
    ),
    "db.session": ModuleMetrics(
        module="db.session",
        path="db/session.py",
        ca=1,
        ce=9,
        instability=0.90,
        lcom4=1.0,
        cc_max=6,
        violations=["SDP"],  # unstable but depended on by stable billing
    ),
    "db.models": ModuleMetrics(
        module="db.models",
        path="db/models.py",
        ca=6,
        ce=1,
        instability=0.14,
        lcom4=1.0,
        cc_max=4,
        violations=[],
    ),
    "utils.logging": ModuleMetrics(
        module="utils.logging",
        path="utils/logging.py",
        ca=4,
        ce=0,
        instability=0.0,
        lcom4=None,
        cc_max=2,
        violations=[],
    ),
    "events.bus": ModuleMetrics(
        module="events.bus",
        path="events/bus.py",
        ca=0,
        ce=0,
        instability=0.0,
        lcom4=None,
        cc_max=1,
        violations=[],
    ),
}

_FAKE_EDGES: list[tuple[str, str]] = [
    ("handlers.user", "db.session"),
    ("handlers.user", "db.models"),
    ("handlers.user", "utils.logging"),
    ("handlers.billing", "db.session"),
    ("handlers.billing", "db.models"),
    ("handlers.billing", "utils.logging"),
    ("db.session", "db.models"),
    ("db.session", "utils.logging"),
]


class FakeAnalyzer:
    """Implements the Analyzer protocol with hardcoded data."""

    def analyze(self, repo_path: str) -> GraphSnapshot:
        return GraphSnapshot(
            root=repo_path,
            modules=dict(_FAKE_MODULES),
            edges=list(_FAKE_EDGES),
        )

    def incremental_check(self, repo_path: str, files: list[str]) -> dict:
        # Pretend the agent touched handlers/user.py and improved it.
        before = _FAKE_MODULES["handlers.user"]
        after = ModuleMetrics(
            module=before.module,
            path=before.path,
            ca=before.ca,
            ce=before.ce - 2,
            instability=0.40,
            lcom4=2.0,
            cc_max=9,
            violations=[],
        )
        return {
            "changed": [{"module": before.module, "before": before, "after": after}],
            "new_violations": [],
            "resolved_violations": ["GOD_MODULE", "HIGH_CC"],
            "verdict": "green",
        }


# Module-level instance: import-and-use, no construction needed at call sites.
_analyzer: Analyzer = FakeAnalyzer()


def get_analyzer() -> Analyzer:
    """Single accessor — swapping to the real analyzer means changing this function."""
    return _analyzer
