version 16
set varabbrev off
clear all

// Stata's Python: this repository's venv (whose taxsim-py is this checkout), through its base
// interpreter, which Stata can load on every system.
local venv = regexr(subinstr(c(pwd), "\", "/", .), "/stata/testing/?$", "") + "/.venv"
tempname cfg
file open `cfg' using "`venv'/pyvenv.cfg", read text
file read `cfg' cfg_line
while r(eof) == 0 {
	if regexm(`"`cfg_line'"', "^home *= *(.+)$") local home = subinstr(trim(regexs(1)), "\", "/", .)
	file read `cfg' cfg_line
}
file close `cfg'
if c(os) == "Windows" set python_exec "`home'/python.exe"
else set python_exec "`home'/python3"
python: import glob, site; from sfi import Macro; site.addsitedir(glob.glob(Macro.getLocal("venv") + "/**/site-packages", recursive=True)[0])

tempfile scratch
local folder = regexr(subinstr("`scratch'", "\", "/", .), "/[^/]*$", "")

// 1. NBER's taxsim35 example: married, $100,000 of long-term gains in 1970 -> fiitax 16,700.04.
clear
input state year mstat ltcg
0 1970 2 100000
end
taxsim_py, full interest replace
assert r(N) == 1
assert abs(fiitax - 16700.04) < 1e-6
assert "`: variable label fiitax'" == "Federal Income Tax"
assert "`: variable label v10'" == "Federal AGI"
foreach v in taxsimid fiitax siitax fica frate srate ficar tfica v10 v41 v45 {
	confirm variable `v', exact
}

// A small two-state sample used below.
// (Built with set obs: inside a program, the end that closes input would end the program.)
program define make_sample
	clear
	quietly set obs 3
	generate id = _n
	generate year = cond(_n == 3, 2021, 2022)
	generate state = cond(_n == 2, 33, 5)
	generate mstat = cond(_n == 1, 1, 2)
	generate pwages = cond(_n == 1, 60000, cond(_n == 2, 120000, 30000))
	generate swages = cond(_n == 1, 0, cond(_n == 2, 40000, 10000))
	generate depx = _n - 1
	generate keepme = id * 10
end

// 2. Results must be asked for, and only one marginal-rate option given.
make_sample
capture noisily taxsim_py
assert _rc == 198
capture noisily taxsim_py, replace secondary wages
assert _rc == 198

// 3. Without replace the data are unchanged; saving() writes the results (with labels).
make_sample
datasignature
local before `r(datasignature)'
taxsim_py, saving("`folder'/taxsim_py_results.dta", replace)
datasignature
assert "`r(datasignature)'" == "`before'"
capture confirm variable fiitax, exact
assert _rc != 0
capture noisily taxsim_py, saving("`folder'/taxsim_py_results.dta")
assert _rc == 602
preserve
use "`folder'/taxsim_py_results.dta", clear
assert _N == 3
assert "`: variable label siitax'" == "State Income Tax"
restore

// 4. replace merges on a created taxsimid, keeps the order and other variables, and does not
// add state when the data had none.
make_sample
drop state
gsort -id
generate order = _n
taxsim_py, replace
confirm variable taxsimid fiitax siitax frate, exact
assert taxsimid == _n
assert order == _n
assert keepme == id * 10
capture confirm variable state, exact
assert _rc != 0
assert fiitax > 0 if pwages > 50000

// 5. A rerun with replace replaces the outputs.
quietly summarize fiitax if id == 1
local first = r(mean)
quietly replace pwages = pwages * 2 if id == 1
taxsim_py, replace
quietly summarize fiitax if id == 1
assert r(mean) > `first'

// 6. replace with saving() in another type writes that file too.
make_sample
taxsim_py, replace saving("`folder'/taxsim_py_results.parquet", replace)
confirm file "`folder'/taxsim_py_results.parquet"
confirm variable fiitax, exact

// 7. Missing inputs are an error unless missing_to_zero.
make_sample
quietly replace swages = . in 1
capture noisily taxsim_py, replace
assert _rc == 459
taxsim_py, replace missing_to_zero
assert !missing(fiitax[1])

// 8. Numeric strings are converted; other text is reported.
make_sample
tostring pwages, replace
taxsim_py, replace
assert fiitax[2] > 0
quietly replace pwages = "lots" in 3
capture noisily taxsim_py, replace
assert _rc == 459

// 9. taxsimid must identify observations to merge results back, and may be a string.
make_sample
generate taxsimid = 1
capture noisily taxsim_py, replace
assert _rc == 459
make_sample
generate taxsimid = "household " + string(4 - id)
taxsim_py, replace
confirm string variable taxsimid
assert !missing(fiitax)
assert keepme == id * 10

// 10. statutory, threads() and debug run.
make_sample
taxsim_py, replace statutory threads(2) debug
assert r(N) == 3
confirm file "`r(debug_input)'"
erase "`r(debug_input)'"
erase "`r(debug_results)'"

capture erase "`folder'/taxsim_py_results.dta"
capture erase "`folder'/taxsim_py_results.parquet"
