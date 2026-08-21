import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.hookimpl(wrapper=True)
def pytest_runtest_makereport(item, call):
    """Stash each phase's result on the item so fixtures can see the outcome.

    tests/test_ui.py uses it to label its screenshots pass or fail.
    """
    report = yield
    setattr(item, f"rep_{report.when}", report)
    return report
