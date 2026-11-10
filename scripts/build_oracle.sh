#!/bin/sh
# Build the TAXSIM oracles from taxsim_2026_11_09.f (gfortran).
#   taxsim2026.exe      the source as shipped
#   taxsim2026_mtr.exe  same, with the marginal-rate loop starting at 1.
# The shipped source starts that loop at isgn=0, whose zero perturbation exits
# the loop at once, so it always reports frate=srate=ficar=0 (docs/pending_issues.md).
set -e
cd "$(dirname "$0")/.."
SRC=${1:-taxsim_2026_11_09.f}
gfortran -O2 -std=legacy -o taxsim2026.exe "$SRC"
sed 's/do 10 isgn=0,int(extnd(54))/do 10 isgn=1,int(extnd(54))/' "$SRC" > /tmp/taxsim_mtr_patched.f
cmp -s "$SRC" /tmp/taxsim_mtr_patched.f && { echo "mtr patch did not apply" >&2; exit 1; }
gfortran -O2 -std=legacy -o taxsim2026_mtr.exe /tmp/taxsim_mtr_patched.f
