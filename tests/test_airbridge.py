"""
AirBridge Integration and Unit Test Suite.
Can be executed via:
    python -m pytest tests/
    python tests/test_airbridge.py
"""

import io
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
import pytest

# Ensure project root is in sys.path
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from receiver.terminal_ui import RemoteClient, TerminalExplorer


def find_free_port() -> int:
    """Finds an available local port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def server_instance():
    """Starts the AirBridge sender server for integration tests."""
    port = find_free_port()
    token = "test_token_xyz123"

    env = os.environ.copy()
    env["SHARE_PATH"] = PROJECT_ROOT
    env["AUTH_TOKEN"] = token

    cmd = [
        sys.executable, "-m", "uvicorn", "sender.server:app",
        "--host", "127.0.0.1", "--port", str(port),
        "--log-level", "warning"
    ]

    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(2)

    base_url = f"http://127.0.0.1:{port}"
    client = RemoteClient(base_url, token)

    yield {
        "proc": proc,
        "base_url": base_url,
        "token": token,
        "client": client,
        "port": port
    }

    proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        proc.kill()


def test_unauthenticated_access_rejected(server_instance):
    """Verifies that invalid or missing tokens are rejected."""
    base_url = server_instance["base_url"]
    bad_client = RemoteClient(base_url, "invalid_secret_token")
    assert not bad_client.ping(), "Ping with bad token should fail"


def test_authenticated_info(server_instance):
    """Verifies that authenticated /api/info returns sender metadata."""
    client = server_instance["client"]
    assert client.ping(), "Ping with valid token should succeed"
    info = client.get_info()
    assert info["status"] == "ok"
    assert "sender_platform" in info
    assert "shared_name" in info


def test_browse_directory(server_instance):
    """Verifies directory listing endpoint."""
    client = server_instance["client"]
    res = client.browse("")
    assert res["status"] == "ok"
    items = res.get("items", [])
    names = [i["name"] for i in items]
    assert "run.py" in names
    assert "sender" in names


def test_file_preview(server_instance):
    """Verifies file preview endpoint."""
    client = server_instance["client"]
    prev = client.preview("run.py")
    assert prev["status"] == "ok"
    assert "AirBridge" in prev["content"]


def test_path_traversal_blocked(server_instance):
    """Verifies that path traversal attacks (../../) are blocked with 403."""
    client = server_instance["client"]
    with pytest.raises(Exception):
        client.browse("../../../Windows")


def test_single_file_pull(server_instance):
    """Verifies single file download with exact byte-for-byte verification."""
    client = server_instance["client"]
    with tempfile.TemporaryDirectory() as temp_dest:
        saved_file = client.pull_file("run.py", temp_dest)
        orig_path = os.path.join(PROJECT_ROOT, "run.py")
        with open(orig_path, "rb") as f_orig, open(saved_file, "rb") as f_pulled:
            assert f_orig.read() == f_pulled.read(), "Downloaded file content must match original"


def test_folder_tar_pull_and_extract(server_instance):
    """Verifies on-the-fly .tar.gz streaming and extraction."""
    client = server_instance["client"]
    with tempfile.TemporaryDirectory() as temp_dest:
        dest, count = client.pull_folder("sender", temp_dest)
        extracted_server_file = os.path.join(temp_dest, "sender", "server.py")
        assert os.path.exists(extracted_server_file), "Extracted folder must contain sender/server.py"
        assert count > 0


def test_dynamic_zip_stream(server_instance):
    """Verifies dynamic .zip archive streaming."""
    base_url = server_instance["base_url"]
    token = server_instance["token"]
    zip_url = f"{base_url}/api/zip?path=sender&token={token}"

    with urllib.request.urlopen(zip_url) as resp:
        zip_bytes = resp.read()
        with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
            names = zf.namelist()
            assert any("server.py" in n for n in names)


def test_terminal_aliases_and_suggestions():
    """Verifies terminal command alias and fuzzy typo suggestion helpers."""
    client = RemoteClient("http://127.0.0.1:9999", "dummy")
    explorer = TerminalExplorer(client, {"shared_name": "test"}, ".")

    assert "pull" in explorer.AVAILABLE_COMMANDS
    assert "ls" in explorer.AVAILABLE_COMMANDS
    assert "cd" in explorer.AVAILABLE_COMMANDS


if __name__ == "__main__":
    print("[*] Running AirBridge Test Suite via pytest...")
    code = pytest.main(["-v", __file__])
    sys.exit(code)
