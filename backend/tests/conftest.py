import os
import tempfile

_tmp = tempfile.mkdtemp(prefix="mkt_test_")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["DATA_DIR"] = _tmp
os.environ["LLM_MODE"] = "fake"
os.environ["RUNTIME_MODE"] = "subprocess"
os.environ["AGENTS_DIR"] = _tmp  # hermetic: seeded listings run the SDK example agent, not agents/
os.environ["OPENAI_API_KEY"] = ""  # set (empty) so dotenv cannot load the real key; tests never reach the provider

import pytest  # noqa: E402

from app.db import SessionLocal  # noqa: E402
from app.seed import seed  # noqa: E402


@pytest.fixture()
def db():
    seed(reset=True)
    s = SessionLocal()
    yield s
    s.close()


@pytest.fixture(scope="module")
def live_server():
    """Real uvicorn on a free port so agent subprocesses can call the proxy over HTTP."""
    import socket
    import threading
    import time

    import httpx
    import uvicorn

    from app.config import settings
    from app.main import app

    seed(reset=True)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    settings.public_base_url = f"http://127.0.0.1:{port}"
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    for _ in range(100):
        try:
            httpx.get(f"{settings.public_base_url}/health")
            break
        except httpx.HTTPError:
            time.sleep(0.05)
    yield settings.public_base_url
    server.should_exit = True
    t.join(5)
