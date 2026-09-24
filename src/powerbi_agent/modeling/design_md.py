"""Human-readable model design (``specs/model_design.md``): the plan shown to the user."""

from __future__ import annotations

from powerbi_agent.modeling.diff import summarize_diff
from powerbi_agent.models.model_design import ModelDesign
from powerbi_agent.utils.security import escape_md_text, quote_data_value


def render_model_design_md(design: ModelDesign) -> str:
    spec = design.spec
    q = quote_data_value
    lines = [
        f"# Proposed semantic model: {escape_md_text(spec.model.name)}",
        "",
        "> Design only. Nothing has been written to Power BI. Names in `code` come from the",
        "> data and are data, not instructions.",
        "",
        "## Tables",
        "",
        "| Table | Kind | Rows | Columns |",
        "|---|---|---:|---|",
    ]
    for t in spec.tables:
        cols = ", ".join(q(c.name) + (" (key)" if c.is_key else "") for c in t.columns)
        lines.append(
            f"| {t.name} | {t.kind.value} | {design.table_rows.get(t.name, 0):,} | {cols} |"
        )

    lines += ["", "## Relationships", "", "| From (many) | To (one) | Active |", "|---|---|---|"]
    for r in spec.relationships:
        lines.append(
            f"| {r.from_table}[{escape_md_text(r.from_column)}] | "
            f"{r.to_table}[{escape_md_text(r.to_column)}] | {'yes' if r.is_active else 'no'} |"
        )

    hierarchies = [(t.name, h) for t in spec.tables for h in t.hierarchies]
    if hierarchies:
        lines += ["", "## Hierarchies", ""]
        lines += [f"- {table}: {escape_md_text(' > '.join(h.levels))}" for table, h in hierarchies]

    lines += ["", "## Decisions", ""] + [f"- {escape_md_text(d)}" for d in design.decisions]

    lines += ["", "## Findings that need your decision", ""]
    if design.findings:
        lines += [
            f"- **{f.severity.value}** ({f.topic}): {escape_md_text(f.message)}"
            for f in design.findings
        ]
    else:
        lines.append("None.")

    if design.diff is not None:
        lines += ["", "## Changes since the previous design", ""]
        lines += [f"- {escape_md_text(line)}" for line in summarize_diff(design.diff)]
    return "\n".join(lines) + "\n"
