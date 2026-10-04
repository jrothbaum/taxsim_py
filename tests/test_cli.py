"""The command line interface reads and writes tables by file extension."""

import importlib.util

import polars as pl
import pytest

from taxsim_py import calculate_taxes
from taxsim_py.cli import main
from taxsim_py.io.tables import read_table, write_table

HOUSEHOLDS = pl.DataFrame(
    {
        "year": [2021, 2024],
        "state": [5, 36],
        "mstat": [1, 2],
        "page": [45, 50],
        "pwages": [50_000.0, 80_000.0],
        "swages": [0.0, 40_000.0],
        "depx": [0, 2],
    }
)
HAS_READSTAT = importlib.util.find_spec("polars_readstat") is not None
BASIC_FORMATS = ["csv", "tsv", "parquet", "arrow", "ndjson"]
READSTAT_FORMATS = ["dta", "sav"]


def _expected() -> pl.DataFrame:
    return calculate_taxes(HOUSEHOLDS)


@pytest.mark.parametrize("input_format", BASIC_FORMATS)
@pytest.mark.parametrize("output_format", BASIC_FORMATS)
def test_every_basic_format_converts_to_every_other(tmp_path, input_format, output_format) -> None:
    source, target = tmp_path / f"in.{input_format}", tmp_path / f"out.{output_format}"
    write_table(HOUSEHOLDS, source)

    assert main([str(source), str(target)]) == 0

    result = read_table(target)
    expected = _expected()
    assert result.columns == expected.columns
    for column in ("fiitax", "siitax", "fica"):
        assert result.get_column(column).to_list() == pytest.approx(expected.get_column(column).to_list(), abs=0.01)


@pytest.mark.skipif(not HAS_READSTAT, reason="needs taxsim-py[readstat]")
@pytest.mark.parametrize("file_format", READSTAT_FORMATS)
def test_stata_and_spss_files_round_trip_through_the_cli(tmp_path, file_format) -> None:
    source, target = tmp_path / f"in.{file_format}", tmp_path / f"out.{file_format}"
    write_table(HOUSEHOLDS, source)

    assert main([str(source), str(target)]) == 0

    result = read_table(target)
    assert result.get_column("fiitax").to_list() == pytest.approx(_expected().get_column("fiitax").to_list(), abs=0.01)


def test_output_defaults_to_csv_on_standard_output(tmp_path, capsys) -> None:
    source = tmp_path / "in.parquet"
    write_table(HOUSEHOLDS, source)

    assert main([str(source)]) == 0

    assert capsys.readouterr().out.splitlines()[0].startswith("year,state,mstat")


def test_options_reach_calculate_taxes(tmp_path) -> None:
    source, target = tmp_path / "in.csv", tmp_path / "out.csv"
    write_table(HOUSEHOLDS.head(1), source)

    assert main([str(source), str(target), "--mode", "taxsim", "--batch-rows", "1"]) == 0

    expected = calculate_taxes(HOUSEHOLDS.head(1), calculation_mode="taxsim")
    assert read_table(target).get_column("siitax").to_list() == pytest.approx(expected.get_column("siitax").to_list())


def test_unknown_file_type_and_missing_readstat_are_clear_errors(tmp_path, capsys, monkeypatch) -> None:
    source = tmp_path / "in.csv"
    write_table(HOUSEHOLDS, source)
    assert main([str(source), str(tmp_path / "out.xyz")]) == 1
    assert "unknown file type" in capsys.readouterr().err

    monkeypatch.setitem(__import__("sys").modules, "polars_readstat", None)
    assert main([str(source), str(tmp_path / "out.dta")]) == 1
    assert "taxsim-py[readstat]" in capsys.readouterr().err


def test_year_after_the_last_implemented_year_is_reported(tmp_path, capsys) -> None:
    source = tmp_path / "in.csv"
    write_table(HOUSEHOLDS, source)

    assert main([str(source), str(tmp_path / "out.csv"), "--year", "2030"]) == 1
    assert "not implemented" in capsys.readouterr().err
