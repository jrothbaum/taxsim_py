# Using taxsim-py from Stata

`taxsim_py` is a Stata command that calculates taxes for the data in memory with the same
input and output variables as NBER's `taxsim35`. If you already use `taxsim35` or
`taxsimlocal35`, change the command name and your do-file should run. Nothing is sent to a
server: the calculation runs on your computer, inside Stata's own Python.

It needs Stata 16 or newer.

## Install

There are two parts: the Stata command, and a Python environment with taxsim-py that the
command uses. You do both once.

### 1. Install the command

Every [release](https://github.com/jrothbaum/taxsim_py/releases) has a Stata zip. The address
below always gives the newest release:

```
https://github.com/jrothbaum/taxsim_py/releases/latest/download/taxsim_py-stata.zip
```

To install a particular version, use that release's `taxsim_py-stata-vX.Y.Z.zip` instead, for
example `.../releases/download/v0.4.0/taxsim_py-stata-v0.4.0.zip`.

Stata's `net install` cannot read a zip, so unzip it first, then install from the folder.
This does both from inside Stata:

```stata
copy "https://github.com/jrothbaum/taxsim_py/releases/latest/download/taxsim_py-stata.zip" ///
    "taxsim_py_stata.zip", replace
mkdir taxsim_py_stata
cd taxsim_py_stata
unzipfile "../taxsim_py_stata.zip", replace
net install taxsim_py, from("`c(pwd)'") replace
```

You can also download and unzip the file in a browser, then give `net install` the folder
that holds `taxsim_py.pkg`.

The release zip is the recommended route. To follow the development version instead, install straight from GitHub (Stata needs
the raw-file address of the [`stata` folder](https://github.com/jrothbaum/taxsim_py/tree/main/stata),
not the web page):

```stata
net install taxsim_py, from("https://raw.githubusercontent.com/jrothbaum/taxsim_py/main/stata") replace
```

`help taxsim_py` then shows the full syntax.

### 2. Install the taxsim-py Python library

The command does the calculation with taxsim-py, a Python library, so that must be installed
too. Stata runs Python through its built-in Python integration (`help python`); it needs
Python 3.9 or newer, which `python query` shows. If Stata has none, you can install one with
[uv](https://docs.astral.sh/uv/), which is also used below
([install uv](https://docs.astral.sh/uv/getting-started/installation/)). In a terminal:

```bash
uv python install
uv python find --no-project     # prints the path to that Python
```

Then point Stata at the path that `uv python find` printed, as described in Stata's own
instructions ([`help python`](https://www.stata.com/manuals/ppython.pdf), or
[setting up Stata to use Python](https://blog.stata.com/2020/08/18/stata-python-integration-part-1-setting-up-stata-to-use-python/)).
Any other installation, such as one from [python.org](https://www.python.org), works too; on
Windows it must be an installed Python, not the `python.exe` inside a virtual environment.

The easiest way is to let the command build a Python environment for you. It uses uv, a
free, fast Python package manager, so uv must be installed (see above). Then run, in Stata:

```stata
taxsim_py install, path(folder)
```

This creates the environment in the folder you name (it must not exist yet, or be empty)
and installs taxsim-py into it. You can use it immediately, with no restart. For later
sessions, the command prints the `set python_userpath` line that makes Stata find it again;
run it at the start of a session (or put it in your `profile.do`).

If you already manage your own Python environment, install taxsim-py into it yourself,
with the `readstat` extra, which is how Stata's data reach the library:

```bash
uv add "taxsim-py[readstat]"
```

Then make Stata use that environment; `taxsim_py install, path(<that folder>)` changes
nothing in it and prints the Stata lines to run.

### Update or pin a version

```stata
taxsim_py install, path(folder) update          // newest release
taxsim_py install, path(folder) version(x.y.z)   // a specific release, such as 1.2.3
```

Restart Stata afterwards if the current session had already used taxsim-py. `install` only
changes environments that it created.

### If something is missing

`taxsim_py` checks everything before it calculates. When Python cannot start, taxsim-py or
`polars-readstat` cannot be imported, or taxsim-py is too old, it stops and prints the exact
setting to change.

## Calculate

Load your data, then run `taxsim_py`. You must say what to do with the results: `replace`
merges them into the data in memory on `taxsimid`, and `saving()` writes them to a file.

```stata
use households, clear
taxsim_py, replace
summarize fiitax siitax fica
```

If the data have no `taxsimid`, one is created from the observation number. `year` and
`mstat` are required; `state` is 0 (no state tax) when absent, and absent dollar amounts are
0. Variable names are TAXSIM's, listed under [Inputs](inputs.md). Input variables must be
numeric; a missing value is an error unless you add `missing_to_zero`.

The results are `fiitax siitax fica frate srate ficar tfica`, with `taxsim35`'s variable
labels. See [Outputs](outputs.md).

### An example

NBER's test case, a married couple with $100,000 of long-term capital gains in 1970:

```stata
clear
input state year mstat ltcg
0 1970 2 100000
end
taxsim_py, full interest replace
display fiitax        // 16700.04
```

### Options

| Option | Meaning |
|:--|:--|
| `replace` | Merge the results into the data in memory, replacing existing output variables. |
| `saving(filename)` | Write the results to a file. The extension sets the type: `.dta` (with labels), `.parquet`, `.csv`, `.tsv`, `.arrow`, `.ndjson` or `.sav`. Add `, replace` to overwrite. |
| `full` | Also return the detailed federal and state worksheets, `v10` to `v45`. |
| `secondary`, `wages`, `interest`, `long`, `mortgage` | Which income or deduction the marginal rates `frate` and `srate` are for. The default is the primary earner's wages. |
| `backwards_compatible` | Reproduce TAXSIM's own calculation, errors included. See below. |
| `missing_to_zero` | Treat missing input values as 0. |
| `threads(#)` | Number of threads. Takes effect only on the first call in a session; afterwards set `POLARS_MAX_THREADS` before starting Stata. |

### Statutory law or TAXSIM

By default `taxsim_py` calculates under current statutory law and corrects the places where
TAXSIM departs from it ([the corrections](statutory_corrections.md)). Add
`backwards_compatible` to reproduce TAXSIM exactly, for example to compare with results you
already have from `taxsim35`. That mode is slower, because it runs the pure-Python
implementation.

```stata
taxsim_py, replace backwards_compatible
```

## Good to know

- The first call in a Stata session takes about a second to start Python; later calls are
  fast. For how long the calculation itself takes, compared with the Fortran `taxsim35`
  program, see [Performance](performance.md).
- Results can differ slightly from `taxsim35` at very high incomes, because `taxsim35`
  rounds values when it writes and reads its CSV files and `taxsim_py` does not.
