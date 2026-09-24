---
name: dax
description: Write, reuse and validate DAX measures (and, only when justified, calculated columns/tables) for a Power BI semantic model.
---

# DAX

## Conventions

- Measures over calculated columns. Justify every calculated column or table.
- Base measures first (`Total Revenue = SUM ( Sales[Revenue] )`), then build on them.
- `DIVIDE ( a, b )` for every ratio. Don't use `/` on measures.
- Qualify columns (`Sales[Revenue]`), never measures (`[Total Revenue]`).
- Variables (`VAR ... RETURN`) for repeated sub-expressions.
- Each measure has `format_string`, `description` and optionally `display_folder`.
- Time intelligence needs a marked date table.

## Standard KPI set (sales example)

```DAX
Total Revenue = SUM ( FactSales[Revenue] )
Total Cost = SUM ( FactSales[Cost] )
Total Profit = [Total Revenue] - [Total Cost]
Profit Margin = DIVIDE ( [Total Profit], [Total Revenue] )
Orders = DISTINCTCOUNT ( FactSales[OrderID] )
Average Order Value = DIVIDE ( [Total Revenue], [Orders] )
```

## Validation ladder (report which level was reached)

1. **Static**: brackets balanced, references resolve against the spec, ratios use DIVIDE.
2. **Executed**: `EVALUATE ROW ( "x", [Measure] )` against a live model through the MCP
   DAX query tool.
3. **Expected**: compared with an independently computed value (DuckDB/Polars) on the
   same data.
