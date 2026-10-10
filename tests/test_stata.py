"""The Stata command's calculation: taxsim35's inputs in, taxsim35's outputs out."""

import importlib.util

import polars as pl
import pytest

from taxsim_py.stata import (
    FULL_OUTPUTS,
    LABELS,
    OUTPUTS,
    StataInputError,
    calculate_for_stata,
    prepare_inputs,
    run_stata_files,
)

HAS_READSTAT = importlib.util.find_spec("polars_readstat") is not None
# NBER's taxsim35 help example: married, $100,000 of long-term gains in 1970.
NBER_EXAMPLE = pl.DataFrame({"state": [0], "year": [1970], "mstat": [2], "ltcg": [100_000.0]})


def test_nber_example_gives_nbers_answer() -> None:
    result = calculate_for_stata(NBER_EXAMPLE, mtr=14, full=True)
    assert result.get_column("fiitax").to_list() == [16_700.04]


def test_outputs_are_taxsim35s_in_its_order() -> None:
    assert calculate_for_stata(NBER_EXAMPLE).columns == list(OUTPUTS)
    full = calculate_for_stata(NBER_EXAMPLE, full=True).columns
    assert full == [*OUTPUTS, *FULL_OUTPUTS]
    assert {f"v{n}" for n in range(10, 46)} <= set(full)


def test_every_output_has_a_label() -> None:
    columns = calculate_for_stata(NBER_EXAMPLE, full=True).columns
    assert [c for c in columns if c not in LABELS] == ["taxsimid", "year", "state"]


def test_absent_taxsimid_and_state_are_filled() -> None:
    inputs = prepare_inputs(pl.DataFrame({"year": [2022, 2022], "mstat": [1, 2]}))
    assert inputs.get_column("taxsimid").to_list() == [1, 2]
    assert inputs.get_column("state").to_list() == [0, 0]


def test_idtl_and_mtr_in_the_data_are_ignored() -> None:
    frame = NBER_EXAMPLE.with_columns(idtl=pl.lit(2), mtr=pl.lit(70))
    assert "idtl" not in prepare_inputs(frame).columns
    assert calculate_for_stata(frame).columns == list(OUTPUTS)


def test_numeric_strings_are_converted() -> None:
    frame = pl.DataFrame({"year": ["2022"], "mstat": [" 1 "], "pwages": ["50000.5"]})
    inputs = prepare_inputs(frame)
    assert inputs.get_column("pwages").to_list() == [50_000.5]
    assert inputs.get_column("mstat").to_list() == [1.0]


def test_text_that_is_not_a_number_is_reported_with_observations() -> None:
    frame = pl.DataFrame({"year": [2022, 2022], "mstat": [1, 1], "pwages": ["50000", "lots"]})
    with pytest.raises(StataInputError, match=r"pwages has text that is not a number in 1 observation \(first: 2\)"):
        prepare_inputs(frame)


def test_codes_and_counts_must_be_whole_numbers() -> None:
    frame = pl.DataFrame({"year": [2022.0], "mstat": [1.5], "depx": [2.0]})
    with pytest.raises(StataInputError, match="mstat must be a whole number"):
        prepare_inputs(frame)


def test_missing_values_are_an_error_unless_missing_to_zero() -> None:
    frame = pl.DataFrame({"year": [2022, 2022], "mstat": [1, 1], "pwages": [50_000.0, None]})
    with pytest.raises(StataInputError, match="pwages has missing values .* missing_to_zero"):
        prepare_inputs(frame)
    assert prepare_inputs(frame, missing_to_zero=True).get_column("pwages").to_list() == [50_000.0, 0.0]


def test_taxsimid_passes_through_as_it_is() -> None:
    frame = pl.DataFrame({"taxsimid": ["hh-1", "hh-2"], "year": [2022, 2022], "mstat": [1, 2]})
    assert calculate_for_stata(frame).get_column("taxsimid").to_list() == ["hh-1", "hh-2"]
    fractional = frame.with_columns(taxsimid=pl.Series([1.5, 2.5]))
    assert calculate_for_stata(fractional).get_column("taxsimid").to_list() == [1.5, 2.5]


def test_blank_string_taxsimid_is_missing() -> None:
    frame = pl.DataFrame({"taxsimid": ["hh-1", " "], "year": [2022, 2022], "mstat": [1, 1]})
    with pytest.raises(StataInputError, match=r"taxsimid has missing values in 1 observation \(first: 2\)"):
        prepare_inputs(frame)


def test_missing_taxsimid_is_always_an_error() -> None:
    frame = pl.DataFrame({"taxsimid": [1, None], "year": [2022, 2022], "mstat": [1, 1]})
    with pytest.raises(StataInputError, match="taxsimid has missing values"):
        prepare_inputs(frame, missing_to_zero=True)


def test_statutory_mode_is_passed_through() -> None:
    frame = pl.DataFrame({"year": [2022], "state": [5], "mstat": [1], "pwages": [60_000.0]})
    assert calculate_for_stata(frame, statutory=True).height == 1


@pytest.mark.skipif(not HAS_READSTAT, reason="needs taxsim-py[readstat]")
def test_files_round_trip_with_labels(tmp_path) -> None:
    import polars_readstat

    source = tmp_path / "in.dta"
    polars_readstat.write_readstat(NBER_EXAMPLE, str(source), format="dta")
    outputs = [tmp_path / "out.dta", tmp_path / "out.parquet"]

    assert run_stata_files(source, outputs, mtr=14, full=True) == 1

    from_dta = polars_readstat.read_readstat(str(outputs[0]))
    assert from_dta.get_column("fiitax").to_list() == [16_700.04]
    assert pl.read_parquet(outputs[1]).get_column("fiitax").to_list() == [16_700.04]
    labels = dict(
        polars_readstat.ScanReadstat(str(outputs[0])).metadata_df.select("name", "label").iter_rows()
    )
    assert labels["fiitax"] == "Federal Income Tax"
    assert labels["v10"] == "Federal AGI"
