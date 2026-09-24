"""CSV/TSV loader (Polars).

- Delimiter: taken from the source, else sniffed from the first 64 KiB (``, ; \\t |``).
- Encoding: UTF-8 (a BOM is stripped). If the file is not valid UTF-8 and no encoding
  was given, it falls back to cp1252 and records a note. Nothing is guessed silently.
- Null tokens: empty field, ``NULL``, ``null``, ``#N/A``, ``N/A``. ``NA`` is *not* treated
  as null because it is a legitimate value (e.g. a region code).
"""

from __future__ import annotations

import csv
import io

import polars as pl

from powerbi_agent.data.loaders import DataLoadError, LoadedDataset
from powerbi_agent.models.datasource import DataSource

NULL_TOKENS = ["", "NULL", "null", "#N/A", "N/A"]
_SNIFF_BYTES = 64 * 1024
_UTF8 = {"utf-8", "utf8", "utf-8-sig"}


def sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ","


def _decode(raw: bytes, encoding: str, notes: list[str]) -> str:
    if encoding.lower() not in _UTF8:
        try:
            return raw.decode(encoding)
        except (UnicodeDecodeError, LookupError) as exc:
            raise DataLoadError(f"cannot decode file as {encoding}: {exc}") from exc
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        notes.append("File is not valid UTF-8; decoded as cp1252. Verify special characters.")
        return raw.decode("cp1252", errors="replace")


def load_csv(source: DataSource) -> LoadedDataset:
    assert source.path is not None
    try:
        raw = source.path.read_bytes()
    except OSError as exc:
        raise DataLoadError(f"cannot read {source.path.name}: {exc.strerror}") from exc

    notes: list[str] = []
    text = _decode(raw, source.encoding, notes)
    delimiter = source.delimiter or sniff_delimiter(text[:_SNIFF_BYTES])
    if source.delimiter is None and delimiter != ",":
        notes.append(f"Detected delimiter {delimiter!r}.")

    try:
        frame = pl.read_csv(
            io.BytesIO(text.encode("utf-8")),
            separator=delimiter,
            null_values=NULL_TOKENS,
            try_parse_dates=True,
            infer_schema_length=10_000,
        )
    except (pl.exceptions.PolarsError, ValueError) as exc:
        raise DataLoadError(f"cannot parse {source.path.name} as CSV: {exc}") from exc
    return LoadedDataset(source=source, frame=frame, notes=notes)
