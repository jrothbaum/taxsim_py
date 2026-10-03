"""Public dataframe API."""

from concurrent.futures import ThreadPoolExecutor
from enum import IntEnum
from typing import Literal

import polars as pl

from taxsim_py.behavior import BehaviorProfile, CalculationMode, resolve_behavior
from taxsim_py.calculators.states import (
    FIPS_TO_TAXSIM,
    STATE_CALCULATOR_PATHS,
    get_state_calculators,
)
from taxsim_py.engine.detail import (
    DETAIL_NAME_TO_TAXSIM,
    FEDERAL_DETAIL_COLUMNS,
    STATE_DETAIL_COLUMNS,
    STATE_DETAIL_SOURCES,
    federal_detail,
    state_detail,
)
from taxsim_py.engine.federal_state import resolve_federal_and_state
from taxsim_py.engine.inputs import SUPPORTED_MSTAT, with_input_defaults

StateIdType = Literal["taxsim", "fips"]


class MarginalInput(IntEnum):
    """TAXSIM code identifying the input used for marginal-rate calculations."""

    NONE = 0
    BOTH_WAGES = 11
    DIVIDENDS = 12
    INTEREST_RECEIVED = 14
    PRIMARY_SELF_EMPLOYMENT_INCOME = 17
    PENSIONS = 20
    NONPROPERTY_INCOME = 30
    TRANSFERS = 41
    PROPERTY_TAX = 51
    OTHER_ITEMIZED_DEDUCTIONS = 54
    MORTGAGE_INTEREST = 56
    CHILDCARE_EXPENSES = 64
    SHORT_TERM_CAPITAL_GAINS = 68
    LONG_TERM_CAPITAL_GAINS = 70
    OTHER_PROPERTY_INCOME = 79
    PRIMARY_UNEMPLOYMENT = 82
    PRIMARY_WAGES = 85
    SPOUSE_WAGES = 86
    SOCIAL_SECURITY_BENEFITS = 91
    RENT_PAID = 160
    SPOUSE_UNEMPLOYMENT = 180
    PRIMARY_BUSINESS_INCOME = 211
    PRIMARY_PROFESSIONAL_INCOME = 212
    S_CORPORATION_INCOME = 213
    SPOUSE_BUSINESS_INCOME = 214
    SPOUSE_PROFESSIONAL_INCOME = 215


# Results returned by default, beside the input columns.
OUTPUT_COLUMNS = (
    "fiitax", "siitax", "fica", "ficar", "tfica", "addmed", "agi", "taxable_income",
    "taxable_unemployment", "earned_income", "regular_tax",
)


def _temporary_name(columns: list[str], base: str) -> str:
    name = base
    while name in columns:
        name += "_"
    return name


def _default_year_workers() -> int:
    """Polars' thread pool size, so `POLARS_MAX_THREADS` caps year workers too."""
    return pl.thread_pool_size()


def _normalized_states(values: pl.Series, state_id_type: StateIdType) -> list[int]:
    if values.null_count():
        raise ValueError("State IDs cannot be null")
    try:
        state_ids = [int(value) for value in values.unique().to_list()]
    except (TypeError, ValueError) as exc:
        raise ValueError("State IDs must be integers") from exc

    if state_id_type == "fips":
        unknown = sorted(set(state_ids) - FIPS_TO_TAXSIM.keys())
        if unknown:
            raise ValueError(f"Unknown state FIPS code(s): {unknown}")
        state_ids = [FIPS_TO_TAXSIM[state] for state in state_ids]
    elif state_id_type != "taxsim":
        raise ValueError("state_id_type must be 'taxsim' or 'fips'")

    unsupported = sorted(set(state_ids) - STATE_CALCULATOR_PATHS.keys())
    if unsupported:
        raise NotImplementedError(
            f"No state tax calculator is implemented for TAXSIM state code(s): {unsupported}"
        )
    return state_ids


