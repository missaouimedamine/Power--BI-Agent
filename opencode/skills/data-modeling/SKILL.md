---
name: data-modeling
description: Design a Power BI star schema (facts, dimensions, date table, relationships, hierarchies) as a SemanticModelSpec before any Power BI artifact is generated.
---

# Star-schema design

## Steps

1. **Grain first.** State the fact grain in one sentence.
2. **Facts** hold foreign keys plus additive numeric columns. No descriptive text.
3. **Dimensions**: one row per business entity with a unique key. Denormalise snowflakes
   into one dimension unless there is a strong reason not to.
4. **Date table**: always a dedicated date dimension spanning the full fact date range,
   marked as a date table. For role-playing dates, use one active relationship plus
   inactive ones, or separate date dimensions.
5. **Relationships**: many-to-one from fact to dimension, single-direction filter.
   Bidirectional or many-to-many need a written justification.
6. **Hierarchies**: natural drill paths (Year > Quarter > Month > Date,
   Category > Subcategory > Product).
7. Hide key columns and raw numeric columns that are exposed through measures.

## Output: `SemanticModelSpec` (see `src/powerbi_agent/models/semantic_model.py`)

```json
{
  "model": {"name": "Sales Analytics"},
  "tables": [
    {"name": "FactSales", "kind": "fact", "columns": []},
    {"name": "DimDate", "kind": "date", "columns": []}
  ],
  "relationships": [
    {"from_table": "FactSales", "from_column": "DateKey",
     "to_table": "DimDate", "to_column": "DateKey"}
  ],
  "measures": []
}
```

## How `design-model` decides (so you can explain or challenge it)

- Grain: smallest unique combination of keys, line/sequence numbers and dates; it must
  contain a key. Exact duplicate rows are reported, never dropped.
- Dimension membership: attribute A goes to key K if at least 99% of rows agree with K's
  most frequent A (case/whitespace-insensitive). If several keys determine A, the key
  with the fewest values wins (Customer before Order).
- Geography that depends on the customer stays in DimCustomer (Region > Country >
  CustomerName). A separate DimRegion would need a RegionKey in the fact.
- `findings` in `specs/model_design.json` need the user's decision; relay them.

The spec model rejects unknown relationship endpoints, duplicate names (case-insensitive),
measures that shadow columns, and hierarchies over missing columns.
