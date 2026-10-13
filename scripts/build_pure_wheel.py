"""Build the Python-only wheel (no compiled calculator): `py3-none-any`.

The browser calculator (Pyodide) installs it, and PyPI carries it so platforms without a
compiled wheel can still install `taxsim-py` and use `engine="python"`. pip prefers a
platform wheel where one exists.

Usage: uv run python scripts/build_pure_wheel.py [output-dir]   (default: dist)
"""

import base64
import hashlib
import re
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "taxsim_py"


def build(out_dir: Path) -> Path:
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    name, version = project["name"].replace("-", "_"), project["version"]
    dist_info = f"{name}-{version}.dist-info"
    metadata = [
        "Metadata-Version: 2.4",
        f"Name: {project['name']}",
        f"Version: {version}",
        f"Summary: {project['description']}",
        f"License-Expression: {project['license']}",
        f"Requires-Python: {project['requires-python']}",
        *(f"Requires-Dist: {dep}" for dep in project["dependencies"]),
        *(
            f"Requires-Dist: {dep}; extra == '{extra}'"
            for extra, deps in project.get("optional-dependencies", {}).items()
            for dep in deps
        ),
        *(f"Provides-Extra: {extra}" for extra in project.get("optional-dependencies", {})),
        *(f"Project-URL: {label}, {url}" for label, url in project.get("urls", {}).items()),
        "Description-Content-Type: text/markdown",
        "",
        (ROOT / project["readme"]).read_text(),
    ]
    entry_points = "[console_scripts]\n" + "".join(f"{k} = {v}\n" for k, v in project.get("scripts", {}).items())
    files = {
        str(path.relative_to(PACKAGE.parent)): path.read_bytes()
        for path in sorted(PACKAGE.rglob("*"))
        if path.is_file() and "__pycache__" not in path.parts and not re.match(r"_internal.*\.(so|pyd|dylib)$", path.name)
    }
    files[f"{dist_info}/METADATA"] = "\n".join(metadata).encode()
    files[f"{dist_info}/WHEEL"] = b"Wheel-Version: 1.0\nGenerator: scripts/build_pure_wheel.py\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    files[f"{dist_info}/entry_points.txt"] = entry_points.encode()
    files[f"{dist_info}/licenses/LICENSE"] = (ROOT / "LICENSE").read_bytes()
    record = "".join(
        f"{path},sha256={base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b'=').decode()},{len(data)}\n"
        for path, data in files.items()
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    wheel = out_dir / f"{name}-{version}-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, data in files.items():
            archive.writestr(path, data)
        archive.writestr(f"{dist_info}/RECORD", record + f"{dist_info}/RECORD,,\n")
    return wheel


if __name__ == "__main__":
    print(build(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "dist"))
