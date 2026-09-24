# Architecture

## Goals

An extensible agent platform that turns a natural-language request plus a dataset into
a validated Power BI semantic model, DAX measures, a report and (on explicit request)
a deployment. It is a platform, not a script. It has three rules:

1. **Deterministic tools do the work; LLMs reason and orchestrate.** Every number,
   schema, diff and validation verdict comes from Python code or the Power BI engine.
2. **Intermediate specifications separate reasoning from implementation.**
   Business reasoning → `SemanticModelSpec` / `ReportSpec` → TMDL/PBIR/MCP.
3. **Safety by construction.** Risky steps are typed (`Risk`), and the orchestrator
   refuses to run them without confirmation. Missing capabilities are reported as
   `not_implemented`, never faked.

## Layers

```text
 ┌──────────────── OpenCode (LLM runtime) ─────────────────┐
 │ opencode/config/opencode.json   agents/*.md  skills/*    │
 │ orchestrator ─► data_analyst, data_engineer,             │
 │                 powerbi_modeler, dax_expert,             │
 │                 report_designer, validator               │
 └───────────────┬───────────────────────┬─────────────────┘
                 │ shell: powerbi-agent  │ MCP: powerbi-modeling
                 ▼                       ▼
 ┌─────────────── powerbi_agent (Python core) ─────────────┐
 │ cli.py             thin Typer front-end                  │
 │ core/              planner → orchestrator → registry     │
 │ agents/            data_analyst, validator (+ later)     │
 │                    events (observability), state         │
 │ models/            Pydantic contracts (the IR)           │
 │ data/              loaders, profiler, quality, analyzer  │
 │ modeling/          star-schema inference                 │
 │ dax/               generation, static validation         │
 │ powerbi/           TMDL/PBIP/PBIR renderers, MCP adapter │
 │ validation/        data/model/DAX/report validators      │
 │ tools/             workspace fs, locked duckdb, git, ... │
 │ utils/             config, logging, security             │
 └──────────────────────────────────────────────────────────┘
            (optional) FastAPI layer calls the same core
```

The Python core has no dependency on OpenCode, the LLM or FastAPI. The core works on
its own through the CLI, and the LLM layer can be replaced.

## Core components

### Models (`powerbi_agent.models`): the contracts

| Model | Purpose |
|---|---|
| `DataSource` | CSV/Excel/Parquet path or SQL query; SQL credentials referenced by env var **name** |
| `DatasetProfile`, `DatasetSchema`, `QualityReport`, `DatasetMetrics`, `AnalysisResult` | `analysis/*.json` outputs |
| `ValidationReport`, `ValidationFinding` | validator output; a check that could not run is `not_run`, never `passed` |
| `SemanticModelSpec` (`ModelInfo`, `TableSpec`, `ColumnSpec`, `RelationshipSpec`, `HierarchySpec`) | model IR, validated for referential consistency |
| `MeasureSpec`, `CalculatedColumnSpec`, `DaxValidationResult` | DAX IR; `executed_against_model` records whether a live engine ran it |
| `ReportSpec`, `PageSpec`, `VisualSpec`, `FieldRef` | report IR; `FieldRef` parses `Table[Column]` / `[Measure]` |
| `Plan`, `PlanStep`, `Task`, `StepResult`, `Risk`, `Capability` | orchestration |

### Planner (`core/planner.py`)

`Planner` is a protocol. The project ships `RuleBasedPlanner`: keyword intent detection plus
fixed per-intent step templates. An LLM planner can replace it later. Its output must still
parse into `Plan`, so capabilities and risks always come from a closed set.

### Orchestrator (`core/orchestrator.py`)

Runs plan steps in dependency order:

- finds the agent registered for each step's `Capability` (`AgentRegistry`);
- steps with `Risk.DESTRUCTIVE` / `Risk.EXTERNAL` call the confirmation callback; no
  callback or a "no" → `CANCELLED`;
- no registered agent → `NOT_IMPLEMENTED`; exception → `FAILED` (error redacted);
  dependants of a non-successful step → `SKIPPED`;
- the task is `SUCCEEDED` only if every step succeeded (an empty plan is `SKIPPED`);
- emits events on the `EventBus` and persists the task in `<workspace>/.agent/tasks/`.

