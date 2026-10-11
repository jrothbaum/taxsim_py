"""Dataframe-oriented TAXSIM calculations."""

from importlib.metadata import PackageNotFoundError, version

from taxsim_py.api import MarginalInput, calculate_row, calculate_taxes
from taxsim_py.behavior import CalculationMode

try:
    # The version imported, which in a long-running Python (such as Stata's) can differ from
    # the one installed on disk after an update.
    __version__ = version("taxsim-py")
except PackageNotFoundError:  # running from a source tree that is not installed
    __version__ = "0.0.0"

__all__ = ["CalculationMode", "MarginalInput", "__version__", "calculate_row", "calculate_taxes"]
