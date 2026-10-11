version 16
set varabbrev off
clear all

// Install the package with net install from this checkout into a temporary PLUS folder,
// as users will from SSC or GitHub, without touching the real PLUS folder.
local tmp = subinstr(c(tmpdir), "\", "/", .)
while substr("`tmp'", -1, 1) == "/" {
	local tmp = substr("`tmp'", 1, strlen("`tmp'") - 1)
}
tempfile unique
local stamp = subinstr(regexr(subinstr("`unique'", "\", "/", .), "^.*/", ""), ".tmp", "", .)
local root "`tmp'/taxsim_py_package_`stamp'"
mkdir "`root'"
local source = regexr(subinstr(c(pwd), "\", "/", .), "/testing/?$", "")
local real_plus `"`c(sysdir_plus)'"'
sysdir set PLUS "`root'/"

net install taxsim_py, from("`source'") replace

// Every file in the package lands where Stata looks for it.
foreach file in taxsim_py.ado taxsim_py.sthlp {
	local folder = substr("`file'", 1, 1)
	confirm file "`root'/`folder'/`file'"
}
discard
findfile taxsim_py.ado
assert strpos(subinstr("`r(fn)'", "\", "/", .), "`root'") == 1

// The help file renders as SMCL.
type "`root'/t/taxsim_py.sthlp", smcl

sysdir set PLUS `"`real_plus'"'
discard
// Remove the temporary PLUS folder with Stata's own commands: it holds files and one level
// of subfolders.
local subfolders : dir "`root'" dirs "*"
foreach folder of local subfolders {
	local files : dir "`root'/`folder'" files "*"
	foreach file of local files {
		erase "`root'/`folder'/`file'"
	}
	rmdir "`root'/`folder'"
}
local files : dir "`root'" files "*"
foreach file of local files {
	erase "`root'/`file'"
}
rmdir "`root'"
