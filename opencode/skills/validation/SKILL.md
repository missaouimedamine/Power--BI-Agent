---
name: validation
description: Validate data, semantic models, DAX measures and report specifications and return structured findings. Use after every generated artifact and before any publish.
---

# Validation

## Finding format

```json
{"severity": "error|warning|info", "check": "<check id>", "object": "<Table[Column] or measure>",
 "message": "...", "status": "passed|failed|not_run"}
```

## Required checks

| Area | Checks |
|---|---|
| Data | row counts, null %, duplicates, types, referential integrity, unexpected values |
| Model | endpoints exist, key types compatible, cardinality matches data, orphan keys, ambiguous paths, naming |
| DAX | syntax, references, DIVIDE for ratios, filter context, expected output |
| Report | fields/measures exist, types fit visual, duplicate visuals, filter usability, naming |

## Loop

Generate, check, then accept, or send back for regeneration with the findings. Never mark
a check `passed` if it did not run. Any `error` blocks publishing.