### Agents (`core/agent.py`, `agents/`)

`BaseAgent` subclasses declare `name` and `capabilities` and implement
`run(step, context) -> StepResult`. `AgentContext` carries settings, the workspace, the
JSON outputs of earlier steps and a `scratch` dict for in-memory objects such as loaded
DataFrames, which are never persisted. `default_registry()` is where each phase
registers its agents.

| Agent | Capabilities | Since |
|---|---|---|
| `DataAnalystAgent` | `load_data`, `profile_data`, `check_quality`, `generate_insights` | Phase 2 |
| `ValidatorAgent` | `validate_data` | Phase 2 |

An agent reports `SUCCEEDED` only after checking that the artifacts it lists exist.

### Data analysis pipeline (Phase 2)

```text
DataSource ─► loaders/ (csv, excel, parquet, sql) ─► LoadedDataset (Polars frame + notes)
           ─► profiler  ─► DatasetProfile  ─► schema ─► DatasetSchema (role + reason)
           ─► quality   ─► QualityReport   (8 deterministic checks)
           ─► analyzer  ─► DatasetMetrics  (KPIs with computed values) + insights
           ─► charts (optional), outputs (insights.md)
           ─► data_validator ─► ValidationReport (consistency, source re-check,
                                 DuckDB SQL cross-check of every measure total)
```

- **Loaders** never evaluate content. Excel uses openpyxl read-only with cached values,
  CSV nulls are an explicit token list, and SQL accepts one read-only statement with the
  URL taken from an environment variable. Datetimes that are always at midnight become
  dates.
- **Roles** come from explainable rules (id-like names, cardinality, type, name
  patterns). Measures are marked `additive`, so prices and rates are averaged, never summed.
- **KPIs** such as Total/Average, Profit, Profit Margin, Orders and Average Order Value
  are proposed from recognised column names, and each carries its value over the full
  dataset. These values are the ground truth that Phase 4/5 DAX validation compares
  against.
- **Insights** are templates filled with computed numbers, and each carries its evidence.
- **Writes** go through `tools/filesystem.Workspace`. Writes are atomic, UTF-8 without
  BOM, confined to the workspace, and the previous version of any file is copied to
  `.agent/history/<task_id>/` first.

### Observability

`utils/logging.py` emits JSON logs to stderr with `timestamp, level, task_id, agent,
tool, message` plus event fields (`action, status, duration_ms, error`). All payloads pass
through `utils/security.redact`.

### External services: adapters

Power BI access goes through adapters (`powerbi/mcp_client.py`, `powerbi/deployment.py`)
so the MCP server or REST API can be swapped out. MCP tool schemas are discovered at runtime
(see [powerbi.md](powerbi.md)).

## Output layout

```text
workspaces/<name>/
├── analysis/            profile.json, quality_report.json, schema.json, metrics.json, insights.md, charts/
├── specs/               semantic_model.json, report.json
├── <Name>.pbip
├── <Name>.SemanticModel/
├── <Name>.Report/
├── README.md
└── .agent/              state.json, tasks/<task_id>.json
```

> The original brief names the project file `Sales.pbiproj`. Microsoft's PBIP format uses
> **`Sales.pbip`**, and this project follows the real format.

## Implementation status

| Phase | Scope | Status |
|---|---|---|
| 1 | Structure, config, logging, CLI, models, agent interface, docs | **done** |
| 2 | Loaders (CSV/Excel/Parquet/SQL), profiling, schema roles, data quality, KPIs, insights, charts, data validation, DuckDB tool, sample data (`analyze`, `profile`, data part of `validate`) | **done** |
| 3 | Star-schema inference, relationships, orphan-key checks, model spec (`design-model`) | not started |
| 4 | DAX generation, static validation, measure library | not started |
| 5 | Power BI MCP adapter (inspect, modify, execute DAX) | not started |
| 6 | TMDL/PBIP rendering, git wrappers, diffs (`generate-model`) | not started |
| 7 | Report spec generation, PBIR rendering (`generate-report`) | not started |
| 8 | Auth, publish, refresh, deployment validation (`publish`) | not started; hard-disabled |
| – | Optional FastAPI service | not started |

Until a phase lands, its CLI commands run the plan and report `not_implemented` for the
missing steps (exit code 2).
