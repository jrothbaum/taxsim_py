"""Build the browser calculator: put the taxsim-py wheel next to web/index.html.

Usage: uv run python scripts/build_web.py   (then serve web/, e.g. `python -m http.server -d web`)
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"

for old in WEB.glob("*.whl"):
    old.unlink()
subprocess.run(["uv", "build", "--quiet", "--wheel", "-o", str(WEB)], cwd=ROOT, check=True)
shutil.rmtree(ROOT / "build", ignore_errors=True)
(WEB / ".gitignore").unlink(missing_ok=True)  # uv writes one into its output directory
wheels = list(WEB.glob("taxsim_py-*.whl"))
if len(wheels) != 1:
    sys.exit(f"expected one wheel in {WEB}, found {len(wheels)}")
(WEB / "wheel.txt").write_text(wheels[0].name)
print(f"wrote {wheels[0].name}")
