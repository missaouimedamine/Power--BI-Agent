"""Name-level diff between two semantic model specs (what a change adds or removes)."""

from __future__ import annotations

from powerbi_agent.models.model_design import SpecDiff
from powerbi_agent.models.semantic_model import SemanticModelSpec


def _names(spec: SemanticModelSpec) -> dict[str, set[str]]:
    return {
        "tables": {t.name for t in spec.tables},
        "columns": {f"{t.name}[{c.name}]" for t in spec.tables for c in t.columns},
        "relationships": {r.key for r in spec.relationships},
        "hierarchies": {f"{t.name}/{h.name}" for t in spec.tables for h in t.hierarchies},
        "measures": {f"[{m.name}]" for m in spec.measures},
    }


def diff_specs(old: SemanticModelSpec | None, new: SemanticModelSpec) -> SpecDiff:
    before = _names(old) if old is not None else {k: set() for k in _names(new)}
    after = _names(new)
    dropped_tables = before["tables"] - after["tables"]

    def added(kind: str) -> list[str]:
        return sorted(after[kind] - before[kind])

    def removed(kind: str) -> list[str]:
        return sorted(before[kind] - after[kind])

    return SpecDiff(
        tables_added=added("tables"),
        tables_removed=removed("tables"),
        # Columns of added/removed tables are implied by the table change.
        columns_added=[
            c for c in added("columns") if c.split("[")[0] not in after["tables"] - before["tables"]
        ],
        columns_removed=[c for c in removed("columns") if c.split("[")[0] not in dropped_tables],
        relationships_added=added("relationships"),
        relationships_removed=removed("relationships"),
        hierarchies_added=added("hierarchies"),
        hierarchies_removed=removed("hierarchies"),
        measures_added=added("measures"),
        measures_removed=removed("measures"),
    )


def summarize_diff(diff: SpecDiff) -> list[str]:
    """Lines like ``+ 2 tables: DimCustomer, DimDate`` for the change summary."""
    lines = []
    for label, added, removed in [
        ("table", diff.tables_added, diff.tables_removed),
        ("column", diff.columns_added, diff.columns_removed),
        ("relationship", diff.relationships_added, diff.relationships_removed),
        ("hierarchy", diff.hierarchies_added, diff.hierarchies_removed),
        ("measure", diff.measures_added, diff.measures_removed),
    ]:
        for sign, items in (("+", added), ("-", removed)):
            if items:
                plural = label + ("s" if len(items) != 1 else "")
                plural = plural.replace("hierarchys", "hierarchies")
                shown = ", ".join(items[:6]) + (" ..." if len(items) > 6 else "")
                lines.append(f"{sign} {len(items)} {plural}: {shown}")
    if not lines:
        lines.append("No changes.")
    elif not diff.has_removals:
        lines.append("No objects removed.")
    return lines
