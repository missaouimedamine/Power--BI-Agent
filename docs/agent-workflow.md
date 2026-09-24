# Agent workflow

## End-to-end flow

```text
USER REQUEST
  → understand request            (orchestrator; planner → Plan)
  → inspect data                  LOAD_DATA           read_only
  → profile data                  PROFILE_DATA        local_write (profile.json, schema.json)
  → data-quality check            CHECK_QUALITY       local_write (quality_report.json)
  → KPIs / dimensions / insights  GENERATE_INSIGHTS   local_write (metrics.json, insights.md, charts/)
  → validate analysis             VALIDATE_DATA       local_write (validation/data_validation.json)
  → design star schema            DESIGN_STAR_SCHEMA  local_write (specs/, model_data/)
  → validate model                VALIDATE_MODEL      local_write (validation/model_validation.json)
  → generate DAX                  GENERATE_DAX        read_only   (MeasureSpec[])
  → validate DAX                  VALIDATE_DAX        read_only
  → generate PBIP/TMDL            GENERATE_PBIP       local_write (new files in workspace)
  → validate model                VALIDATE_MODEL      read_only
  → report spec                   DESIGN_REPORT       local_write (ReportSpec)
  → validate report               VALIDATE_REPORT     read_only
  → SHOW PLAN / FINDINGS TO USER
  → USER CONFIRMATION             required for destructive / external steps
  → apply / publish               PUBLISH             external
```

See the plan for any request without running anything:

```bash
powerbi-agent plan "Analyze sales.xlsx and create an executive sales dashboard"
```

## Intents and their plans

| Intent | Triggered by (keywords) | Steps |
|---|---|---|
| `profile` | profile | load, profile, validate data |
| `analyze` | analyze, insight, explore, quality | load, profile, quality, insights, validate data |
| `design_model` | star schema, data model | analysis + design star schema + validate model |
| `generate_dax` | dax, measure | analysis + design + DAX + validate DAX |
| `generate_model` | semantic model, pbip, tmdl | … + generate PBIP + validate model |
| `generate_report` | dashboard, report, visual, page | … + design report + validate report |
| `validate` | validate, verify | validate data, validate model, validate report |
| `publish` | publish, deploy | validate x3, **publish (confirm)** |

The most far-reaching intent mentioned wins ("analyze X and build a dashboard" →
`generate_report`). With no data file for a data intent, or an unknown intent, the plan
carries a note telling the orchestrator to ask the user.

## Confirmation policy

| Risk | Examples | Confirmation |
|---|---|---|
| `read_only` | profiling, validation, DAX queries | no |
| `local_write` | new files in `workspaces/<name>/` | no (Git records it) |
| `destructive` | overwriting files, deleting/renaming model objects | **yes, per step** |
| `external` | publish, refresh, any write to Power BI/Fabric | **yes, per step** |

The orchestrator enforces this in code: without a confirmation callback that returns
`True` for that specific step, the step is `CANCELLED`.

## Example conversation (target behaviour)

```text
User: Analyze sales.xlsx and create a Power BI sales dashboard with Revenue, Profit,
      Profit Margin, Orders, AOV by Date, Region, Product Category, Customer.

Agent: I found:
         1.2M sales records, 4 candidate dimensions, 17 potential metrics
       Proposed model: FactSales, DimDate, DimCustomer, DimProduct, DimRegion
       Proposed report: Executive Overview, Regional, Product, Customer Analysis
       Data-quality issues:
         - 1,240 missing customer IDs
         - 31 duplicate records
         - 17 invalid product categories
       I will not modify the Power BI model until you confirm.
```

Every figure above must come from `analysis/*.json`, not from the LLM.

## Failure handling

- A failing step is `FAILED` with a redacted error; everything depending on it is
  `SKIPPED`. The orchestrator reports the error and proposes a fix or asks the user.
- A missing implementation is `NOT_IMPLEMENTED`. It is reported as such and never
  described as done.
- Task records live in `workspaces/<name>/.agent/tasks/<task_id>.json`.

## CLI exit codes

| Code | Meaning |
|---|---|
| 0 | all steps succeeded |
| 1 | a step failed |
| 2 | incomplete: not implemented, cancelled, skipped, or publish disabled |
| 3 | bad input (missing file, unsupported type) |
