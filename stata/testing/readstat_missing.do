version 16
set varabbrev off
clear all

// Stata's Python with taxsim-py but without polars-readstat: the readiness check reports the
// missing readstat extra before anything is calculated.
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
// A None entry in sys.modules makes "import polars_readstat" fail, as if it were not installed.
python: import sys; sys.modules["polars_readstat"] = None

clear
quietly set obs 1
generate year = 2022
generate mstat = 1
generate pwages = 50000
capture noisily taxsim_py, replace
assert _rc == 601
capture confirm variable fiitax, exact
assert _rc != 0
