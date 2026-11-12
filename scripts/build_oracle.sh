#!/bin/sh
# Build the TAXSIM oracles from taxsim_2026_11_09.f (gfortran).
#   taxsim2026.exe      the source as shipped
#   taxsim2026_mtr.exe  the source with two fixes, used by scripts/oracle.py
# NJ dependent filers (1977-2020) stop the build and are NOT patched: see scripts/oracle.py.
# Fixes (each is a bug in the shipped source; docs/pending_issues.md):
#  1. The marginal-rate loop starts at isgn=0, whose zero perturbation exits the
#     loop at once, so frate=srate=ficar=0 always. Start it at 1.
#  2. povertf stops (5140) for years before 2016, which WV's senior property tax credit
#     reaches. The value only feeds an `if` whose body is commented out, so any
#     value gives the same tax; return the 2016 line.
#  3. Iowa (iatax77) adds the uninitialised `xm` to the head-of-household personal credit for
#     1977-1997, so the tax depends on stack garbage (0 or +$10 in two builds). Use xmp(law),
#     the credit the line above it adds for the same purpose.
#  4. North Carolina (nctax77) 1995-2017 writes `child=min(statax,child)`, a typo for `chld`,
#     which caps the whole per-child care-expense table by the tax and keeps it for every later
#     record in the run, so the child care credit depends on the records before it. Cap `chld`.
set -e
cd "$(dirname "$0")/.."
SRC=${1:-taxsim_2026_11_09.f}
PATCHED=/tmp/taxsim_mtr_patched.f
gfortran -O2 -std=legacy -o taxsim2026.exe "$SRC"
sed -e 's/do 10 isgn=0,int(extnd(54))/do 10 isgn=1,int(extnd(54))/' \
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
# Iowa 1977-1997 head-of-household credit: uninitialised xm -> xmp(law).
python3 - "$PATCHED" <<'PY'
import sys
path = sys.argv[1]
text = open(path).read()
old = "      if(nfile.eq.3)gcred = gcred+xm\n"
assert text.count(old) == 1, "iowa xm patch did not apply"
open(path, "w").write(text.replace(old, "      if(nfile.eq.3)gcred = gcred+xmp(law)\n"))
PY
# North Carolina child credit cap: `child` (the care-expense table) -> `chld`.
python3 - "$PATCHED" <<'PY'
import sys
path = sys.argv[1]
text = open(path).read()
old = "        child=min(statax,child)"
assert text.count(old) == 1, "north carolina child patch did not apply"
open(path, "w").write(text.replace(old, "        chld=min(statax,chld)"))
PY
# New York 2022+: a married-separate return indexes the three-filing-status tables with 4 (out of bounds, so the
# tax is 1e20 or garbage). Use the single table, as the 1977-2021 code does.
python3 - "$PATCHED" <<'PY'
import sys
path = sys.argv[1]
text = open(path).read()
old = "      m=int(filing(mst,1.,2.,3.,4.))\n      mdedsopa=m\n"
assert text.count(old) == 1, "new york married separate patch did not apply"
text = text.replace(old, old + "      mq=m\n      if(m.eq.4) mq=1\n")
for name in ("tab22", "wkst22", "tab24", "wkst24"):
    call = name + "(1,1,m)"
    assert call in text
    text = text.replace(call, name + "(1,1,mq)")
open(path, "w").write(text)
PY
# Tracing (not a fix): with extnd(30)=999 (inputs opt1=30, opt1v=999) the state dispatcher prints every
# nonzero federal result c(i) and input d(i) it hands the state routine, to stderr (scripts/trace_oracle.py).
python3 - "$PATCHED" <<'PY'
import sys
path = sys.argv[1]
text = open(path).read()
old = "      c(74) = stax*flate\n"
assert text.count(old) == 1, "trace patch did not apply"
trace = (
    "      if(int(extnd(30)).eq.999) then\n"
    "        write(0,*) 'TRACE state id, law, flate, stax', id, law, flate, stax\n"
    "        do i=1,255\n"
    "          if(coms(i).ne.0d0) write(0,*) 'TRACE c', i, coms(i)\n"
    "        enddo\n"
    "        do i=1,255\n"
    "          if(ds(i).ne.0d0) write(0,*) 'TRACE d', i, ds(i)\n"
    "        enddo\n"
    "      endif\n"
)
open(path, "w").write(text.replace(old, trace + old))
PY
for pattern in 'isgn=1,int(extnd(54))' 'povert(1,2016)+isize' 'gcred+xmp(law)' 'chld=min(statax,chld)' 'tab22(1,1,mq)' 'TRACE state id'; do
  grep -q "$pattern" "$PATCHED" || { echo "patch did not apply: $pattern" >&2; exit 1; }
done
gfortran -O2 -std=legacy -o taxsim2026_mtr.exe "$PATCHED"
