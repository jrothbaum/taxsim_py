version 16
set varabbrev off
clear all

// The fallback setup taxsim_py prints for a venv: Stata's Python is the venv's base interpreter
// (pyvenv.cfg's home), with the venv's packages added. Uses this repository's venv, whose
// taxsim-py is this checkout, so it meets the .ado's minimum version.
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

// site.addsitedir rather than python_userpath: the venv installs this checkout in editable
// mode, through a .pth file that only site directories read.
python: import glob, site; from sfi import Macro; site.addsitedir(glob.glob(Macro.getLocal("venv") + "/**/site-packages", recursive=True)[0])

// The check passes and the calculation runs.
clear
quietly set obs 1
generate year = 2022
generate mstat = 1
generate pwages = 50000
taxsim_py, replace
confirm variable fiitax, exact
