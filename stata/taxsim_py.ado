*! taxsim_py 0.2.0  NBER TAXSIM-compatible tax calculations through the taxsim-py Python package
// Calculations run in Stata's Python (Stata 16+), which the user points at an environment
// with taxsim-py installed. taxsim_py install creates such an environment with uv.
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


// taxsim_py install, path(dir) [update | version(x.y.z)] [uv(path)]
//
// Creates a venv at path() with uv and installs taxsim-py[readstat] from PyPI, or updates it.
// Only touches environments it created (marked by a file in the venv); prints the lines
// that point Stata's Python at the environment, for the user to run.
program define _taxsim_py_install, rclass
	version 16
	syntax , PATH(string) [UPDATE VERSION(string) UV(string)]

	_taxsim_py_minimum_version
	local minimum_version "`r(version)'"
	local marker_name .taxsim_py_managed

	if "`update'" != "" & `"`version'"' != "" {
		display as error "options update and version() may not be combined"
		exit 198
	}
	local spec "taxsim-py[readstat]"
	if `"`version'"' != "" local spec "taxsim-py[readstat]==`version'"
	local upgrade = cond("`update'" != "", "--upgrade", "")

	_taxsim_py_normalize_path `"`path'"'
	local path `"`r(path)'"'
	if c(os) == "Windows" local python `"`path'/Scripts/python.exe"'
	else local python `"`path'/bin/python"'

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
		_taxsim_py_require_uv, uv(`uv')
		local uv `"`r(uv)'"'
		display as text `"Creating a Python environment at `path'"'
		_taxsim_py_run "`uv'" venv "`path'", what("uv venv")
		tempname marker
		quietly file open `marker' using `"`path'/`marker_name'"', write text replace
		file write `marker' "Created by taxsim_py install; taxsim_py install may update this environment." _n
		file close `marker'
		display as text `"Installing `spec'"'
		_taxsim_py_run "`uv'" pip install --python "`python'" "`spec'", what("uv pip install")
		local action created
	}
	else {
		capture confirm file `"`path'/`marker_name'"'
		if _rc {
			display as error `"`path' is a Python environment taxsim_py install did not create, so it will not change it."'
			display as error "To use it, add taxsim-py[readstat] with your own tools (uv add or pip install),"
			display as error "then point Stata's Python at it:"
			_taxsim_py_setup_lines `"`path'"'
			exit 601
		}
		_taxsim_py_installed_version `"`python'"'
		local installed `"`r(version)'"'
		local action none
		if "`update'" != "" | `"`version'"' != "" | `"`installed'"' == "" {
			_taxsim_py_require_uv, uv(`uv')
			local uv `"`r(uv)'"'
			display as text `"Installing `spec' into `path'"'
			_taxsim_py_run "`uv'" pip install `upgrade' --python "`python'" "`spec'", what("uv pip install")
			local action = cond("`update'" != "", "updated", cond(`"`version'"' != "", "version", "installed"))
		}
	}

	_taxsim_py_installed_version `"`python'"'
	local installed `"`r(version)'"'
	if `"`installed'"' == "" {
		display as error `"taxsim-py could not be imported by `python'"'
		exit 601
	}
	_taxsim_py_compare_versions `"`installed'"' `"`minimum_version'"'
	if r(comparison) < 0 {
		display as error `"`path' has taxsim-py `installed'; taxsim_py needs `minimum_version' or newer. Rerun with update."'
		exit 601
	}

	display as text `"`path' has taxsim-py `installed'. To use it, point Stata's Python at it"'
	display as text "(add , permanently to keep the setting for later sessions):"
	_taxsim_py_setup_lines `"`path'"'
	if "`action'" == "updated" | "`action'" == "version" {
		display as text "If Stata's Python is already running, restart Stata to load the new version."
	}
	return local python `"`python'"'
	return local version `"`installed'"'
	return local action `"`action'"'
end


// Checks that Stata's Python can import taxsim-py, and explains how to fix it when not.
// Returns r(version), the taxsim-py version Stata's Python loaded.
program define _taxsim_py_python_ready, rclass
	version 16
	_taxsim_py_minimum_version
	local minimum_version "`r(version)'"
	local loaded ""
	capture python: import importlib.metadata, taxsim_py; from sfi import Macro; Macro.setLocal("loaded", importlib.metadata.version("taxsim-py"))
	if _rc | `"`loaded'"' == "" {
		quietly python query
		local exec `"`r(execpath)'"'
		local running = r(initialized)
		display as error "taxsim_py runs in Stata's Python, which could not import taxsim-py."
		if !`running' display as error `"Stata could not start Python from `exec'."'
		// A venv's python.exe cannot be loaded on Windows; its base interpreter can.
		_taxsim_py_normalize_path `"`exec'"'
		local exec `"`r(path)'"'
		local env = regexr(`"`exec'"', "/(Scripts|bin)/[^/]+$", "")
		capture confirm file `"`env'/pyvenv.cfg"'
		if !_rc & `"`env'"' != `"`exec'"' {
			display as error "That is a virtual environment. Point Stata at its base Python and add the"
			display as error "environment's packages instead, then restart Stata:"
			_taxsim_py_base_lines `"`env'"'
		}
		else {
			display as error `"Point Stata's Python at an environment with taxsim-py[readstat] ({help python:set python_exec}),"'
			display as error "or create one with: taxsim_py install, path(<folder>)"
		}
		exit 601
	}
	_taxsim_py_compare_versions `"`loaded'"' `"`minimum_version'"'
	if r(comparison) < 0 {
		display as error `"Stata's Python has taxsim-py `loaded'; taxsim_py needs `minimum_version' or newer."'
		display as error "Update it (taxsim_py install, path(<folder>) update, or your own tools), then restart Stata."
		exit 601
	}
	return local version `"`loaded'"'
end


// Prints the simple setup line: Stata's Python is the environment's own interpreter.
program define _taxsim_py_setup_lines
	version 16
	args env
	if c(os) == "Windows" local python `"`env'/Scripts/python.exe"'
	else local python `"`env'/bin/python"'
	display as input `"    set python_exec "`python'""'
end


// Prints the fallback setup: the venv's base interpreter (pyvenv.cfg's home), plus its packages.
program define _taxsim_py_base_lines
	version 16
	args env
	local home ""
	local pyversion ""
	tempname cfg
	file open `cfg' using `"`env'/pyvenv.cfg"', read text
	file read `cfg' line
	while r(eof) == 0 {
		if regexm(`"`line'"', "^ *home *= *(.+)$") local home = trim(regexs(1))
		if regexm(`"`line'"', "^ *version(_info)? *= *([0-9]+\.[0-9]+)") local pyversion = regexs(2)
		file read `cfg' line
	}
	file close `cfg'
	_taxsim_py_normalize_path `"`home'"'
	local home `"`r(path)'"'
	if c(os) == "Windows" {
		local base `"`home'/python.exe"'
		local packages `"`env'/Lib/site-packages"'
	}
	else {
		local base `"`home'/python3"'
		local packages `"`env'/lib/python`pyversion'/site-packages"'
	}
	display as input `"    set python_exec "`base'""'
	display as input `"    set python_userpath "`packages'", prepend"'
end


// The installed taxsim-py version, or "" when it is not importable.
program define _taxsim_py_installed_version, rclass
	version 16
	args python
	_taxsim_py_exec "`python'" -c "import importlib.metadata as m; print(m.version('taxsim-py'))"
	local found ""
	if r(rc) == 0 & regexm(`"`r(first)'"', "^[0-9]+(\.[0-9A-Za-z]+)*$") local found `"`r(first)'"'
	return local version `"`found'"'
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
