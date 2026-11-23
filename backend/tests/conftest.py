import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="mkt_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["DATA_DIR"] = _tmp
os.environ["LLM_MODE"] = "fake"
os.environ["RUNTIME_MODE"] = "subprocess"

import pytest  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture()
def db():
    seed(reset=True)
    s = SessionLocal()
    yield s
    s.close()
