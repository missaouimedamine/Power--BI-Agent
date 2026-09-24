# Development

## Commands

```bash
make install          # venv + dev deps + pre-commit hooks
make check            # ruff lint + format check + mypy (strict) + pytest
make test-unit
make test-integration
make format
```

## Conventions

- Python 3.12, `from __future__ import annotations`, full type hints; `mypy --strict`
  must pass.
- Ruff (line length 100) for lint and format.
- Pydantic v2 models for every cross-module data contract, in `powerbi_agent.models`.
- Logs go to stderr as JSON; command output goes to stdout (so `--json` output can be piped).
- No module performs network or Power BI calls at import time.

## Adding a capability (how later phases plug in)

1. Implement the logic in its package (e.g. `data/profiler.py`), pure and testable.
2. Wrap it in a `BaseAgent` subclass in `powerbi_agent/agents/` declaring its
   `capabilities` (see `agents/data_analyst.py` for the pattern: one handler per
   capability, artifacts verified before `SUCCEEDED`).
3. Register it in `core/agent.py::default_registry()`.
4. Unit-test the logic; add an orchestrator-level test; update the integration test
   (`tests/integration/test_pipeline_scaffold.py`) and remove the relevant `xfail`.
5. Update the status table in `docs/architecture.md`.

An agent returns `SUCCEEDED` only after verifying its output exists and is valid.

## Sample data

`python examples/sales/generate_data.py` (or `make sample-data`) regenerates
`examples/sales/data/sales.xlsx` with a fixed seed and writes the planted defect counts
to `examples/sales/expected/defects.json`. Integration tests generate their own smaller
copy and assert that analysis finds exactly the planted defects.

## Testing policy

- Unit tests must not need network, a Power BI tenant, OpenCode or an LLM.
- Power BI MCP interactions are tested against recorded `tools/list` / tool-call
  responses stored under `tests/fixtures/mcp/` (Phase 5).
- Tests that need a real tenant use `@pytest.mark.powerbi` and are skipped in CI.
- `tests/conftest.py` isolates env vars and the working directory per test.
