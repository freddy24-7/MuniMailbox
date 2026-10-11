import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest


def _free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port: int = probe.getsockname()[1]
        return port


@pytest.fixture(scope="session")
def base_url(tmp_path_factory: pytest.TempPathFactory) -> Iterator[str]:
    """Starts the app once for all browser tests (see tests/e2e/server.py). The name
    `base_url` is what pytest-playwright looks for: `page.goto("/queue")` then works."""
    workdir = tmp_path_factory.mktemp("e2e")
    port = _free_port()
    log = (workdir / "server.log").open("w")
    server = subprocess.Popen(  # noqa: S603  (our own module, fixed arguments)
        [sys.executable, "-m", "tests.e2e.server", str(port), str(workdir)],
        cwd=Path(__file__).resolve().parents[2],
        stdout=log,
        stderr=subprocess.STDOUT,
        env={**os.environ, "PYTHONPATH": "."},
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 90  # loading the language model takes a while
        while True:
            try:
                if httpx.get(f"{url}/readyz", timeout=2).status_code == 200:
                    break
            except httpx.HTTPError:
                pass
            if server.poll() is not None or time.monotonic() > deadline:
                log.flush()
                raise RuntimeError(
                    "the app did not start:\n" + (workdir / "server.log").read_text()[-3000:]
                )
            time.sleep(0.5)
        yield url
    finally:
        server.terminate()
        server.wait(timeout=20)
        log.close()
