"""Relationship helpers: type mapping and data-level checks on key pairs.

Used both when designing (to accept/reject a relationship) and by the model validator.
"""

from __future__ import annotations

from dataclasses import dataclass

import polars as pl

from powerbi_agent.models.semantic_model import DataType


def tmdl_type(dtype: pl.DataType) -> DataType:
    if dtype.is_integer():
        return DataType.INT64
    if dtype.is_float():
        return DataType.DOUBLE
    if isinstance(dtype, pl.Decimal):
        return DataType.DECIMAL
    if dtype == pl.Boolean:
        return DataType.BOOLEAN
    if dtype == pl.Date or isinstance(dtype, pl.Datetime):
        return DataType.DATETIME
    return DataType.STRING


@dataclass
class KeyCheck:
    type_compatible: bool
    to_side_unique: bool
    to_side_nulls: int
    orphan_rows: int
    orphan_examples: list[object]
    null_from_rows: int

    @property
    def ok(self) -> bool:
        return (
            self.type_compatible
            and self.to_side_unique
            and self.to_side_nulls == 0
            and self.orphan_rows == 0
        )


def check_key_pair(many: pl.DataFrame, many_col: str, one: pl.DataFrame, one_col: str) -> KeyCheck:
    """Checks for a many-to-one relationship ``many[many_col] -> one[one_col]``."""
    fk, pk = many.get_column(many_col), one.get_column(one_col)
    compatible = tmdl_type(fk.dtype) == tmdl_type(pk.dtype)
    pk_values = pk.drop_nulls()
    unique = pk_values.n_unique() == pk_values.len()
    orphans = pl.Series(dtype=fk.dtype)
    if compatible:
        fk_values = fk.drop_nulls()
        orphans = fk_values.filter(~fk_values.is_in(pk_values.unique().implode()))
    return KeyCheck(
        type_compatible=compatible,
        to_side_unique=unique,
        to_side_nulls=pk.null_count(),
        orphan_rows=orphans.len(),
        orphan_examples=orphans.unique(maintain_order=True).head(5).to_list(),
        null_from_rows=fk.null_count(),
    )
