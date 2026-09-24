"""Excel loader.

Uses the ``openpyxl`` engine explicitly (Polars' default ``calamine`` engine needs the
optional ``fastexcel`` package). Workbooks are opened read-only; formulas are read as
their cached values and never evaluated. Without an explicit sheet, the first sheet is
loaded and the others are listed in a note.
"""

from __future__ import annotations

import zipfile

import openpyxl
import polars as pl

from powerbi_agent.data.loaders import DataLoadError, LoadedDataset
from powerbi_agent.models.datasource import DataSource


def list_sheets(source: DataSource) -> list[str]:
    assert source.path is not None
    try:
        workbook = openpyxl.load_workbook(source.path, read_only=True, data_only=True)
    except (OSError, zipfile.BadZipFile, KeyError, ValueError) as exc:
        raise DataLoadError(f"cannot open workbook {source.path.name}: {exc}") from exc
    try:
        return list(workbook.sheetnames)
    finally:
        workbook.close()


def load_excel(source: DataSource) -> LoadedDataset:
    assert source.path is not None
    sheets = list_sheets(source)
    if not sheets:
        raise DataLoadError(f"{source.path.name} has no sheets")
    notes: list[str] = []
    sheet = source.sheet
    if sheet is None:
        sheet = sheets[0]
        if len(sheets) > 1:
            others = ", ".join(sheets[1:])
            notes.append(f"Loaded sheet '{sheet}'. Other sheets not analysed: {others}.")
    elif sheet not in sheets:
        raise DataLoadError(f"sheet '{sheet}' not found; available: {', '.join(sheets)}")

    try:
        frame = pl.read_excel(
            source.path, sheet_name=sheet, engine="openpyxl", infer_schema_length=10_000
        )
    except (pl.exceptions.PolarsError, ValueError, OSError) as exc:
        raise DataLoadError(f"cannot read sheet '{sheet}' of {source.path.name}: {exc}") from exc

    source = source.model_copy(update={"sheet": sheet})
    return LoadedDataset(source=source, frame=frame, notes=notes, available_sheets=sheets)
