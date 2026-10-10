version 16
set varabbrev off
clear all

// A scratch folder used only by this test, removed at the end.
local tmp = subinstr(c(tmpdir), "\", "/", .)
while substr("`tmp'", -1, 1) == "/" {
	local tmp = substr("`tmp'", 1, strlen("`tmp'") - 1)
}
// Named after a tempfile, which carries Stata's process ID, so runs never share a folder.
tempfile unique
local stamp = subinstr(regexr(subinstr("`unique'", "\", "/", .), "^.*/", ""), ".tmp", "", .)
local root "`tmp'/taxsim_py_test_`stamp'"
mkdir "`root'"
local repo = regexr(subinstr(c(pwd), "\", "/", .), "/stata/testing/?$", "")
local current_pypi 0.1.0
// PyPI's newest taxsim-py is older than this .ado's minimum (0.2.0), so install creates the
// environment and then reports it too old (601). Set to 1 once 0.2.0 is on PyPI.
local pypi_meets_minimum 0
local install_rc = cond(`pypi_meets_minimum', 0, 601)

// Windows batch mode ignores shell, so install runs uv through Stata's Python. Stata cannot
// load a venv's python.exe, so start it from the base interpreter behind this repo's venv.
if c(os) == "Windows" & c(mode) == "batch" {
	tempname cfg
	file open `cfg' using "`repo'/.venv/pyvenv.cfg", read text
	file read `cfg' cfg_line
	while r(eof) == 0 {
		if regexm(`"`cfg_line'"', "^home *= *(.+)$") local base_python = trim(regexs(1)) + "/python.exe"
		file read `cfg' cfg_line
	}
	file close `cfg'
	set python_exec "`base_python'"
}

// 1. Nothing at path(): create a venv and install taxsim-py from PyPI.
capture noisily taxsim_py install, path(`root'/managed)
assert _rc == `install_rc'
confirm file "`root'/managed/.taxsim_py_managed"
if c(os) == "Windows" confirm file "`root'/managed/Scripts/python.exe"
else confirm file "`root'/managed/bin/python"

// 2. Already installed: check only.
capture noisily taxsim_py install, path(`root'/managed)
assert _rc == `install_rc'
if `pypi_meets_minimum' assert "`r(action)'" == "none"

// 3. update and version() change an environment install created.
capture noisily taxsim_py install, path(`root'/managed) update
assert _rc == `install_rc'
if `pypi_meets_minimum' assert "`r(action)'" == "updated"
capture noisily taxsim_py install, path(`root'/managed) version(`current_pypi')
assert _rc == 601
capture noisily taxsim_py install, path(`root'/managed) update version(`current_pypi')
assert _rc == 198

// 4. A non-empty folder that is not a Python environment is refused.
mkdir "`root'/not_an_env"
tempname handle
quietly file open `handle' using "`root'/not_an_env/notes.txt", write text replace
file write `handle' "unrelated" _n
file close `handle'
capture noisily taxsim_py install, path(`root'/not_an_env)
assert _rc == 601

// 5. An environment install did not create is refused, with setup instructions.
_taxsim_py_require_uv
_taxsim_py_run "`r(uv)'" venv "`root'/plain", what("uv venv")
capture noisily taxsim_py install, path(`root'/plain)
assert _rc == 601

// 6. Running needs Stata's Python to import taxsim-py; it cannot yet, so the command explains.
clear
quietly set obs 1
generate year = 2022
generate mstat = 1
generate pwages = 50000
capture noisily taxsim_py, replace
assert _rc == 601

// 7. Once Stata's Python has the environment's packages, the calculation runs or, with PyPI's older
// taxsim-py, the command reports it too old (601).
quietly python query
if r(initialized) {
	python: import glob, site; from sfi import Macro; site.addsitedir(glob.glob(Macro.getLocal("root") + "/managed/**/site-packages", recursive=True)[0])
	capture noisily taxsim_py, replace
	assert _rc == cond(`pypi_meets_minimum', 0, 601)
}

// Clean up the scratch folder.
local root_native = subinstr("`root'", "/", "\", .)
if c(os) == "Windows" _taxsim_py_exec cmd /c rmdir /s /q "`root_native'"
else _taxsim_py_exec rm -rf "`root'"
