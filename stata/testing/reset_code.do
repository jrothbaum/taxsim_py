// Copy the working .ado files into PLUS so Stata runs this checkout. Run from stata/testing/.
// Like net install, each file goes in the PLUS subfolder named by its first character.
discard
local source ".."
local files : dir "`source'" files "*.ado"
local more : dir "`source'" files "*.sthlp"
local files `"`files' `more' taxsim_py.pkg"'
foreach file of local files {
	capture confirm file `"`source'/`file'"'
	if _rc continue
	local destination `"`c(sysdir_plus)'`=substr("`file'", 1, 1)'"'
	capture mkdir `"`destination'"'
	copy `"`source'/`file'"' `"`destination'/`file'"', replace
}
discard
clear
