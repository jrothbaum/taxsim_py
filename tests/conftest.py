"""The suite checks the Python reference implementation unless a test asks for the compiled one.

`calculate_taxes` defaults to the compiled calculator; the tests of taxsim mode, detailed
output and the like need Python, so the default for the test session is Python. Tests of
the compiled calculator (`test_rust_backend.py`) pass `engine="rust"` themselves.
"""

import os

os.environ.setdefault("TAXSIM_PY_ENGINE", "python")
