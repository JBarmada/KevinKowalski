![tag:innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![tag:python](https://img.shields.io/badge/python-3776AB)
![tag:architecture-advisor](https://img.shields.io/badge/architecture_advisor-4CAF50)
![tag:static-analysis](https://img.shields.io/badge/static_analysis-FF6B35)
![tag:ai-powered](https://img.shields.io/badge/ai_powered-9C27B0)

# 🏗️ KevinKowalski

Your intelligent architectural advisor for Python repos! Just share a GitHub URL or describe what you're building, and get instant insights on coupling, cohesion, complexity, and structural health. Whether you need a full repo analysis, a per-module health check, or pre-feature refactoring advice, I'll understand and help you write cleaner code.

## What I Can Do

🔍 **Full Repo Analysis**: Share a GitHub URL like `https://github.com/pallets/flask` and I'll map out the entire dependency graph — module count, internal edges, average instability, and the top architectural offenders.

🩺 **Module Health Cards**: Ask about a specific module and I'll return its coupling (Ca/Ce), instability score, cohesion (LCOM4), cyclomatic complexity, any rule violations, and what imports it or depends on it.

🛠️ **Pre-Feature Refactoring Advice**: Describe the feature you're about to build and I'll tell you which modules to decouple *first* so you don't amplify existing spaghetti.

✅ **Change Verification**: After you make edits, I can re-analyze the modified files and give you a green/yellow/red verdict on whether the architecture improved or got worse.

📊 **Dependency Graph Data**: Get the full metric graph as structured JSON (nodes + edges) for visualization or further processing.

💬 **Natural Conversation**: Talk to me naturally! I'll figure out what you need — and if I need more info (like a repo URL or module name), I'll ask.

## Example Queries

**Full Analysis:**
- "Analyze https://github.com/pallets/flask"
- "What does the architecture of https://github.com/psf/requests look like?"
- "Give me an overview of https://github.com/fastapi/fastapi"

**Module Health:**
- "Check health of handlers.user in https://github.com/myorg/myapp"
- "How is the db.session module doing in https://github.com/myorg/backend?"

**Pre-Feature Advice:**
- "I want to add authentication to https://github.com/myorg/myapp"
- "What should I refactor before adding audit logging in https://github.com/myorg/backend?"

**Change Verification:**
- "Check changes to handlers/user.py in https://github.com/myorg/myapp"
- "Did my edits to models/order.py improve things in https://github.com/myorg/shop?"

**Conversational:**
- "Hi, what can you do?"
- "What's the Stable Dependencies Principle?"
- "Explain LCOM4 to me"

## Sample Responses

### Example 1: Full Repo Analysis

**You ask**: "Analyze https://github.com/pallets/flask"

**I respond**:
```
Flask's architecture is generally healthy, but there are a few hotspots
worth attention — the `app` module has high cyclomatic complexity and the
widest coupling surface. Consider thinning it before adding more features.

---

# Repo analysis — `/tmp/kowalski_.../repo`

- Modules: **42**
- Internal edges: **87**
- Average instability: **0.54**
- Modules with violations: **3**

## Top offenders
- `app` — HIGH_CC (Ca=12, Ce=8, I=0.40, CC=18)
- `helpers` — SDP (Ca=6, Ce=10, I=0.63, CC=9)
- `wrappers` — GOD_MODULE (Ca=4, Ce=7, I=0.64, CC=6)
```

### Example 2: Module Health Card

**You ask**: "Check health of handlers.user in https://github.com/myorg/myapp"

**I respond**:
```
The `handlers.user` module is a coupling hotspot — it imports 6 other
modules and has high cyclomatic complexity. Consider extracting the
validation logic into a separate module.

---

# Module `handlers.user`
- Path: `handlers/user.py`
- Afferent (Ca): **3**  Efferent (Ce): **6**
- Instability: **0.67** (mixed)
- LCOM4: **2**
- Max cyclomatic: **14**

## Violations
- **HIGH_CC** — Cyclomatic complexity hotspot: at least one function
  is hard to reason about.

## Imported by (3)
`routes.api`, `routes.web`, `tests.test_user`

## Imports (6)
`models.user`, `models.role`, `db.session`, `utils.auth`,
`utils.validation`, `services.email`
```

### Example 3: Pre-Feature Refactoring Advice

**You ask**: "I want to add authentication to https://github.com/myorg/myapp"

**I respond**:
```
Before adding auth, decouple the `handlers.user` module — it already
has an SDP violation and high complexity that will get worse when auth
logic lands there. Extract validation first.

---

# Pre-feature decoupling advice
_Feature: add authentication_

Address these **before** writing the feature — each one would amplify
spaghetti otherwise:

1. **`handlers.user`** (Ca=3, Ce=6, I=0.67, CC=14)
   - Stable Dependencies Principle violation: an unstable module is
     depended on by a stable one.
   - Cyclomatic complexity hotspot.
2. **`models.user`** (Ca=5, Ce=2, I=0.29, CC=11)
   - Cyclomatic complexity hotspot.
```

### Example 4: Conversational Help

**You ask**: "What can you do?"

**I respond**:
```
Hey! I'm KevinKowalski — I analyze Python repo architectures. Share a
GitHub URL and I can map out coupling, cohesion, complexity, and
structural violations. I can also advise on what to refactor before you
build a new feature, or verify that your recent changes improved things.

Try: "Analyze https://github.com/pallets/flask"
```

## Metrics I Track

| Metric | What It Means |
|---|---|
| **Ca** (Afferent Coupling) | How many modules depend on this one |
| **Ce** (Efferent Coupling) | How many modules this one depends on |
| **Instability (I)** | Ce / (Ca + Ce) — 0.0 is maximally stable, 1.0 is maximally unstable |
| **LCOM4** | Lack of Cohesion of Methods — 1 means cohesive, >1 means the module should be split |
| **CC** (Cyclomatic Complexity) | Worst-case function complexity — higher means harder to reason about |

## Violations I Detect

| Rule | Description |
|---|---|
| **SDP** | Stable Dependencies Principle: a stable module depends on an unstable one |
| **GOD_MODULE** | Too many responsibilities — high LCOM and high coupling |
| **HIGH_CC** | Cyclomatic complexity hotspot — at least one function is hard to test |
| **CYCLE** | Import cycle — modules transitively depend on each other |

## Setup

```bash
# Install uv (if you don't have it)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Clone and install
git clone https://github.com/JBarmada/KevinKowalski.git
cd KevinKowalski
uv sync
```

## Running

**MCP Server** (for Claude Code / Claude Desktop / Devin):
```bash
uv run python mcp/mcp_server.py
```

**Agentverse Agent** (for ASI:One chat):
```bash
export ASI_ONE_API_KEY=<your-key>
uv run python mcp/agentverse_agent.py
```

**Tests:**
```bash
uv run pytest
```
