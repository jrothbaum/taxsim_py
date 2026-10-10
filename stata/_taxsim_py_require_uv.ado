*! taxsim_py 0.2.0  helper for taxsim_py
// The uv executable: uv(), then PATH, then uv's default install folders.
program define _taxsim_py_require_uv, rclass
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
	_taxsim_py_exec uv --version
	if r(rc) == 0 & regexm(`"`r(first)'"', "^uv ") {
		return local uv uv
		exit
	}
	if c(os) == "Windows" {
		local home : environment USERPROFILE
		local candidates `""`home'/.local/bin/uv.exe" "`home'/.cargo/bin/uv.exe""'
	}
	else {
		local home : environment HOME
		local candidates `""`home'/.local/bin/uv" "`home'/.cargo/bin/uv" "/opt/homebrew/bin/uv" "/usr/local/bin/uv""'
	}
	foreach candidate of local candidates {
		local candidate : subinstr local candidate "\" "/", all
		capture confirm file `"`candidate'"'
		if !_rc {
			return local uv `"`candidate'"'
			exit
		}
	}
	display as error "taxsim_py needs uv to create or update its Python environment, and uv was not found."
	display as error `"Install it from {browse "https://docs.astral.sh/uv/"}, then restart Stata, or give its location with uv()."'
	exit 601
end
