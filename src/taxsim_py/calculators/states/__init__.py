"""State calculator registry and state-code conversions."""

from collections.abc import Callable, Iterable
from functools import lru_cache
from importlib import import_module

import polars as pl

from taxsim_py.behavior import BehaviorProfile

StateCalculator = Callable[[pl.LazyFrame, int, BehaviorProfile], pl.LazyFrame]

# TAXSIM numbers states alphabetically, with the District of Columbia after
# Delaware. Registry values are module and function names so importing the
# public API does not parse parameter files for states absent from a batch.
_NO_INCOME_TAX = ("no_income_tax", "compute_no_income_tax")

STATE_CALCULATOR_PATHS: dict[int, tuple[str, str]] = {
    # No state: federal taxes only.
    0: _NO_INCOME_TAX,
    1: ("al", "compute_al_tax"),
    2: ("ak", "compute_ak_tax"),
    3: ("az", "compute_az_tax"),
    4: ("ar", "compute_ar_tax"),
    5: ("ca", "compute_ca_tax"),
    6: ("co", "compute_co_tax"),
    7: ("ct", "compute_ct_tax"),
    8: ("de", "compute_de_tax"),
    9: ("dc", "compute_dc_tax"),
    10: _NO_INCOME_TAX,  # Florida
    11: ("ga", "compute_ga_tax"),
    12: ("hi", "compute_hi_tax"),
    13: ("id", "compute_id_tax"),
    14: ("il", "compute_il_tax"),
    15: ("in_", "compute_in_tax"),
    16: ("ia", "compute_ia_tax"),
    17: ("ks", "compute_ks_tax"),
    18: ("ky", "compute_ky_tax"),
    19: ("la", "compute_la_tax"),
    20: ("me", "compute_me_tax"),
    21: ("md", "compute_md_tax"),
    22: ("ma", "compute_ma_tax"),
    23: ("mi", "compute_mi_tax"),
    24: ("mn", "compute_mn_tax"),
    25: ("ms", "compute_ms_tax"),
    26: ("mo", "compute_mo_tax"),
    27: ("mt", "compute_mt_tax"),
    28: ("ne", "compute_ne_tax"),
    29: _NO_INCOME_TAX,  # Nevada
    30: ("nh", "compute_nh_tax"),
    31: ("nj", "compute_nj_tax"),
    32: ("nm", "compute_nm_tax"),
    33: ("ny", "compute_ny_tax"),
    34: ("nc", "compute_nc_tax"),
    35: ("nd", "compute_nd_tax"),
    36: ("oh", "compute_oh_tax"),
    37: ("ok", "compute_ok_tax"),
    38: ("or_", "compute_or_tax"),
    39: ("pa", "compute_pa_tax"),
    40: ("ri", "compute_ri_tax"),
    41: ("sc", "compute_sc_tax"),
    42: _NO_INCOME_TAX,  # South Dakota
    43: ("tn", "compute_tn_tax"),
    44: _NO_INCOME_TAX,  # Texas
    45: ("ut", "compute_ut_tax"),
    46: ("vt", "compute_vt_tax"),
    47: ("va", "compute_va_tax"),
    48: _NO_INCOME_TAX,  # Washington
    49: ("wv", "compute_wv_tax"),
    50: ("wi", "compute_wi_tax"),
    51: _NO_INCOME_TAX,  # Wyoming
}

# FIPS 0 stands for no state, as TAXSIM state 0 does.
FIPS_TO_TAXSIM = {
    0: 0, 1: 1, 2: 2, 4: 3, 5: 4, 6: 5, 8: 6, 9: 7, 10: 8, 11: 9,
    12: 10, 13: 11, 15: 12, 16: 13, 17: 14, 18: 15, 19: 16,
    20: 17, 21: 18, 22: 19, 23: 20, 24: 21, 25: 22, 26: 23,
    27: 24, 28: 25, 29: 26, 30: 27, 31: 28, 32: 29, 33: 30,
    34: 31, 35: 32, 36: 33, 37: 34, 38: 35, 39: 36, 40: 37,
    41: 38, 42: 39, 44: 40, 45: 41, 46: 42, 47: 43, 48: 44,
    49: 45, 50: 46, 51: 47, 53: 48, 54: 49, 55: 50, 56: 51,
}


@lru_cache(maxsize=None)
def get_state_calculator(taxsim_state: int) -> StateCalculator:
    """Load and cache the calculator for a TAXSIM state code."""
    try:
        module_name, function_name = STATE_CALCULATOR_PATHS[taxsim_state]
    except KeyError as exc:
        raise NotImplementedError(
            f"No state tax calculator is implemented for TAXSIM state {taxsim_state}"
        ) from exc
    module = import_module(f"taxsim_py.calculators.states.{module_name}")
    return getattr(module, function_name)


def get_state_calculators(taxsim_states: Iterable[int]) -> dict[int, StateCalculator]:
    """Return calculators for the unique TAXSIM codes in a batch."""
    return {state: get_state_calculator(state) for state in set(taxsim_states)}
