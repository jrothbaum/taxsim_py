#!/bin/sh
# Build the TAXSIM oracles from taxsim_2026_11_09.f (gfortran).
#   taxsim2026.exe      the source as shipped
#   taxsim2026_mtr.exe  the source with three fixes, used by scripts/oracle.py
# Fixes (each is a bug in the shipped source; docs/pending_issues.md):
#  1. The marginal-rate loop starts at isgn=0, whose zero perturbation exits the
#     loop at once, so frate=srate=ficar=0 always. Start it at 1.
#  2. NJ reads its pension exclusion table with the taxpayer count as the index,
#     which is 0 for a dependent filer and reads before the array (stop 229).
#     Use the single-filer table (index 1) then.
#  3. povertf stops (5140) for years before 2016, which WV's senior property tax credit
#     reaches. The value only feeds an `if` whose body is commented out, so any
#     value gives the same tax; return the 2016 line.
set -e
cd "$(dirname "$0")/.."
SRC=${1:-taxsim_2026_11_09.f}
PATCHED=/tmp/taxsim_mtr_patched.f
gfortran -O2 -std=legacy -o taxsim2026.exe "$SRC"
sed -e 's/do 10 isgn=0,int(extnd(54))/do 10 isgn=1,int(extnd(54))/' \
    -e 's/tablki(penpoa(1,1,int(d(7))),4,agi,d)/tablki(penpoa(1,1,max(1,int(d(7)))),4,agi,d)/' \
    "$SRC" > "$PATCHED"
# povertf: replace the stop for early years with the 2016 line.
python3 - "$PATCHED" <<'PY'
import sys
path = sys.argv[1]
text = open(path).read()
old = "       else\n          stop 5140\n       endif"
new = "       else\n          povertf=povert(1,2016)+isize*povert(2,2016)\n       endif"
assert text.count(old) == 1, "povertf patch did not apply"
open(path, "w").write(text.replace(old, new))
PY
for pattern in 'isgn=1,int(extnd(54))' 'max(1,int(d(7)))' 'povert(1,2016)+isize'; do
  grep -q "$pattern" "$PATCHED" || { echo "patch did not apply: $pattern" >&2; exit 1; }
done
gfortran -O2 -std=legacy -o taxsim2026_mtr.exe "$PATCHED"
