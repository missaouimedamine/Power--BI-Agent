"""Data source descriptors."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

_EXTENSION_TYPES = {
    ".csv": "csv",
    ".tsv": "csv",
    ".txt": "csv",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".xls": "excel",
    ".parquet": "parquet",
    ".pq": "parquet",
}


class DataSourceType(StrEnum):
    CSV = "csv"
    EXCEL = "excel"
    PARQUET = "parquet"
    SQL = "sql"


class DataSource(BaseModel):
    """Where a dataset comes from.

    SQL sources reference a connection string by *environment variable name*
    (``connection_env``) so credentials never end up in specs, logs or generated files.
    """

    name: str
    type: DataSourceType
    path: Path | None = None
    sheet: str | None = None
    delimiter: str | None = None
    encoding: str = "utf-8"
    query: str | None = None
    connection_env: str | None = Field(
        default=None, description="Env var holding the SQLAlchemy URL (SQL sources only)."
    )

    @model_validator(mode="after")
    def _check_consistency(self) -> DataSource:
        if self.type is DataSourceType.SQL:
            if not self.connection_env or not self.query:
                raise ValueError("SQL sources need 'connection_env' and 'query'")
        elif self.path is None:
            raise ValueError(f"{self.type} sources need a 'path'")
        if self.sheet is not None and self.type is not DataSourceType.EXCEL:
            raise ValueError("'sheet' is only valid for Excel sources")
        return self

    @classmethod
    def from_path(cls, path: Path | str, *, sheet: str | None = None) -> DataSource:
        """Infer the source type from the file extension."""
        p = Path(path)
        kind = _EXTENSION_TYPES.get(p.suffix.lower())
        if kind is None:
            raise ValueError(f"unsupported file type: {p.suffix or '<none>'} ({p.name})")
        delimiter = "\t" if p.suffix.lower() == ".tsv" else None
        return cls(name=p.stem, type=DataSourceType(kind), path=p, sheet=sheet, delimiter=delimiter)
