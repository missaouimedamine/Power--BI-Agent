"""Static analysis charts (PNG plus a CSV table view of the same data).

Optional: needs the ``viz`` extra (matplotlib). Without it, no charts are produced and
the caller records a note. Charts show one series each, so the title names the series
and there is no legend.
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

import polars as pl

from powerbi_agent.data.analyzer import (
    BusinessColumns,
    breakdown,
    breakdown_dimensions,
    monthly_totals,
)
from powerbi_agent.models.analysis import DatasetProfile, DatasetSchema

if TYPE_CHECKING:
    from powerbi_agent.tools.filesystem import Workspace

# Reference data-viz palette, light mode (single-series slot 1 + neutral ink).
SURFACE = "#fcfcfb"
SERIES = "#2a78d6"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"
TOP_N = 10


def matplotlib_available() -> bool:
    try:
        import matplotlib  # noqa: F401
    except ImportError:
        return False
    return True


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_") or "chart"


def _figure(title: str):  # type: ignore[no-untyped-def]
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    fig, ax = plt.subplots(figsize=(9, 4.5), dpi=120)
    fig.patch.set_facecolor(SURFACE)
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=TEXT_PRIMARY, fontsize=12, pad=12)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, labelsize=9, length=0)
    ax.grid(color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    return fig, ax


def _save(fig, workspace: Workspace, name: str) -> str:  # type: ignore[no-untyped-def]
    import matplotlib.pyplot as plt

    buffer = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buffer, format="png", facecolor=SURFACE)
    plt.close(fig)
    return workspace.relative(
        workspace.write_bytes(f"analysis/charts/{name}.png", buffer.getvalue())
    )


def _save_table(frame: pl.DataFrame, workspace: Workspace, name: str) -> str:
    return workspace.relative(
        workspace.write_text(f"analysis/charts/{name}.csv", frame.write_csv())
    )


def render_charts(
    frame: pl.DataFrame,
    profile: DatasetProfile,
    schema: DatasetSchema,
    cols: BusinessColumns,
    workspace: Workspace,
) -> list[str]:
    """Write charts under ``analysis/charts/`` and return their workspace-relative paths."""
    if not matplotlib_available():
        return []
    paths: list[str] = []
    measure = cols.primary_measure
    what = measure or "Rows"

    if cols.date:
        monthly = monthly_totals(frame, cols.date, measure)
        if monthly.height >= 2:
            fig, ax = _figure(f"{what} by month")
            ax.plot(monthly["month"].to_list(), monthly["value"].to_list(), color=SERIES, lw=2)
            ax.grid(axis="x", visible=False)
            ax.set_ylim(bottom=0)
            ax.yaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
            name = f"trend_{_slug(what)}"
            paths += [_save(fig, workspace, name), _save_table(monthly, workspace, name)]

    for dim in breakdown_dimensions(profile, schema):
        table = breakdown(frame, dim, measure).head(TOP_N)
        labels = [str(c) if c is not None else "(blank)" for c in table["category"].to_list()]
        values = table["value"].to_list()
        fig, ax = _figure(f"{what} by {dim}" + (f" (top {TOP_N})" if len(labels) == TOP_N else ""))
        ax.barh(labels[::-1], values[::-1], color=SERIES, height=0.6)
        ax.grid(axis="y", visible=False)
        ax.xaxis.set_major_formatter(lambda v, _: f"{v:,.0f}")
        # Direct label on the largest bar only; the CSV carries every value.
        ax.annotate(
            f"{values[0]:,.0f}",
            (values[0], len(values) - 1),
            xytext=(4, 0),
            textcoords="offset points",
            va="center",
            color=TEXT_SECONDARY,
            fontsize=9,
        )
        name = f"breakdown_{_slug(dim)}"
        paths += [_save(fig, workspace, name), _save_table(table, workspace, name)]

    missing = [(c.name, c.null_count) for c in profile.column_profiles if c.null_count]
    if missing:
        missing.sort(key=lambda x: x[1])
        fig, ax = _figure("Missing values by column")
        ax.barh([m[0] for m in missing], [m[1] for m in missing], color=SERIES, height=0.6)
        ax.grid(axis="y", visible=False)
        table = pl.DataFrame(missing, schema=["column", "missing"], orient="row")
        paths += [
            _save(fig, workspace, "missing_values"),
            _save_table(table, workspace, "missing_values"),
        ]
    return paths
