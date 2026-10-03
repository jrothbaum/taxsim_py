"""Chunked parquet calculation gives the same rows as one in-memory call."""

import polars as pl
from polars.testing import assert_frame_equal

from taxsim_py import calculate_taxes, calculate_taxes_to_parquet


def test_chunked_parquet_matches_in_memory(tmp_path) -> None:
    rows = 23
    frame = pl.DataFrame(
        {
            "taxsimid": list(range(rows)),
            "year": [2023, 2024, 2025][0:1] * rows,
            "state": [5, 33, 6, 44][0:1] * rows,
            "mstat": [1 + i % 2 for i in range(rows)],
            "page": [40] * rows,
            "pwages": [10_000.0 * (i + 1) for i in range(rows)],
        }
    ).with_columns(state=pl.Series([[5, 33, 6, 44][i % 4] for i in range(rows)]), year=pl.Series([[2023, 2024, 2025][i % 3] for i in range(rows)]))
    source = tmp_path / "in.parquet"
    frame.write_parquet(source)

    written = calculate_taxes_to_parquet(source, tmp_path / "out", chunk_rows=5)
    chunked = pl.concat([pl.read_parquet(f) for f in sorted((tmp_path / "out").glob("part-*.parquet"))])

    assert written == rows
    assert len(list((tmp_path / "out").glob("part-*.parquet"))) == 5
    assert_frame_equal(chunked, calculate_taxes(frame), check_dtypes=False)
