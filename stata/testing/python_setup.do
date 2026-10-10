version 16
set varabbrev off
clear all

// The simple setup that taxsim_py install prints: Stata's Python is the venv's own interpreter.
// Uses this repository's venv, which has taxsim-py installed.
local repo = regexr(subinstr(c(pwd), "\", "/", .), "/stata/testing/?$", "")
if c(os) == "Windows" set python_exec "`repo'/.venv/Scripts/python.exe"
else set python_exec "`repo'/.venv/bin/python"

clear
quietly set obs 1
generate year = 2022
generate mstat = 1
generate pwages = 50000
capture noisily taxsim_py, replace
if c(os) == "Windows" {
	// Stata cannot load a venv's python.exe on Windows; the command explains the alternative.
	assert _rc == 601
}
else {
	// Elsewhere the venv's interpreter loads, so the calculation runs.
	assert _rc == 0
}
