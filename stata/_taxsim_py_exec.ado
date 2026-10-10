*! taxsim_py 0.2.0  helper for taxsim_py
// _taxsim_py_exec program [arguments]: runs a program with output and errors captured.
// Uses shell, through a temporary script so each argument is quoted once. Where shell
// cannot run (Windows batch mode ignores it), falls back to Python's subprocess through
// Stata's Python integration. Returns r(rc) (0 on success, nonzero on failure,
// -1 if the program could not be started), r(first) (first line of output), r(text) (all).
program define _taxsim_py_exec, rclass
	version 16
	local rest `"`0'"'
	local exec_n 0
	while `"`rest'"' != "" {
		gettoken argument rest : rest
		if `"`argument'"' == "" continue
		local ++exec_n
		local exec_arg`exec_n' `"`argument'"'
	}
	tempfile output status base
	local exec_output `"`output'"'
	local exec_rc -1
	local exec_error ""

	local shell_works = !(c(os) == "Windows" & c(mode) == "batch")
	if `shell_works' {
		local line ""
		forvalues i = 1/`exec_n' {
			local line `"`line' "`exec_arg`i''""'
		}
		local script = cond(c(os) == "Windows", `"`base'.cmd"', `"`base'.sh"')
		tempname handle
		quietly file open `handle' using `"`script'"', write text replace
		if c(os) == "Windows" file write `handle' "@echo off" _n
		file write `handle' `"`line' > "`output'" 2>&1 && (echo 0) > "`status'" || (echo 1) > "`status'""' _n
		file close `handle'
		if c(os) == "Windows" quietly shell "`script'"
		else quietly shell sh "`script'"
		capture erase `"`script'"'
		capture confirm file `"`status'"'
		if _rc local shell_works 0
		else {
			file open `handle' using `"`status'"', read text
			file read `handle' status_line
			file close `handle'
			local exec_rc = cond(trim(`"`status_line'"') == "0", 0, 1)
		}
	}
	if !`shell_works' {
		capture version 16: python: import subprocess; from sfi import Macro
		capture version 16: python: _taxsim_py_args = [Macro.getLocal("exec_arg%d" % i) for i in range(1, int(Macro.getLocal("exec_n")) + 1)]
		if _rc {
			display as error `"taxsim_py could not run `exec_arg1': shell is unavailable here (Windows batch mode ignores it),"'
			display as error "and Stata's Python, used instead, could not start. Run taxsim_py install once in"
			display as error "interactive Stata, or set python_exec to a Python Stata can load ({help python})."
			exit 198
		}
		capture version 16: python: _taxsim_py_out = open(Macro.getLocal("exec_output"), "w"); _taxsim_py_done = subprocess.run(_taxsim_py_args, stdout=_taxsim_py_out, stderr=subprocess.STDOUT); Macro.setLocal("exec_rc", str(_taxsim_py_done.returncode))
		if _rc local exec_error "could not start `exec_arg1'"
		capture version 16: python: _taxsim_py_out.close()
	}

	// Read the output in Mata: program output can hold backticks and quotes, which a
	// Stata file read loop would try to expand as macros.
	local first ""
	local text ""
	capture confirm file `"`output'"'
	if !_rc mata: _taxsim_py_read_output(st_local("output"))
	mata: st_local("text", strtrim(st_local("exec_error") + " " + st_local("text")))
	// copy local returns the text without expanding the backticks and quotes it may hold.
	return scalar rc = `exec_rc'
	return local first : copy local first
	return local text : copy local text
end


version 16
mata:
// Sets the caller's locals first (first non-blank line, trimmed) and text (all lines).
void _taxsim_py_read_output(string scalar path)
{
	string colvector lines, nonblank
	lines = cat(path)
	if (rows(lines) == 0) return
	st_local("text", invtokens(lines', " "))
	nonblank = select(strtrim(lines), strtrim(lines) :!= "")
	if (rows(nonblank)) st_local("first", nonblank[1])
}
end