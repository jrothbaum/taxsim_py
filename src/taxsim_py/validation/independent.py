"""Adapters for independent tax calculators used by the validation suite.

The imports for these calculators are deliberately local. They are test
dependencies, not requirements for using :func:`taxsim_py.calculate_taxes`.
"""

from __future__ import annotations

import polars as pl


COMPARABLE_OUTPUTS = ("fiitax", "fica")
POLICYENGINE_COMPARABLE_OUTPUTS = ("fiitax", "siitax", "fica")


def _column(frame: pl.DataFrame, name: str, default: float = 0.0) -> list:
    if name not in frame.columns:
        return [default] * frame.height
    return frame.get_column(name).fill_null(default).to_list()


def _taxcalc_filing_statuses(frame: pl.DataFrame) -> list[int]:
    statuses = []
    for marital_status, dependents in zip(
        _column(frame, "mstat"), _column(frame, "depx")
    ):
        marital_status = int(marital_status)
        if marital_status == 2:
            statuses.append(2)
        elif marital_status in (6, 66):
            statuses.append(3)
        elif marital_status in (1, 3) and dependents > 0:
            statuses.append(4)
        elif marital_status in (1, 3, 33):
            statuses.append(1)
        else:
            raise ValueError(
                f"Tax-Calculator adapter does not support TAXSIM mstat={marital_status}"
            )
    return statuses


def _taxcalc_input(frame: pl.DataFrame):
    """Translate the common TAXSIM inputs to a Tax-Calculator Records frame."""
    import pandas as pd

    dependents = [int(value) for value in _column(frame, "depx")]
    qualifying_children = [int(value) for value in _column(frame, "dep18")]
    child_tax_credit_children = [int(value) for value in _column(frame, "dep17")]
    primary_wages = _column(frame, "pwages")
    spouse_wages = _column(frame, "swages")
    primary_self_employment = _column(frame, "psemp")
    spouse_self_employment = _column(frame, "ssemp")
    unemployment = [
        primary + spouse
        for primary, spouse in zip(_column(frame, "pui"), _column(frame, "sui"))
    ]
    pensions = _column(frame, "pensions")
    dividends = _column(frame, "dividends")
    filing_statuses = _taxcalc_filing_statuses(frame)

    return pd.DataFrame(
        {
            "RECID": [int(value) for value in _column(frame, "taxsimid")],
            "MARS": filing_statuses,
            "age_head": [int(value) for value in _column(frame, "page")],
            "age_spouse": [int(value) for value in _column(frame, "sage")],
            "XTOT": [
                dependents_count + (2 if status == 2 else 1)
                for dependents_count, status in zip(dependents, filing_statuses)
            ],
            # TAXSIM supplies age-band counts rather than every dependent age.
            # These are the closest Tax-Calculator record concepts.
            "nu06": [int(value) for value in _column(frame, "dep6")],
            "nu18": qualifying_children,
            "n24": child_tax_credit_children,
            "EIC": [min(value, 3) for value in qualifying_children],
            "e00200": [
                primary + spouse
                for primary, spouse in zip(primary_wages, spouse_wages)
            ],
            "e00200p": primary_wages,
            "e00200s": spouse_wages,
            "e00300": _column(frame, "intrec"),
            "e00600": dividends,
            "e00650": dividends,
            "p22250": _column(frame, "stcg"),
            "p23250": _column(frame, "ltcg"),
            "e00900": [
                primary + spouse
                for primary, spouse in zip(
                    primary_self_employment, spouse_self_employment
                )
            ],
            "e00900p": primary_self_employment,
            "e00900s": spouse_self_employment,
            "e01500": pensions,
            "e01700": pensions,
            "e02300": unemployment,
            "e02400": _column(frame, "gssi"),
            "e00800": _column(frame, "nonprop"),
            "e02000": [
                rental + s_corporation
                for rental, s_corporation in zip(
                    _column(frame, "otherprop"), _column(frame, "scorp")
                )
            ],
            "e26270": _column(frame, "scorp"),
        }
    )


def run_taxcalc(frame: pl.DataFrame) -> pl.DataFrame:
    """Calculate federal income and payroll tax with PSL Tax-Calculator.

    Tax-Calculator supports federal law from 2013 onward. Mixed-year frames
    are split because each ``Records`` object has one current tax year.
    """
    import taxcalc as tc

    required = {"taxsimid", "year", "mstat"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(sorted(missing))}")

    results = []
    for year_frame in frame.partition_by("year", maintain_order=True):
        year = int(year_frame.get_column("year")[0])
        if year < tc.Policy.JSON_START_YEAR:
            raise ValueError(
                f"Tax-Calculator begins in {tc.Policy.JSON_START_YEAR}; got {year}"
            )
        records = tc.Records(
            data=_taxcalc_input(year_frame),
            start_year=year,
            gfactors=None,
            weights=None,
        )
        # Tax-Calculator models incomplete EITC/ACTC take-up using a stable
        # random draw. A tax-law comparison needs every eligible unit to claim.
        records.credit_claim_urn[:] = 0.0
        calculator = tc.Calculator(policy=tc.Policy(), records=records, verbose=False)
        calculator.calc_all()
        results.append(
            pl.DataFrame(
                {
                    "taxsimid": year_frame.get_column("taxsimid"),
                    "year": [year] * year_frame.height,
                    "fiitax": calculator.array("iitax"),
                    "fica": calculator.array("payrolltax"),
                }
            )
        )
    return pl.concat(results, how="vertical").sort("taxsimid")


def run_policyengine(frame: pl.DataFrame) -> pl.DataFrame:
    """Calculate TAXSIM outputs with PolicyEngine's local TAXSIM API.

    PolicyEngine independently implements federal and state law from 2021
    onward. Calling ``PolicyEngineRunner`` directly is important: the package's
    stitched runner delegates earlier years back to NBER TAXSIM.
    """
    import pandas as pd
    from policyengine_taxsim.runners.policyengine_runner import PolicyEngineRunner

    if frame.get_column("year").min() < 2021:
        raise ValueError("PolicyEngine TAXSIM comparisons require tax year 2021+")
    result = PolicyEngineRunner(
        pd.DataFrame(frame.to_dicts()),
        logs=False,
        disable_salt=True,
        assume_w2_wages=True,
    ).run(show_progress=False)
    return pl.from_pandas(result).select(
        "taxsimid", "year", "state", *POLICYENGINE_COMPARABLE_OUTPUTS
    ).sort("taxsimid")
