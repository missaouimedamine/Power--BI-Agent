# Power BI integration

## Strategy

**PBIP + TMDL + Git + Power BI Modeling MCP.** No mouse/keyboard automation of Power BI
Desktop. Every model change should produce a reviewable Git diff.

## Verified facts (checked 2026-09-24)

| Fact | Source |
|---|---|
| PBIP layout: `<Name>.pbip`, `<Name>.SemanticModel/`, `<Name>.Report/`, `.gitignore` | [PBIP overview](https://learn.microsoft.com/en-us/power-bi/developer/projects/projects-overview) |
| The project file is **`.pbip`** and is optional (it's a shortcut to the report folder) | same |
| **PBIP is in preview** and must be enabled in Desktop preview features | same |
| Default `.gitignore`: `**/.pbi/localSettings.json`, `**/.pbi/cache.abf` | same |
| Files must be UTF-8 **without BOM**; Desktop writes CRLF line endings | same |
| `report.json`, `mobileState.json`, `diagramLayout.json`, `semanticModelDiagramLayout.json` schemas are undocumented; external edits unsupported | same |
| Deploy via Fabric Git integration, Fabric REST APIs, or Desktop publish | same |
| `definition.pbism` and `definition.pbir` (`datasetReference.byPath` relative path) formats | installed `powerbi-authoring` plugin v0.3.10, `pbip.md` |
| MCP server `@microsoft/powerbi-modeling-mcp`, run with `npx -y ...@latest --start` | [microsoft/powerbi-modeling-mcp](https://github.com/microsoft/powerbi-modeling-mcp) README, plugin `.mcp.json` |
| MCP targets: Power BI Desktop, Fabric workspace models, PBIP/TMDL folders | README |
| MCP tools: `connection_operations`, `database_operations`, `transaction_operations`, `model_operations`, `table_operations`, `column_operations`, `measure_operations`, `relationship_operations`, `dax_query_operations`, `trace_operations`, `partition_operations`, `user_hierarchy_operations`, `calculation_group_operations`, `security_role_operations`, `perspective_operations`, `named_expression_operations`, `function_operations`, `culture_operations`, `object_translation_operations`, `calendar_operations`, `query_group_operations` | README |
| Writes are off by default; `--readwrite` enables them with per-database confirmation; `--readonly` forbids them | README |
| MCP **cannot** modify reports or diagram layouts; needs Write permission and XMLA endpoint access | README |
| Auth: interactive (Azure Identity) or service principal via `AZURE_*` env vars | README |

## Not verified yet

- **MCP tool argument schemas.** The server timed out when connecting in the Phase 1
  environment, so `tools/list` was never inspected. Phase 5 starts by recording the live
  schemas as fixtures (`tests/fixtures/mcp/`) and building the adapter from those.
- OpenCode's handling of this project's `opencode.json` (OpenCode not installed).
- PBIR visual JSON schemas for the report renderer (Phase 7). The installed
  `powerbi-report-authoring` skill references a `powerbi-report-author` CLI worth
  evaluating as the renderer backend before hand-writing PBIR.

## Capability map

| Need | Mechanism | Phase |
|---|---|---|
| Inspect existing model | MCP `*_operations` list/get; fallback: parse TMDL; or DAX `INFO.*` queries | 5 |
| Create/modify tables, columns, measures, relationships | MCP (inside a transaction) **or** render TMDL from `SemanticModelSpec` | 5 / 6 |
| Execute/validate DAX | MCP `dax_query_operations` | 5 |
| Generate PBIP project | local renderer (`powerbi/pbip.py`, `powerbi/tmdl.py`) | 6 |
| Report pages/visuals | `ReportSpec` → PBIR renderer. **Not available through MCP.** | 7 |
| Publish / refresh | Fabric REST (`az rest` or HTTP), service principal | 8 |

## Safety rules applied to Power BI

- Inspect before modify; reuse existing objects; avoid duplicate measures.
- Deletes, renames, refreshes and publishes are `Risk.DESTRUCTIVE` / `Risk.EXTERNAL`
  and need explicit confirmation.
- Group multi-object MCP changes in a transaction so they can be rolled back.
- `git status` before a change, `git diff` after, and a change summary for the user.
- Never publish automatically. `publish` is hard-disabled until Phase 8.
