"""Parquet loader (Polars / PyArrow)."""

from __future__ import annotations

import polars as pl

from powerbi_agent.data.loaders import DataLoadError, LoadedDataset
from powerbi_agent.models.datasource import DataSource


def load_parquet(source: DataSource) -> LoadedDataset:
    assert source.path is not None
    try:
        frame = pl.read_parquet(source.path)
    except (pl.exceptions.PolarsError, OSError, ValueError) as exc:
        raise DataLoadError(f"cannot read {source.path.name} as Parquet: {exc}") from exc
    return LoadedDataset(source=source, frame=frame)
