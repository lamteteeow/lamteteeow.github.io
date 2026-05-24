# AGENTS.md

## Quick commands
- `uv run fetch_data.py` — fetch FRED data and write `data/macro_risk.json`
- `uv sync` — install Python dependencies from lockfile
- `uv run pytest tests/ -v` — run test suite

## Architecture
- **Static GitHub Pages site**: single `index.html` (inline CSS/JS, Plotly via CDN). No build step.
- **Data pipeline**: `fetch_data.py` → `data/macro_risk.json` → `index.html` reads it at runtime.
- Python package manager is `uv` (see `pyproject.toml`, `uv.lock`).

## Key conventions
- Requires `FRED_API_KEY` env var (set in `.env` for local, GitHub secret for CI).
- `fetch_data.py` has fallback logic: if FRED API fails, it reuses cached data from `data/macro_risk.json` and **does not overwrite the file** to avoid empty git diffs.
- Only save `data/macro_risk.json` when at least one series was successfully fetched from FRED — if all fetches fail, the script prints "No new data fetched" and exits without writing.

## CI
- GitHub Actions runs twice daily (`cron: '0 6,18 * * *'`).
- `[skip ci]` in the auto-commit message prevents recursive CI runs.
- Workflow: checkout → install uv → run `uv run fetch_data.py` → commit & push `data/macro_risk.json`.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
| ------ | ---------- |
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