# TAXSIM marginal-rate codes (`mtr`, the `data()` slot perturbed) and the
# input each changes, with its sign (`nonprop` is stored negated).
MARGINAL_INPUTS: dict[MarginalInput, tuple[str, float]] = {
    MarginalInput.PRIMARY_WAGES: ("pwages", 1.0),
    MarginalInput.SPOUSE_WAGES: ("swages", 1.0),
    MarginalInput.PRIMARY_SELF_EMPLOYMENT_INCOME: ("psemp", 1.0),
    MarginalInput.DIVIDENDS: ("dividends", 1.0),
    MarginalInput.INTEREST_RECEIVED: ("intrec", 1.0),
    MarginalInput.SHORT_TERM_CAPITAL_GAINS: ("stcg", 1.0),
    MarginalInput.LONG_TERM_CAPITAL_GAINS: ("ltcg", 1.0),
    MarginalInput.PENSIONS: ("pensions", 1.0),
    MarginalInput.SOCIAL_SECURITY_BENEFITS: ("gssi", 1.0),
    MarginalInput.PRIMARY_UNEMPLOYMENT: ("ui", 1.0),
    MarginalInput.SPOUSE_UNEMPLOYMENT: ("sui", 1.0),
    MarginalInput.PROPERTY_TAX: ("proptax", 1.0),
    MarginalInput.OTHER_ITEMIZED_DEDUCTIONS: ("otheritem", 1.0),
    MarginalInput.MORTGAGE_INTEREST: ("mortgage", 1.0),
    MarginalInput.CHILDCARE_EXPENSES: ("childcare", 1.0),
    MarginalInput.RENT_PAID: ("rentpaid", 1.0),
    MarginalInput.OTHER_PROPERTY_INCOME: ("otherprop", 1.0),
    MarginalInput.NONPROPERTY_INCOME: ("nonprop", -1.0),
    MarginalInput.TRANSFERS: ("transfers", 1.0),
    MarginalInput.S_CORPORATION_INCOME: ("scorp", 1.0),
    MarginalInput.PRIMARY_BUSINESS_INCOME: ("pbusinc", 1.0),
    MarginalInput.PRIMARY_PROFESSIONAL_INCOME: ("pprofinc", 1.0),
    MarginalInput.SPOUSE_BUSINESS_INCOME: ("sbusinc", 1.0),
    MarginalInput.SPOUSE_PROFESSIONAL_INCOME: ("sprofinc", 1.0),
}
_MARGINAL_STEP = 0.01
# TAXSIM retries with a decrease when an increase gives a rate outside these.
_FEDERAL_RATE_LIMIT = 100.0
_STATE_RATE_LIMIT = 25.0
_MIN_ROWS_PER_WORKER = 25_000
_MAX_ROW_WORKERS = 8


