"""Build the browser calculator: put the taxsim-py wheel next to web/index.html.

Usage: uv run python scripts/build_web.py   (then serve web/, e.g. `python -m http.server -d web`)
"""

import sys
from pathlib import Path

from build_pure_wheel import build

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

for old in WEB.glob("*.whl"):
    old.unlink()
build(WEB)  # the browser runs the Python engine, so it gets the wheel without the compiled calculator
wheels = list(WEB.glob("taxsim_py-*.whl"))
if len(wheels) != 1:
    sys.exit(f"expected one wheel in {WEB}, found {len(wheels)}")
(WEB / "wheel.txt").write_text(wheels[0].name)
print(f"wrote {wheels[0].name}")
