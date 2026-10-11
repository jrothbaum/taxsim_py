*! taxsim_py 0.2.0  NBER TAXSIM-compatible tax calculations through the taxsim-py Python package
// Calculations run in Stata's Python (Stata 16+), which the user points at an environment
// with taxsim-py installed; taxsim_py install creates one with uv. Every external program also
// runs through Stata's Python, never shell, so batch mode works on every system.
program define taxsim_py, rclass
	version 16
	gettoken subcommand rest : 0, parse(" ,")
	if `"`subcommand'"' == "install" {
		_taxsim_py_install `rest'
		return add
		exit
	}
	_taxsim_py_calculate `0'
	return add
end


// taxsim_py [, options]: taxes for the data in memory, with taxsim35's inputs and outputs.
program define _taxsim_py_calculate, rclass
	version 16
	syntax [, Replace SAVing(string asis) Full Secondary Wages Interest Long Mortgage Debug ///
		STATutory MISSing_to_zero THReads(integer 0)]

	// Results are kept only when asked for.
	if "`replace'" == "" & `"`saving'"' == "" {
		display as error "specify replace (merge the results into the data in memory), saving(filename), or both"
		exit 198
	}
	local rate_options `secondary' `wages' `interest' `long' `mortgage'
	if `: word count `rate_options'' > 1 {
		display as error "choose only one of secondary, wages, interest, long and mortgage"
		exit 198
	}
	local mtr 85
	if "`secondary'" != "" local mtr 86
	if "`wages'" != "" local mtr 11
	if "`interest'" != "" local mtr 14
	if "`long'" != "" local mtr 70
	if "`mortgage'" != "" local mtr 56
	if `threads' < 0 {
		display as error "threads() must be a positive number"
		exit 198
	}
	if _N == 0 {
		display as error "no observations"
		exit 2000
	}
	foreach required in year mstat {
		capture confirm variable `required', exact
		if _rc {
			display as error "variable `required' not found; taxsim_py needs at least year and mstat"
			exit 111
		}
	}

	local saving_file ""
	if `"`saving'"' != "" {
		_taxsim_py_parse_saving `saving'
		local saving_file `"`r(file)'"'
	}

	// Polars sizes its thread pool when it first loads, which happens in the readiness check.
	if `threads' > 0 {
		local tpy_pool ""
		capture python: import os, sys; from sfi import Macro; Macro.setLocal("tpy_pool", str(sys.modules["polars"].thread_pool_size()) if "polars" in sys.modules else ""); os.environ.update({} if "polars" in sys.modules else {"POLARS_MAX_THREADS": Macro.getLocal("threads")})
		if "`tpy_pool'" != "" & "`tpy_pool'" != "`threads'" {
			display as text "Note: Python is already running with `tpy_pool' threads; threads(`threads') limits how many"
			display as text "batches run at once. Set POLARS_MAX_THREADS before starting Stata to change the thread pool."
		}
	}
	_taxsim_py_python_ready
	local loaded `"`r(version)'"'

	// taxsim35's input variables (idtl and mtr come from the options, as in taxsim35).
	local inputs taxsimid year state mstat page sage depx dep13 dep17 dep18 pwages swages      ///
		dividends intrec stcg ltcg otherprop nonprop pensions gssi ui transfers rentpaid proptax ///
		otheritem childcare mortgage scorp pbusinc pprofinc sbusinc sprofinc pui sui dep6 dep19  ///
		opt1 opt1v opt2 opt2v age1 age2 age3 psemp ssemp
	local present ""
	foreach name of local inputs {
		capture confirm variable `name', exact
		if !_rc local present `present' `name'
	}
	local has_taxsimid : list posof "taxsimid" in present
	if `has_taxsimid' & "`replace'" != "" {
		capture isid taxsimid
		if _rc {
			display as error "taxsimid must identify observations uniquely, with no missing values, to merge the results back"
			exit 459
		}
	}

	// Working files, named .dta so taxsim-py reads and writes them as Stata files.
	tempfile input_base merge_base
	local input `"`input_base'.dta"'
	local merge_source ""
	if "`replace'" != "" {
		if lower(substr(`"`saving_file'"', -4, .)) == ".dta" local merge_source `"`saving_file'"'
		else local merge_source `"`merge_base'.dta"'
	}
	local tpy_outputs `"`merge_source'"'
	if `"`saving_file'"' != "" & `"`saving_file'"' != `"`merge_source'"' {
		local tpy_outputs = cond(`"`tpy_outputs'"' == "", `"`saving_file'"', `"`tpy_outputs'|`saving_file'"')
	}

	preserve
	quietly keep `present'
	quietly save `"`input'"', replace
	restore

	local tpy_input `"`input'"'
	local tpy_mtr `mtr'
	local tpy_full = "`full'" != ""
	local tpy_statutory = "`statutory'" != ""
	local tpy_missing = "`missing_to_zero'" != ""
	local tpy_workers `threads'
	local tpy_rows ""
	local tpy_error ""
	capture noisily python: from taxsim_py.stata import run_from_stata; run_from_stata()
	local python_rc = _rc
	mata: st_local("failed", strofreal(st_local("tpy_error") != ""))
	if `python_rc' | `failed' {
		if `failed' mata: printf("{err}%s\n", st_local("tpy_error"))
		_taxsim_py_cleanup "`debug'" `"`input'"' `"`merge_base'.dta"'
		exit `=cond(`python_rc', `python_rc', 459)'
	}

	if "`replace'" != "" {
		if !`has_taxsimid' quietly generate double taxsimid = _n
		quietly describe using `"`merge_source'"', varlist
		local results `r(varlist)'
		local keys taxsimid year state
		local added : list results - keys
		tempvar order merged
		quietly generate long `order' = _n
		quietly merge 1:1 taxsimid using `"`merge_source'"', keepusing(`added') update replace generate(`merged')
		quietly count if `merged' < 3
		if r(N) {
			display as error "the results did not match the data on taxsimid"
			_taxsim_py_cleanup "`debug'" `"`input'"' `"`merge_base'.dta"'
			exit 459
		}
		quietly drop `merged'
		sort `order'
	}
	_taxsim_py_cleanup "`debug'" `"`input'"' `"`merge_base'.dta"'

	display as text "taxsim-py `loaded' calculated `tpy_rows' record" plural(`tpy_rows', "", "s") _continue
	if `"`saving_file'"' != "" display as text `"; results saved to `saving_file'"' _continue
	display as text "."
	if "`debug'" != "" {
		return local debug_input `"`input'"'
		capture confirm file `"`merge_base'.dta"'
		if !_rc return local debug_results `"`merge_base'.dta"'
	}
	return scalar N = `tpy_rows'
	return local version `"`loaded'"'
	return local saving `"`saving_file'"'
end


// Parses saving(filename [, replace]) into an absolute path, and checks the file type and
// that an existing file is only overwritten with replace.
program define _taxsim_py_parse_saving, rclass
	version 16
	syntax anything(name=file id="filename") [, REPLACE]
	local file `file'
	local file : subinstr local file "\" "/", all
	if !regexm(`"`file'"', "^(/|~|[A-Za-z]:|//)") local file `"`c(pwd)'/`file'"'
	local file : subinstr local file "\" "/", all
	local extension = lower(substr(`"`file'"', strrpos(`"`file'"', ".") + 1, .))
	local types dta csv tsv txt parquet pq arrow feather ipc ndjson jsonl sav zsav
	if strpos(`"`file'"', ".") == 0 | !`: list extension in types' {
		display as error `"saving(): `file' needs one of these extensions: `types'"'
		exit 198
	}
	if "`replace'" == "" {
		capture confirm new file `"`file'"'
		if _rc {
			display as error `"`file' already exists; use saving(`file', replace) to overwrite it"'
			exit 602
		}
	}
	return local file `"`file'"'
end


// Deletes the working files, or with debug keeps them and says where they are.
program define _taxsim_py_cleanup
	version 16
	args debug input merge_file
	if "`debug'" != "" {
		display as text `"taxsim_py debug: inputs sent to taxsim-py: `input'"'
		capture confirm file `"`merge_file'"'
		if !_rc display as text `"taxsim_py debug: results merged back: `merge_file'"'
		exit
	}
	capture erase `"`input'"'
	capture erase `"`merge_file'"'
end


// taxsim_py install, path(dir) [update | version(x.y.z)] [from(source)] [uv(path)]
//
// from() installs taxsim-py from a local folder or a git URL instead of PyPI (for testing a
// checkout or an unreleased branch); it reinstalls every time, so a checkout's latest code is used.
//
// Creates a venv at path() with uv, built on the Python Stata is running, and installs
// taxsim-py[readstat] from PyPI, or updates it. Because the venv shares Stata's interpreter,
// its packages are added to the running session at once (no restart), and later sessions
// need only python_userpath. Only touches environments it created (marked by a file in the
// venv). Everything runs through Stata's Python, so it works in batch mode too.
program define _taxsim_py_install, rclass
	version 16
	syntax , PATH(string) [UPDATE VERSION(string) FROM(string) UV(string)]

	_taxsim_py_minimum_version
	local minimum_version "`r(version)'"
	local marker_name .taxsim_py_managed

	if "`update'" != "" & `"`version'"' != "" {
		display as error "options update and version() may not be combined"
		exit 198
	}
	if `"`from'"' != "" & `"`version'"' != "" {
		display as error "options from() and version() may not be combined"
		exit 198
	}
	local spec "taxsim-py[readstat]"
	if `"`version'"' != "" local spec "taxsim-py[readstat]==`version'"
	local upgrade = cond("`update'" != "", "--upgrade", "")
	if `"`from'"' != "" {
		// A URL (git+https://..., https://...) or a local folder holding taxsim-py's pyproject.toml.
		if regexm(`"`from'"', "^[A-Za-z+]+://") local spec `"taxsim-py[readstat] @ `from'"'
		else {
			_taxsim_py_normalize_path `"`from'"'
			local from `"`r(path)'"'
			capture confirm file `"`from'/pyproject.toml"'
			if _rc {
				display as error `"from(): `from' is not a URL or a folder with taxsim-py's pyproject.toml"'
				exit 601
			}
			local spec `"`from'[readstat]"'
		}
		local upgrade "--reinstall-package taxsim-py"
	}

	_taxsim_py_normalize_path `"`path'"'
	local path `"`r(path)'"'
	if c(os) == "Windows" local python `"`path'/Scripts/python.exe"'
	else local python `"`path'/bin/python"'
	_taxsim_py_python_starts

	capture confirm file `"`python'"'
	if _rc {
		// Nothing there yet: create a venv, but never inside an unrelated folder.
		mata: st_local("is_dir", strofreal(direxists(st_local("path"))))
		if `is_dir' {
			local files : dir `"`path'"' files "*"
			local dirs : dir `"`path'"' dirs "*"
			if `"`files'`dirs'"' != "" {
				display as error `"`path' is not empty and is not a Python environment"'
				exit 601
			}
		}
		_taxsim_py_find_uv, uv(`uv')
		local uv `"`r(uv)'"'
		// Inside Stata, sys.executable is Stata itself; the interpreter is in sys.base_prefix.
		local stata_python ""
		python: import os, sys; from sfi import Macro; _taxsim_py_base = os.path.join(sys.base_prefix, "python.exe") if os.name == "nt" else os.path.join(sys.base_prefix, "bin", "python%d.%d" % sys.version_info[:2]); Macro.setLocal("stata_python", _taxsim_py_base if os.path.isfile(_taxsim_py_base) else "")
		if `"`stata_python'"' == "" {
			quietly python query
			local stata_python `"`r(execpath)'"'
		}
		display as text `"Creating a Python environment at `path' (on `stata_python')"'
		_taxsim_py_exec "`uv'" venv --python "`stata_python'" "`path'", what("uv venv")
		tempname marker
		quietly file open `marker' using `"`path'/`marker_name'"', write text replace
		file write `marker' "Created by taxsim_py install; taxsim_py install may update this environment." _n
		file close `marker'
		display as text `"Installing `spec'"'
		_taxsim_py_exec "`uv'" pip install --python "`python'" "`spec'", what("uv pip install")
		local action created
	}
	else {
		capture confirm file `"`path'/`marker_name'"'
		if _rc {
			display as error `"`path' is a Python environment taxsim_py install did not create, so it will not change it."'
			display as error "To use it, add taxsim-py[readstat] with your own tools (uv add or pip install), then:"
			_taxsim_py_environment `"`python'"'
			_taxsim_py_setup_lines `"`r(packages)'"' `"`r(base_python)'"' `r(same_python)' error
			exit 601
		}
		_taxsim_py_environment `"`python'"'
		local installed `"`r(version)'"'
		local action none
		if "`update'" != "" | `"`version'"' != "" | `"`from'"' != "" | `"`installed'"' == "" {
			_taxsim_py_find_uv, uv(`uv')
			local uv `"`r(uv)'"'
			display as text `"Installing `spec' into `path'"'
			_taxsim_py_exec "`uv'" pip install `upgrade' --python "`python'" "`spec'", what("uv pip install")
			local action = cond(`"`from'"' != "", "from", cond("`update'" != "", "updated", cond(`"`version'"' != "", "version", "installed")))
		}
	}

	_taxsim_py_environment `"`python'"'
	local installed `"`r(version)'"'
	local packages `"`r(packages)'"'
	local base_python `"`r(base_python)'"'
	local same_python `r(same_python)'
	if `"`installed'"' == "" {
		display as error `"taxsim-py is not installed in `path'"'
		exit 601
	}
	_taxsim_py_compare_versions `"`installed'"' `"`minimum_version'"'
	if r(comparison) < 0 {
		display as error `"`path' has taxsim-py `installed'; taxsim_py needs `minimum_version' or newer. Rerun with update."'
		exit 601
	}

	// Use it now when it shares Stata's interpreter; say what later sessions need.
	if `same_python' {
		python: import site; from sfi import Macro; site.addsitedir(Macro.getLocal("packages"))
		local loaded ""
		python: import sys; from sfi import Macro; _taxsim_py_module = sys.modules.get("taxsim_py"); Macro.setLocal("loaded", getattr(_taxsim_py_module, "__version__", "an older version") if _taxsim_py_module else "")
		display as text `"`path' has taxsim-py `installed', ready to use in this session."'
		if "`loaded'" != "" & "`loaded'" != "`installed'" display as text "This session already loaded taxsim-py `loaded'; restart Stata to use `installed'."
	}
	else display as text `"`path' has taxsim-py `installed'."'
	_taxsim_py_setup_lines `"`packages'"' `"`base_python'"' `same_python' text
	return local python `"`python'"'
	return local version `"`installed'"'
	return local action `"`action'"'
end


// Checks that Stata's Python can import taxsim-py and polars-readstat, and explains how to
// fix it when not. Returns r(version), the taxsim-py version Stata's Python loaded.
program define _taxsim_py_python_ready, rclass
	version 16
	_taxsim_py_minimum_version
	local minimum_version "`r(version)'"
	_taxsim_py_python_starts
	local loaded ""
	local interpreter ""
	capture python: import importlib.metadata, taxsim_py; from sfi import Macro; Macro.setLocal("loaded", getattr(taxsim_py, "__version__", None) or importlib.metadata.version("taxsim-py"))
	python: import sys; from sfi import Macro; Macro.setLocal("interpreter", sys.executable)
	if `"`loaded'"' == "" {
		display as error `"Stata's Python (`interpreter') cannot import taxsim-py. Either create an environment for it:"'
		display as input "    taxsim_py install, path(<folder>)"
		display as error "or add taxsim-py[readstat] to an environment you use and point Stata at it"
		display as error "(taxsim_py install, path(<that folder>) prints the lines to run)."
		exit 601
	}
	_taxsim_py_compare_versions `"`loaded'"' `"`minimum_version'"'
	if r(comparison) < 0 {
		display as error `"Stata's Python has taxsim-py `loaded'; taxsim_py needs `minimum_version' or newer."'
		display as error "Update it (taxsim_py install, path(<folder>) update, or your own tools), then restart Stata."
		exit 601
	}
	// The data go to taxsim-py and back as .dta files, which need polars-readstat.
	capture python: import polars_readstat
	if _rc {
		display as error "Stata's Python has taxsim-py `loaded' but not polars-readstat, which taxsim_py needs to"
		display as error "pass .dta files to taxsim-py and back. Add the readstat extra to that environment:"
		display as input `"    uv add "taxsim-py[readstat]"     or     pip install "taxsim-py[readstat]""'
		display as error "then restart Stata."
		exit 601
	}
	return local version `"`loaded'"'
end


// Stops with setup instructions when Stata's Python cannot start: taxsim_py needs it both to
// calculate and to run uv (it never uses shell, which Windows batch mode ignores).
program define _taxsim_py_python_starts
	version 16
	capture python: import sys
	if !_rc exit
	quietly python query
	local exec `"`r(execpath)'"'
	display as error `"taxsim_py runs in Stata's Python, which could not start from `exec'."'
	_taxsim_py_normalize_path `"`exec'"'
	local env = regexr(`"`r(path)'"', "/(Scripts|bin)/[^/]+$", "")
	capture confirm file `"`env'/pyvenv.cfg"'
	if !_rc & `"`env'"' != `"`exec'"' {
		// A venv's python.exe cannot be loaded on Windows; its base interpreter can.
		display as error "That is a virtual environment. Point Stata at the Python it was built on instead, then restart Stata:"
		_taxsim_py_base_python `"`env'"'
		display as input `"    set python_exec "`r(base_python)'", permanently"'
	}
	else {
		display as error "Point Stata at a Python 3.9 or newer installation (for example from python.org;"
		display as error "on Windows, not a virtual environment's python.exe), then restart Stata:"
		display as input `"    set python_exec "<path to python>", permanently"'
		display as error "See {help python}."
	}
	exit 601
end


// _taxsim_py_exec program [arguments] [, what(description)]: runs a program through Stata's
// Python, each argument passed as is, so it works in batch mode on every system. Returns
// r(rc) (the exit code, -1 if it could not start), r(first) (first non-blank line of output)
// and r(text) (all of it). With what(), stops with the output when the program fails.
program define _taxsim_py_exec, rclass
	version 16
	syntax anything(name=arguments id="program") [, WHAT(string)]
	_taxsim_py_python_starts
	local rest `"`arguments'"'
	local exec_n 0
	while `"`rest'"' != "" {
		gettoken argument rest : rest
		if `"`argument'"' == "" continue
		local ++exec_n
		local exec_arg`exec_n' `"`argument'"'
	}
	local exec_rc -1
	local exec_first ""
	local exec_text ""
	// Output is set through sfi, so backticks and quotes in it are never expanded as macros.
	capture python: import subprocess; from sfi import Macro; _taxsim_py_done = subprocess.run([Macro.getLocal("exec_arg%d" % i) for i in range(1, int(Macro.getLocal("exec_n")) + 1)], stdout=subprocess.PIPE, stderr=subprocess.STDOUT, encoding="utf-8", errors="replace", creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)); Macro.setLocal("exec_rc", str(_taxsim_py_done.returncode)); Macro.setLocal("exec_text", _taxsim_py_done.stdout.strip()); Macro.setLocal("exec_first", next((line.strip() for line in _taxsim_py_done.stdout.splitlines() if line.strip()), ""))
	if _rc mata: st_local("exec_text", "could not start " + st_local("exec_arg1"))
	if `"`what'"' != "" & `exec_rc' != 0 {
		display as error `"`what' failed:"'
		mata: printf("{err}%s\n", st_local("exec_text"))
		exit 693
	}
	return scalar rc = `exec_rc'
	return local first : copy local exec_first
	return local text : copy local exec_text
end


// The uv executable: uv(), then the search path, then uv's default install folders.
program define _taxsim_py_find_uv, rclass
	version 16
	syntax [, UV(string)]
	if `"`uv'"' != "" {
		capture confirm file `"`uv'"'
		if _rc {
			display as error `"uv not found at `uv'"'
			exit 601
		}
		return local uv `"`uv'"'
		exit
	}
	local found ""
	python: import os, shutil; from sfi import Macro; _taxsim_py_uv = "uv.exe" if os.name == "nt" else "uv"; _taxsim_py_home = os.path.expanduser("~"); Macro.setLocal("found", shutil.which("uv") or next((p for p in [os.path.join(_taxsim_py_home, ".local", "bin", _taxsim_py_uv), os.path.join(_taxsim_py_home, ".cargo", "bin", _taxsim_py_uv), "/opt/homebrew/bin/uv", "/usr/local/bin/uv"] if os.path.isfile(p)), ""))
	if `"`found'"' == "" {
		display as error "taxsim_py install needs uv to create the Python environment, and uv was not found."
		display as error `"Install it from {browse "https://docs.astral.sh/uv/"}, or give its location with uv()."'
		exit 601
	}
	return local uv `"`found'"'
end


// Facts about the venv whose interpreter is python, read from its files in Stata's Python
// (no process started): r(packages) its site-packages folder, r(version) its taxsim-py ("" if
// not installed), r(base_python) the Python it was built on (from pyvenv.cfg), and
// r(same_python) 1 when that is the Python Stata is running.
program define _taxsim_py_environment, rclass
	version 16
	args python
	_taxsim_py_python_starts
	_taxsim_py_normalize_path `"`python'"'
	local env = regexr(`"`r(path)'"', "/(Scripts|bin)/[^/]+$", "")
	local packages ""
	local version ""
	local base_python ""
	local same_python 0
	python: import glob, os; from sfi import Macro; _taxsim_py_env = Macro.getLocal("env"); _taxsim_py_found = sorted(glob.glob(os.path.join(_taxsim_py_env, "Lib", "site-packages")) + glob.glob(os.path.join(_taxsim_py_env, "lib", "python*", "site-packages"))); Macro.setLocal("packages", _taxsim_py_found[0] if _taxsim_py_found else "")
	// The installed version from the package metadata in that folder, without importing it.
	if `"`packages'"' != "" {
		python: import importlib.metadata, re; from sfi import Macro; Macro.setLocal("version", next((d.version for d in importlib.metadata.distributions(path=[Macro.getLocal("packages")]) if re.sub(r"[-_.]+", "-", (d.metadata["Name"] or "").lower()) == "taxsim-py"), ""))
	}
	capture python: import os, sys; from sfi import Macro; _taxsim_py_cfg = dict((k.strip().lower(), v.strip()) for k, _, v in (line.partition("=") for line in open(os.path.join(Macro.getLocal("env"), "pyvenv.cfg"), encoding="utf-8") if "=" in line)); _taxsim_py_home = _taxsim_py_cfg.get("home", ""); _taxsim_py_minor = ".".join((_taxsim_py_cfg.get("version_info") or _taxsim_py_cfg.get("version") or "3").split(".")[:2]); Macro.setLocal("base_python", os.path.join(_taxsim_py_home, "python.exe") if os.name == "nt" else os.path.join(_taxsim_py_home, "python" + _taxsim_py_minor)); _taxsim_py_same = lambda p: os.path.normcase(os.path.realpath(p)); Macro.setLocal("same_python", "1" if _taxsim_py_home and _taxsim_py_same(_taxsim_py_home) in (_taxsim_py_same(sys.base_prefix), _taxsim_py_same(os.path.join(sys.base_prefix, "bin"))) else "0")
	_taxsim_py_normalize_path `"`packages'"'
	return local packages `"`r(path)'"'
	_taxsim_py_normalize_path `"`base_python'"'
	return local base_python `"`r(path)'"'
	return local version `"`version'"'
	return local same_python `same_python'
end


// Prints the lines that point Stata's Python at an environment for later sessions: its packages,
// and its interpreter when that is not the one Stata runs now. style is text or error.
program define _taxsim_py_setup_lines
	version 16
	args packages base_python same_python style
	if `same_python' display as `style' "To use it in later sessions too, run once:"
	else display as `style' "Stata runs a different Python. To use this environment, run once and restart Stata:"
	if !`same_python' display as input `"    set python_exec "`base_python'", permanently"'
	display as input `"    set python_userpath "`packages'", prepend permanently"'
end


// The Python a venv was built on, from its pyvenv.cfg (readable even when no Python runs).
program define _taxsim_py_base_python, rclass
	version 16
	args env
	local home ""
	tempname cfg
	file open `cfg' using `"`env'/pyvenv.cfg"', read text
	file read `cfg' line
	while r(eof) == 0 {
		if regexm(`"`line'"', "^ *home *= *(.+)$") local home = trim(regexs(1))
		file read `cfg' line
	}
	file close `cfg'
	_taxsim_py_normalize_path `"`home'"'
	if c(os) == "Windows" return local base_python `"`r(path)'/python.exe"'
	else return local base_python `"`r(path)'/python3"'
end


// r(comparison) is -1, 0 or 1 as version a is older than, equal to or newer than version b.
program define _taxsim_py_compare_versions, rclass
	version 16
	args a b
	local a : subinstr local a "." " ", all
	local b : subinstr local b "." " ", all
	local comparison 0
	forvalues i = 1/4 {
		local x : word `i' of `a'
		local y : word `i' of `b'
		foreach part in x y {
			if regexm("``part''", "^([0-9]+)") local `part' = regexs(1)
			else local `part' 0
		}
		if `comparison' == 0 & `x' != `y' local comparison = cond(`x' < `y', -1, 1)
	}
	return scalar comparison = `comparison'
end


// The oldest taxsim-py this .ado works with: the release that matches it.
program define _taxsim_py_minimum_version, rclass
	version 16
	return local version 0.2.0
end


program define _taxsim_py_normalize_path, rclass
	version 16
	args path
	local path : subinstr local path "\" "/", all
	while strlen(`"`path'"') > 1 & substr(`"`path'"', -1, 1) == "/" {
		local path = substr(`"`path'"', 1, strlen(`"`path'"') - 1)
	}
	return local path `"`path'"'
end
