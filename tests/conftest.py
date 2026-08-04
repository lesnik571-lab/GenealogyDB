import gc

import pytest


@pytest.fixture(autouse=True)
def collect_unreachable_resources():
    """Finalize unreachable resources while pytest can attribute warnings."""
    yield
    gc.collect()
