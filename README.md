# ai-powerbi-agent

An AI data-analyst agent platform that goes from a natural-language request and a dataset
(CSV, Excel, Parquet, SQL) to a validated Power BI semantic model, DAX measures and
report, built as **PBIP/TMDL + Git + the Power BI Modeling MCP**.

> **Status: Phases 1-3 complete.** Foundation (config, logging, models, planner and
> orchestrator with its safety gate, CLI), **data analysis** (CSV/Excel/Parquet/SQL
> loading, profiling, data quality, KPIs, insights, charts, validation) and **semantic
> modeling** (star-schema design, date table, hierarchies, model validation) work end
> to end. DAX, PBIP, report and publish are **not implemented yet**. Their steps report
> `not_implemented` (exit code 2) instead of pretending. See
> [docs/architecture.md](docs/architecture.md#implementation-status).

## Quick start

```bash
# 1. Install (Python 3.12+, uv)
make install                       # or see docs/setup.md for Windows / pip
cp .env.example .env

# 2. Check it works
powerbi-agent version
powerbi-agent config               # effective settings, secrets redacted

# 3. See what the agent would do for a request
powerbi-agent plan "Analyze sales.xlsx and create a Power BI sales dashboard"

# 4. Analyse the sample workbook (writes workspaces/sales/analysis/)
powerbi-agent analyze ./examples/sales/data/sales.xlsx
powerbi-agent design-model ./examples/sales/data/sales.xlsx   # proposes the star schema
powerbi-agent validate ./workspaces/sales     # data + model checks; report: not_implemented

# 5. Development checks
make check                         # ruff + mypy --strict + pytest
```

### Using it with OpenCode

```bash
export OPENCODE_CONFIG=$PWD/opencode/config/opencode.json
make opencode-install              # copy skills where OpenCode discovers them
opencode                           # pick the "orchestrator" agent
```

See [docs/setup.md](docs/setup.md) for model-provider and Power BI authentication.

## CLI

| Command | Purpose | Implemented in |
|---|---|---|
| `powerbi-agent plan "<request>"` | show the execution plan | ✅ Phase 1 |
| `powerbi-agent chat` | interactive session (Phase 1: planning only) | ✅ partial |
| `powerbi-agent config` / `version` | settings (redacted) / version | ✅ Phase 1 |
| `powerbi-agent profile <file>` | column profiling → `profile.json`, `schema.json` | ✅ Phase 2 |
| `powerbi-agent analyze <file>` | profile + quality + KPIs + insights + charts → `analysis/` | ✅ Phase 2 |
| `powerbi-agent design-model <file>` | star schema → `specs/`, `model_data/` (nothing sent to Power BI) | ✅ Phase 3 |
| `powerbi-agent generate-model <file>` | PBIP + TMDL project | Phases 4–6 |
| `powerbi-agent generate-report <file>` | model + report spec/PBIR | Phase 7 |
| `powerbi-agent validate <workspace>` | structured validation report | data ✅, model ✅; report Phase 7 |
| `powerbi-agent publish <workspace>` | publish after validation + confirmation | Phase 8 (disabled) |

All data commands accept `--workspace/-w` and `--json`; Excel sources accept `--sheet`.
SQL sources: `powerbi-agent analyze --query "SELECT ..." --connection-env SALES_DB_URL`
(the option takes the *name* of the environment variable holding the URL, never the URL).

## Output

```text
workspaces/<name>/
├── analysis/
│   ├── profile.json          rows, columns, missing cells, duplicates, per-column stats
│   ├── schema.json           logical types + suggested role (key, foreign_key, measure, ...) and why
│   ├── quality_report.json   issues with severity, affected rows and examples
│   ├── metrics.json          measure totals, date ranges, suggested KPIs with computed values
│   ├── insights.md           human-readable summary (data values quoted, never interpreted)
│   └── charts/               PNG + CSV table view per chart (needs the `viz` extra)
├── specs/
│   ├── semantic_model.json   the model IR (tables, columns, relationships, hierarchies)
│   ├── model_design.json     grain, decisions, findings, diff vs previous design
│   └── model_design.md       the proposal to review before anything touches Power BI
├── model_data/*.parquet      materialised fact and dimension tables
├── validation/data_validation.json   consistency + source re-check + DuckDB cross-check
├── validation/model_validation.json  keys, orphans, cardinality, date table, ambiguity
└── .agent/                   task log; previous versions of overwritten files in history/
```

## How it works

```text
request → planner (Plan of typed steps) → orchestrator
            ├─ read_only / local_write steps run directly
            └─ destructive / external steps need explicit per-step confirmation
          → specialised agents (data analyst, modeler, DAX, report, validator)
          → intermediate specs (SemanticModelSpec, ReportSpec)
          → renderers (TMDL/PBIP/PBIR) or Power BI Modeling MCP
          → validation → Git diff → (optional, confirmed) publish
```

- [Architecture](docs/architecture.md)
- [Agent workflow](docs/agent-workflow.md)
- [Power BI integration and verified API facts](docs/powerbi.md)
- [Security](docs/security.md)
- [Development](docs/development.md)

## Safety guarantees

- Nothing is published, deleted or overwritten without explicit confirmation.
  Publishing is also off by default (`ALLOW_PUBLISH=false`).
- A step is reported as succeeded only when its tool confirmed success.
- Secrets live in `.env`/environment only and are redacted from all logs and output.
- Data values are treated as data, never as instructions.
- The agent never runs `git push`.

## License

MIT. See [LICENSE](LICENSE).
