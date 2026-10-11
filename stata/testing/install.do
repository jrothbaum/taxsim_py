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

// The one-time setup every user needs: a Python Stata can load (here the one behind this
// repository's venv; Stata cannot load a venv's own python.exe on Windows).
tempname cfg
file open `cfg' using "`repo'/.venv/pyvenv.cfg", read text
file read `cfg' cfg_line
while r(eof) == 0 {
	if regexm(`"`cfg_line'"', "^home *= *(.+)$") local home = subinstr(trim(regexs(1)), "\", "/", .)
	file read `cfg' cfg_line
}
file close `cfg'
if c(os) == "Windows" set python_exec "`home'/python.exe"
else set python_exec "`home'/python3"

clear
quietly set obs 1
generate year = 2022
generate mstat = 1
generate pwages = 50000

// 1. Before any install, Stata's Python cannot import taxsim-py, and the command says how to fix it.
capture noisily taxsim_py, replace
assert _rc == 601

// 2. Nothing at path(): create a venv on Stata's Python and install taxsim-py from PyPI.
capture noisily taxsim_py install, path(`root'/managed)
assert _rc == `install_rc'
confirm file "`root'/managed/.taxsim_py_managed"
if c(os) == "Windows" confirm file "`root'/managed/Scripts/python.exe"
else confirm file "`root'/managed/bin/python"

// 3. Once installed, the calculation runs in the same session (no restart) or, with PyPI's
// older taxsim-py, still cannot (601).
capture noisily taxsim_py, replace
assert _rc == `install_rc'

// 4. Already installed: check only.
capture noisily taxsim_py install, path(`root'/managed)
assert _rc == `install_rc'
if `pypi_meets_minimum' assert "`r(action)'" == "none"

// 5. update and version() change an environment install created.
capture noisily taxsim_py install, path(`root'/managed) update
assert _rc == `install_rc'
if `pypi_meets_minimum' assert "`r(action)'" == "updated"
capture noisily taxsim_py install, path(`root'/managed) version(`current_pypi')
assert _rc == 601
capture noisily taxsim_py install, path(`root'/managed) update version(`current_pypi')
assert _rc == 198

// 5b. from() installs this checkout (0.2.0) instead of PyPI's release: install adds the
// environment to this session and the calculation runs without a restart.
taxsim_py install, path(`root'/managed) from(`repo')
assert "`r(action)'" == "from"
assert "`r(version)'" == "0.2.0"
taxsim_py, replace
confirm variable fiitax, exact
drop taxsimid fiitax siitax fica frate srate ficar tfica
capture noisily taxsim_py install, path(`root'/managed) from(`repo') version(0.2.0)
assert _rc == 198
capture noisily taxsim_py install, path(`root'/managed) from(`root'/no_such_checkout)
assert _rc == 601

// 6. A non-empty folder that is not a Python environment is refused.
mkdir "`root'/not_an_env"
tempname handle
quietly file open `handle' using "`root'/not_an_env/notes.txt", write text replace
file write `handle' "unrelated" _n
file close `handle'
capture noisily taxsim_py install, path(`root'/not_an_env)
assert _rc == 601

// 7. An environment install did not create is refused, with the lines to use it instead.
python: import shutil, subprocess; from sfi import Macro; subprocess.run([shutil.which("uv"), "venv", Macro.getLocal("root") + "/plain"], check=True, capture_output=True)
capture noisily taxsim_py install, path(`root'/plain)
assert _rc == 601

// Clean up the scratch folder.
python: import shutil; from sfi import Macro; shutil.rmtree(Macro.getLocal("root"))
