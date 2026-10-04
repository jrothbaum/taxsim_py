"""Command line interface: `taxsim-py INPUT [OUTPUT]`."""

import argparse
import sys
from collections.abc import Sequence

import polars as pl

from taxsim_py.api import LAST_SUPPORTED_YEAR, calculate_taxes
from taxsim_py.io.tables import read_table, write_table


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="taxsim-py",
        description=(
            "Calculate federal, payroll and state taxes for every row of a table. The result is "
            "the input plus the output columns. File types come from the extensions: csv, tsv, "
            "parquet, arrow, ndjson, and (with taxsim-py[readstat]) dta, sas7bdat, sav, zsav "
            "(sas7bdat is read only)."
        ),
    )
    parser.add_argument("input", help="input file")
    parser.add_argument("output", nargs="?", default="-", help="output file (default: CSV on standard output)")
    parser.add_argument("--input-format", help="override the input file type")
    parser.add_argument("--output-format", help="override the output file type")
    parser.add_argument("--mode", choices=("statutory", "taxsim"), default="statutory", help="calculation mode")
    parser.add_argument("--year", type=int, help=f"tax year for every row (up to {LAST_SUPPORTED_YEAR}); default: the year column")
    parser.add_argument("--state-id-type", choices=("taxsim", "fips"), default="taxsim", help="how the state column is coded")
    parser.add_argument("--idtl", type=int, choices=(0, 2), help="2 adds detailed federal and state worksheets")
    parser.add_argument("--mtr", type=int, help="marginal-rate code (see MarginalInput)")
    parser.add_argument("--taxsim-names", action="store_true", help="use TAXSIM's v1-style names for detail columns")
    parser.add_argument("--keep-intermediate", action="store_true", help="also return intermediate columns")
    parser.add_argument("--lowercase", action="store_true", help="lowercase the input column names (useful for SAS files)")
    parser.add_argument("--batch-rows", type=int, help="maximum input rows per calculation batch")
    parser.add_argument("--workers", type=int, help="threads (batches in flight)")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        frame = read_table(args.input, args.input_format)
        if args.lowercase:
            frame = frame.rename({name: name.lower() for name in frame.columns})
        result = calculate_taxes(
            frame,
            year=args.year,
            state_id_type=args.state_id_type,
            idtl=args.idtl,
            mtr=args.mtr,
            taxsim_names=args.taxsim_names,
            keep_intermediate=args.keep_intermediate,
            calculation_mode=args.mode,
            batch_rows=args.batch_rows,
            max_year_workers=args.workers,
        )
        if args.output == "-":
            sys.stdout.write(result.write_csv())
        else:
            write_table(result, args.output, args.output_format)
    except (ValueError, ImportError, FileNotFoundError, pl.exceptions.PolarsError) as error:
        print(f"taxsim-py: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