def _resolve(
    rows: pl.DataFrame,
    year: int | None,
    year_column: str,
    calculators: dict,
    max_year_workers: int | None,
    keep_intermediate: bool,
    behavior: BehaviorProfile,
    keep_columns: tuple[str, ...] = (),
    result_columns: tuple[str, ...] | None = None,
) -> pl.DataFrame:
    """Resolve year partitions, splitting a lone large year into row partitions."""
    def resolve_partition(part: pl.DataFrame, partition_year: int) -> pl.DataFrame:
        return resolve_federal_and_state(
            part,
            partition_year,
            calculators,
            keep_intermediate=keep_intermediate,
            keep_columns=keep_columns,
            result_columns=result_columns,
            behavior=behavior,
        )

    def resolve_single_year(part: pl.DataFrame, partition_year: int) -> pl.DataFrame:
        worker_limit = _default_year_workers() if max_year_workers is None else max_year_workers
        workers = min(
            worker_limit,
            _MAX_ROW_WORKERS,
            max(1, part.height // _MIN_ROWS_PER_WORKER),
        )
        if workers == 1:
            return resolve_partition(part, partition_year)
        slice_size = (part.height + workers - 1) // workers
        slices = list(part.iter_slices(n_rows=slice_size))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            return pl.concat(
                executor.map(lambda chunk: resolve_partition(chunk, partition_year), slices), how="diagonal_relaxed"
            )

    if year is not None:
        return resolve_single_year(rows, int(year))
    year_partitions = list(rows.partition_by(year_column, as_dict=True).items())

    def resolve_year_partition(partition: tuple[tuple[int], pl.DataFrame]) -> pl.DataFrame:
        (partition_year,), part = partition
        return resolve_federal_and_state(
            part,
            int(partition_year),
            calculators,
            keep_intermediate=keep_intermediate,
            keep_columns=keep_columns,
            result_columns=result_columns,
            behavior=behavior,
        )

    if len(year_partitions) == 1:
        (partition_year,), part = year_partitions[0]
        parts = [resolve_single_year(part, int(partition_year))]
    else:
        worker_limit = _default_year_workers() if max_year_workers is None else max_year_workers
        with ThreadPoolExecutor(max_workers=min(worker_limit, len(year_partitions))) as executor:
            parts = list(executor.map(resolve_year_partition, year_partitions))
    return pl.concat(parts, how="diagonal_relaxed")


def _as_marginal_input(value: int | MarginalInput) -> MarginalInput:
    try:
        return MarginalInput(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Unsupported marginal-rate code (mtr): {value}") from exc


def _perturbed(
    rows: pl.DataFrame, code: int | MarginalInput, step: float
) -> pl.DataFrame:
    """Rows with the input for marginal-rate `code` changed by `step`."""
    marginal_input = _as_marginal_input(code)
    if marginal_input is MarginalInput.BOTH_WAGES:
        wages = pl.col("pwages") + pl.col("swages")
        return rows.with_columns(
            pwages=pl.when(wages != 0).then(pl.col("pwages") + step * pl.col("pwages") / wages)
            .otherwise(pl.col("pwages") + step / 2),
            swages=pl.when(wages != 0).then(pl.col("swages") + step * pl.col("swages") / wages)
            .otherwise(pl.col("swages") + step / 2),
        )
    if marginal_input is MarginalInput.NONE:
        return rows
    column, sign = MARGINAL_INPUTS[marginal_input]
    return rows.with_columns((pl.col(column) + sign * step).alias(column))


def _marginal_rates(
    work: pl.DataFrame,
    base: pl.DataFrame,
    codes: pl.Series,
    row_column: str,
    resolve,
    initial_changed: pl.DataFrame,
    detail_sources: tuple[str, ...] = (),
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """`frate` and `srate` (percent) for each row, aligned with `base`.

    Also returns, per perturbed row, the step and the `detail_sources`
    values of the last calculation TAXSIM runs for that row, whose state
    worksheet it prints.
    """
    rates = pl.DataFrame({row_column: work.get_column(row_column), "frate": 0.0, "srate": 0.0})
    base_tax = base.select(row_column, "fiitax", "siitax")
    detail_parts: list[pl.DataFrame] = []
    for code in sorted(set(codes.to_list())):
        if not code:
            continue
        rows = work.filter(codes == code)
        found = None
        pending = rows
        for step in (_MARGINAL_STEP, -_MARGINAL_STEP):
            if pending.is_empty():
                break
            if step > 0:
                changed = initial_changed.join(
                    pending.select(row_column), on=row_column, how="semi"
                )
            else:
                changed = resolve(_perturbed(pending, code, step))
            present = [c for c in detail_sources if c in changed.columns]
            changed = changed.select(row_column, "fiitax", "siitax", *present)
            trial = changed.join(base_tax, on=row_column, suffix="_base").select(
                row_column,
                frate=(100 * (pl.col("fiitax") - pl.col("fiitax_base")) / step),
                srate=(100 * (pl.col("siitax") - pl.col("siitax_base")) / step),
            )
            accepted = (trial["frate"].abs() < _FEDERAL_RATE_LIMIT) & (trial["srate"].abs() < _STATE_RATE_LIMIT)
            printed = changed if step < 0 else changed.filter(accepted)
            detail_parts.append(
                printed.select(row_column, *present).with_columns(
                    __detail_code=pl.lit(code, dtype=pl.Int64), __detail_step=pl.lit(step)
                )
            )
            keep = trial if step < 0 else trial.filter(accepted)
            found = keep if found is None else pl.concat([found, keep])
            pending = pending.join(trial.filter(~accepted).select(row_column), on=row_column, how="semi")
        rates = rates.update(found, on=row_column)
    rates = rates.with_columns(
        pl.col("frate").clip(-99, 999).round(2), pl.col("srate").clip(-99, 999).round(2)
    )
    detail = (
        pl.concat(detail_parts, how="diagonal_relaxed")
        if detail_parts
        else pl.DataFrame(schema={row_column: pl.Int64, "__detail_code": pl.Int64, "__detail_step": pl.Float64})
    )
    return rates.sort(row_column).select("frate", "srate"), detail


def _with_printed_state_worksheet(result: pl.DataFrame, printed: pl.DataFrame, row_column: str) -> pl.DataFrame:
    """`result` with its state worksheet columns taken from `printed`.

    TAXSIM prints the state worksheet of its last marginal-rate calculation.
    Household income there moves by the step unless the perturbed input is
    a deduction slot (`data(47)`-`data(63)`).
    """
    sources = [c for c in printed.columns if c not in (row_column, "__detail_code", "__detail_step")]
    hy_step = pl.when(pl.col("__detail_code").is_between(47, 63)).then(0.0).otherwise(pl.col("__detail_step"))
    printed = printed.select(row_column, *sources, __detail_hy_step=hy_step)
    return result.update(printed.select(row_column, *sources), on=row_column).join(
        printed.select(row_column, "__detail_hy_step"), on=row_column, how="left", maintain_order="left"
    )


def calculate_taxes(
    df: pl.DataFrame | pl.LazyFrame,
    *,
    year: int | None = None,
    year_column: str = "year",
    state_column: str = "state",
    state_id_type: StateIdType = "taxsim",
    max_year_workers: int | None = None,
    keep_intermediate: bool = False,
    mtr: int | MarginalInput | None = None,
    idtl: int | None = None,
    taxsim_names: bool = False,
    calculation_mode: str | CalculationMode = CalculationMode.STATUTORY,
) -> pl.DataFrame:
    """Calculate federal, payroll, and state taxes for a Polars dataframe.

    Rows may contain multiple states and, when ``year`` is omitted, multiple
    years. State IDs are interpreted as TAXSIM codes by default or as Census
    state FIPS codes when ``state_id_type="fips"``; 0 means no state. Input
    columns other than the year, state and ``mstat`` default to 0 when absent
    or null (``dep13``, ``dep17`` and ``dep18`` default to ``depx``). Optional
    semantic survey extensions ``children_under_3``, ``children_under_4`` and
    ``children_under_7`` also default to 0; they are not part of TAXSIM's 35-input contract. The returned
    eager frame is the input, unchanged and in order, plus ``OUTPUT_COLUMNS``;
    ``keep_intermediate=True`` also returns every intermediate federal and
    state column. Mixed-year calls resolve years concurrently; large
    single-year calls similarly split rows across workers. Both use up to
    ``max_year_workers`` threads (default: Polars' thread pool size, which
    ``POLARS_MAX_THREADS`` sets), with single-year row workers capped at eight.

    ``mtr`` (or an integer ``mtr`` column, per row) requests TAXSIM's marginal
    rates ``frate`` and ``srate`` with respect to a ``MarginalInput``. Raw
    TAXSIM codes remain accepted and are converted to that enum; 0 gives no
    rates.

    ``idtl=2`` (or an ``idtl`` column containing 2) adds meaningfully named
    detailed federal and state outputs, ``FEDERAL_DETAIL_COLUMNS`` and
    ``STATE_DETAIL_COLUMNS``. Set ``taxsim_names=True`` to rename only those
    detail columns to TAXSIM's compatibility labels (``credits``, ``v10``-
    ``v45`` and ``staxbc``) as the final output step.

    ``calculation_mode="statutory"`` is the default and applies the canonical
    parameter tables plus independently verified corrections recorded in
    ``docs/statutory_corrections.md``. ``calculation_mode="taxsim"`` preserves
    the compiled model's behavior for replication and compatibility testing.
    """
    behavior = resolve_behavior(calculation_mode)
    frame = df.collect() if isinstance(df, pl.LazyFrame) else df
    columns = frame.columns
    if state_column not in columns:
        raise ValueError(f"Missing state column: {state_column!r}")
    if "mstat" not in columns:
        raise ValueError("Missing filing status column: 'mstat'")
    if frame.get_column("mstat").null_count():
        raise ValueError("Filing status (mstat) cannot be null")
    unsupported_mstat = set(frame.get_column("mstat").unique().to_list()) - set(SUPPORTED_MSTAT)
    if unsupported_mstat:
        raise ValueError(f"Unsupported filing status (mstat): {sorted(unsupported_mstat)}")
    if year is None and year_column not in columns:
        raise ValueError(f"Missing year column: {year_column!r}")
    if year is None and frame.get_column(year_column).null_count():
        raise ValueError("Years cannot be null")
    if frame.is_empty():
        raise ValueError("Cannot calculate taxes for an empty dataframe")
    if max_year_workers is not None and max_year_workers < 1:
        raise ValueError("max_year_workers must be at least 1")
    detail_levels = {int(idtl)} if idtl is not None else set()
    if "idtl" in columns:
        detail_levels |= set(frame.get_column("idtl").fill_null(0).cast(pl.Int64).unique().to_list())
    if detail_levels - {0, 2}:
        raise ValueError(f"Unsupported detail level (idtl): {sorted(detail_levels - {0, 2})}")

    normalized_states = _normalized_states(frame.get_column(state_column), state_id_type)
    calculators = get_state_calculators(normalized_states)

    row_column = _temporary_name(columns, "__taxsim_py_row")
    work = frame.with_row_index(row_column)
    if state_column != "state" and "state" in columns:
        # The engine reads `state`; a caller's own `state` column is set aside.
        work = work.rename({"state": _temporary_name(columns + [row_column], "__taxsim_py_state")})
    state_expr = pl.col(state_column).cast(pl.Int64)
    if state_id_type == "fips":
        state_expr = state_expr.replace_strict(FIPS_TO_TAXSIM, return_dtype=pl.Int64)
    year_expr = pl.lit(int(year)) if year is not None else pl.col(year_column).cast(pl.Int64)
    work = with_input_defaults(work.with_columns(state_expr.alias("state")), year_expr)

    def resolve(rows: pl.DataFrame) -> pl.DataFrame:
        result_columns = None
        if not keep_intermediate and 2 not in detail_levels:
            internal = tuple(c for c in rows.columns if c.startswith("__taxsim_py_"))
            result_columns = (*internal, *OUTPUT_COLUMNS)
        return _resolve(
            rows,
            year,
            year_column,
            calculators,
            max_year_workers,
            keep_intermediate,
            behavior,
            keep_columns=tuple(STATE_DETAIL_SOURCES.values()) if 2 in detail_levels else (),
            result_columns=result_columns,
        ).sort(row_column)

    marginal_input = None if mtr is None else _as_marginal_input(mtr)
    if marginal_input is not None or "mtr" in columns:
        codes = (
            frame.get_column("mtr").fill_null(0).cast(pl.Int64)
            if "mtr" in columns
            else pl.Series([int(marginal_input)] * work.height, dtype=pl.Int64)
        )
        perturbed = [
            _perturbed(work.filter(codes == code), code, _MARGINAL_STEP)
            for code in sorted(set(codes.to_list()))
            if code
        ]
        if perturbed:
            scenario_column = _temporary_name(work.columns, "__taxsim_py_marginal")
            combined = pl.concat(
                [
                    work.with_columns(pl.lit(False).alias(scenario_column)),
                    pl.concat(perturbed, how="diagonal_relaxed").with_columns(
                        pl.lit(True).alias(scenario_column)
                    ),
                ],
                how="diagonal_relaxed",
            )
            combined_result = resolve(combined)
            result = combined_result.filter(~pl.col(scenario_column)).drop(
                scenario_column
            )
            initial_changed = combined_result.filter(pl.col(scenario_column)).drop(
                scenario_column
            )
        else:
            result = resolve(work)
            initial_changed = result.clear()
        detail_sources = tuple(
            c for c in STATE_DETAIL_SOURCES.values() if 2 in detail_levels and c in initial_changed.columns
        )
        rates, printed = _marginal_rates(
            work, result, codes, row_column, resolve, initial_changed, detail_sources
        )
        result = result.hstack(rates)
    else:
        result = resolve(work)
        printed = None
    detail_columns: tuple[str, ...] = ()
    if 2 in detail_levels:
        result = result.with_columns(*federal_detail(result, year_expr))
        if printed is not None and printed.height:
            result = _with_printed_state_worksheet(result, printed, row_column)
        result = result.with_columns(*state_detail(result))
        if printed is not None and printed.height:
            result = result.with_columns(
                state_household_income=pl.col("state_household_income")
                + pl.col("__detail_hy_step").fill_null(0.0)
            ).drop("__detail_hy_step")
        detail_columns = (*FEDERAL_DETAIL_COLUMNS, *STATE_DETAIL_COLUMNS)
        if taxsim_names:
            result = result.rename(DETAIL_NAME_TO_TAXSIM)
            detail_columns = tuple(DETAIL_NAME_TO_TAXSIM[c] for c in detail_columns)
    # Inputs come back exactly as supplied; only new columns are added.
    if keep_intermediate:
        added = [c for c in result.columns if c not in columns and not c.startswith("__taxsim_py_")]
        if state_column != "state" and "state" not in columns:
            added.remove("state")
    else:
        added = [
            c for c in (*OUTPUT_COLUMNS, "frate", "srate", *detail_columns) if c in result.columns and c not in columns
        ]
    return frame.hstack(result.select(added))
