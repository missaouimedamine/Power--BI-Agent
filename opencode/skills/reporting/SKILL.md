---
name: reporting
description: Design a Power BI report as a ReportSpec (pages, visuals, slicers, filters, drill-through, layout, accessibility) bound to an existing semantic model.
---

# Report specification

## Output: `ReportSpec` (see `src/powerbi_agent/models/report.py`)

```json
{
  "name": "Sales Analytics",
  "semantic_model": "Sales",
  "pages": [
    {
      "name": "Executive Overview",
      "visuals": [
        {"id": "kpi_revenue", "type": "kpi", "values": "[Total Revenue]", "title": "Revenue"},
        {"id": "rev_trend", "type": "line", "category": "DimDate[Month]",
         "values": ["[Total Revenue]"], "title": "Revenue by month"}
      ]
    }
  ]
}
```

## Design rules

- Question to visual: trend = line, comparison/ranking = bar, part-to-whole (5 slices or
  fewer) = donut/stacked bar, single value = card/KPI, detail = table/matrix,
  two measures = scatter.
- Page 1 is an executive overview: KPIs in the top row, one trend, one or two breakdowns.
- One page per analysis area (regional, product, customer), with drill-through to detail.
- Every visual has a title and a description (alt text). Colour is never the only carrier
  of meaning.
- Slicers: a date range plus 1-3 low-cardinality dimensions.

## Rendering

The spec is rendered to PBIR by `powerbi_agent.powerbi.report` (Phase 7). No layout API
is assumed; if PBIR can't express something, the renderer documents the gap.
