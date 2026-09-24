---
name: powerbi
description: Create and modify Power BI semantic models and PBIP projects via PBIP/TMDL files, Git and the Power BI Modeling MCP. Use for any Power BI artifact change.
---

# Power BI authoring

## Preferred toolchain

PBIP + TMDL + Git + Power BI Modeling MCP. Never automate Power BI Desktop with
mouse/keyboard.

## PBIP layout (see docs/powerbi.md for sources)

```text
<Name>.pbip                      # shortcut file (NOT .pbiproj)
<Name>.SemanticModel/
  definition.pbism
  definition/                    # TMDL: database.tmdl, model.tmdl, relationships.tmdl, tables/*.tmdl
<Name>.Report/
  definition.pbir                # datasetReference.byPath -> ../<Name>.SemanticModel
  definition/                    # PBIR
```

## MCP usage

- Server: `@microsoft/powerbi-modeling-mcp` (stdio via `npx`). It is disabled by default
  in `opencode/config/opencode.json`; enable it after completing docs/setup.md.
- **Discover tools at runtime.** Only call tools the server actually lists. Microsoft's
  README documents `*_operations` tools (`connection_operations`, `database_operations`,
  `table_operations`, `column_operations`, `measure_operations`,
  `relationship_operations`, `dax_query_operations`, `transaction_operations`, ...), but
  argument schemas must be read from the server, never guessed.
- The server is read-only unless started with `--readwrite` (which then prompts per
  database). Prefer `--readonly` for inspection-only sessions.
- The MCP cannot author report visuals or layout. Reports go through the ReportSpec to
  PBIR renderer.
- Use `transaction_operations` to group multi-object changes so they can be rolled back.
- While MCP is connected to a model, the live model is the source of truth; on-disk TMDL
  may be stale.

## Change safety

1. `git status` before changing anything; stop if there are unexpected changes.
2. Inspect the model; reuse existing objects.
3. Apply the change.
4. `git diff`; summarise additions, modifications and deletions.
5. Deletions, renames, publishing and refreshes require explicit confirmation.
