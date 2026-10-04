"""Build the documentation site, with the browser calculator at /calculator/.

Usage (needs mkdocs and mkdocs-material):
    uv run --no-project --with mkdocs --with mkdocs-material python scripts/build_docs.py
        builds site/ (preview: python -m http.server -d site)
    ... scripts/build_docs.py --deploy
        builds, then pushes the site to the gh-pages branch (publishes it)

Deploy from the commit you want documented, e.g. the release tag you have checked out.
"""

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
CALCULATOR = ROOT / "docs" / "calculator"  # generated; mkdocs copies it into the site as-is

subprocess.run([sys.executable, str(ROOT / "scripts" / "build_web.py")], cwd=ROOT, check=True)
shutil.rmtree(CALCULATOR, ignore_errors=True)
CALCULATOR.mkdir()
for name in ("index.html", "worker.js", "wheel.txt", *(p.name for p in WEB.glob("*.whl"))):
    shutil.copy2(WEB / name, CALCULATOR / name)

try:
    if "--deploy" in sys.argv[1:]:
        subprocess.run([sys.executable, "-m", "mkdocs", "gh-deploy", "--clean", "--force"], cwd=ROOT, check=True)
    else:
        subprocess.run([sys.executable, "-m", "mkdocs", "build", "--clean"], cwd=ROOT, check=True)
finally:
    shutil.rmtree(CALCULATOR, ignore_errors=True)
