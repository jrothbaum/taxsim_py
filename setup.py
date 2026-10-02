from pathlib import Path
import shutil

from setuptools import setup
from setuptools.command.build_py import build_py


class BuildPyWithParameters(build_py):
    """Include the repository's tax-law tables in the wheel."""

    def run(self):
        super().run()
        source = Path("parameters")
        target = Path(self.build_lib) / "taxsim_py" / "parameters"
        shutil.copytree(
            source,
            target,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
        )


setup(cmdclass={"build_py": BuildPyWithParameters})
