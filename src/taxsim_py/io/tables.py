"""Read and write tables by file extension.

CSV, TSV, Parquet, Arrow IPC (Feather) and JSON lines are always available.
Stata, SAS and SPSS files need the optional `readstat` extra
(`pip install taxsim-py[readstat]`), which installs polars-readstat.
"""

from pathlib import Path

import polars as pl

_DELIMITED = {"csv": ",", "tsv": "\t"}
_READ_FORMATS = {
    "csv": "csv", "tsv": "tsv", "txt": "csv",
    "parquet": "parquet", "pq": "parquet",
    "arrow": "ipc", "feather": "ipc", "ipc": "ipc",
    "ndjson": "ndjson", "jsonl": "ndjson",
    "dta": "readstat", "sas7bdat": "readstat", "sav": "readstat", "zsav": "readstat",
}
_WRITE_FORMATS = {**{k: v for k, v in _READ_FORMATS.items() if v != "readstat"}, "dta": "readstat", "sav": "readstat", "zsav": "readstat"}


def _format(path: str | Path, explicit: str | None, formats: dict[str, str], verb: str) -> str:
    key = (explicit or Path(path).suffix.lstrip(".")).lower()
    if key not in formats:
        supported = ", ".join(sorted(formats))
        raise ValueError(f"Cannot {verb} {str(path)!r}: unknown file type {key!r} (supported: {supported})")
    return formats[key]


def _readstat():
    try:
        import polars_readstat
    except ImportError as error:
        raise ImportError(
            "Stata, SAS and SPSS files need polars-readstat: pip install 'taxsim-py[readstat]'"
        ) from error
    return polars_readstat


def read_table(path: str | Path, file_format: str | None = None) -> pl.DataFrame:
    """Read a table, choosing the reader from the file extension (or `file_format`)."""
    kind = _format(path, file_format, _READ_FORMATS, "read")
    if kind in ("csv", "tsv"):
        key = (file_format or Path(path).suffix.lstrip(".")).lower()
        return pl.read_csv(path, separator=_DELIMITED.get(key, ","), infer_schema_length=10_000)
    if kind == "parquet":
        return pl.read_parquet(path)
    if kind == "ipc":
        return pl.read_ipc(path)
    if kind == "ndjson":
        return pl.read_ndjson(path)
    return _readstat().read_readstat(path)


def write_table(frame: pl.DataFrame, path: str | Path, file_format: str | None = None) -> None:
    """Write a table, choosing the writer from the file extension (or `file_format`)."""
    kind = _format(path, file_format, _WRITE_FORMATS, "write")
    if kind in ("csv", "tsv"):
        key = (file_format or Path(path).suffix.lstrip(".")).lower()
        frame.write_csv(path, separator=_DELIMITED.get(key, ","))
    elif kind == "parquet":
        frame.write_parquet(path)
    elif kind == "ipc":
        frame.write_ipc(path)
    elif kind == "ndjson":
        frame.write_ndjson(path)
    else:
        _readstat().write_readstat(frame, path, format=(file_format or Path(path).suffix.lstrip(".")).lower())
