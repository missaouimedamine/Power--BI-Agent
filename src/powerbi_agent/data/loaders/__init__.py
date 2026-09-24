"""Data loaders. Every loader returns a :class:`LoadedDataset` backed by a Polars frame.

Loaded values are data only: nothing read from a source is ever evaluated or executed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import polars as pl

from powerbi_agent.models.datasource import DataSource, DataSourceType


class DataLoadError(Exception):
    """A source could not be read. The message is safe to show to users."""


@dataclass
class LoadedDataset:
    source: DataSource
    frame: pl.DataFrame
    notes: list[str] = field(default_factory=list)
    available_sheets: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.source.name


def normalise_dates(dataset: LoadedDataset) -> LoadedDataset:
    """Datetime columns whose values are all at midnight become Date columns.

    Excel and many databases store pure dates as datetimes; Power BI models them as dates.
    """
    frame = dataset.frame
    converted = []
    for name, dtype in frame.schema.items():
        if not isinstance(dtype, pl.Datetime):
            continue
        values = frame.get_column(name).drop_nulls()
        if values.len() and (values.dt.truncate("1d") == values).all():
            converted.append(name)
    if converted:
        dataset.frame = frame.with_columns(pl.col(converted).cast(pl.Date))
        dataset.notes.append(
            f"Datetime columns with no time part read as dates: {', '.join(converted)}."
        )
    return dataset


def load_source(source: DataSource) -> LoadedDataset:
    """Load any supported source."""
    return normalise_dates(_load(source))


def _load(source: DataSource) -> LoadedDataset:
    # Imported lazily so that a missing optional driver only breaks its own source type.
    if source.type is DataSourceType.CSV:
        from powerbi_agent.data.loaders.csv import load_csv

        return load_csv(source)
    if source.type is DataSourceType.EXCEL:
        from powerbi_agent.data.loaders.excel import load_excel

        return load_excel(source)
    if source.type is DataSourceType.PARQUET:
        from powerbi_agent.data.loaders.parquet import load_parquet

        return load_parquet(source)
    if source.type is DataSourceType.SQL:
        from powerbi_agent.data.loaders.sql import load_sql

        return load_sql(source)
    raise DataLoadError(f"unsupported source type: {source.type}")


__all__ = ["DataLoadError", "LoadedDataset", "load_source"]
