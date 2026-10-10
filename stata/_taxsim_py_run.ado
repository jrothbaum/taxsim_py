*! taxsim_py 0.2.0  helper for taxsim_py
// _taxsim_py_run program [arguments], what(description): runs it and stops with its output on failure.
program define _taxsim_py_run
	version 16
	syntax anything(name=arguments id="program"), WHAT(string)
	_taxsim_py_exec `arguments'
	if r(rc) != 0 {
		display as error `"`what' failed:"'
		mata: printf("{err}%s\n", st_global("r(text)"))
		exit 693
	}
end
