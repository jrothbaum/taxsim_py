"""Calculate taxes for inputs too large to hold in memory, one slice at a time."""

from pathlib import Path

import polars as pl

from taxsim_py.api import calculate_taxes


def calculate_taxes_to_parquet(
    source: str | Path,
    destination: str | Path,
    *,
    chunk_rows: int = 250_000,
    **calculate_options,
) -> int:
    """Run `calculate_taxes` on a parquet file in slices and write each result to disk.

    Rows are independent, so each slice is calculated on its own and written to
    `destination/part-00000.parquet`, `part-00001.parquet`, and so on, in input
    order. Peak memory depends on `chunk_rows` (about 5 KB per row) and not on
    the file size. `calculate_options` are passed to `calculate_taxes`.
    Returns the number of rows written.
    """
    if chunk_rows < 1:
        raise ValueError("chunk_rows must be at least 1")
    directory = Path(destination)
    directory.mkdir(parents=True, exist_ok=True)
    scan = pl.scan_parquet(source)
    total = scan.select(pl.len()).collect().item()
    for part, offset in enumerate(range(0, total, chunk_rows)):
        chunk = scan.slice(offset, chunk_rows).collect()
        calculate_taxes(chunk, **calculate_options).write_parquet(directory / f"part-{part:05d}.parquet")
    return total
