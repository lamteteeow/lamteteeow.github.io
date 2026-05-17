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
