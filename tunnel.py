"""
Automatic Tunnel Manager for ShareIt Remote File Transfer.
Provides instant, secure, globally accessible HTTPS URLs without port forwarding,
router configuration, or firewall rules using Cloudflare Quick Tunnels (trycloudflare.com).
"""

import atexit
import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import time
import urllib.request
from typing import Optional

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")


def get_ssl_context():
    """Returns an SSL context that can verify GitHub certificates."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


def get_cloudflared_download_info() -> tuple[str, str]:
    """Returns (download_url, executable_name) based on the current OS and CPU architecture."""
    system = platform.system().lower()
    machine = platform.machine().lower()

    if "arm" in machine or "aarch64" in machine:
        arch = "arm64"
    elif "386" in machine or "686" in machine:
        arch = "386"
    else:
        arch = "amd64"

    base_url = "https://github.com/cloudflare/cloudflared/releases/latest/download"

    if system == "windows":
        filename = "cloudflared.exe"
        url = f"{base_url}/cloudflared-windows-{arch}.exe"
    elif system == "darwin":
        filename = "cloudflared"
        url = f"{base_url}/cloudflared-darwin-{arch}"
    else:  # linux and others
        filename = "cloudflared"
        url = f"{base_url}/cloudflared-linux-{arch}"

    return url, filename


def ensure_cloudflared_binary() -> Optional[str]:
    """
    Finds or downloads the cloudflared standalone binary.
    Returns the absolute path to the executable, or None if unavailable.
    """
    # 1. Check if cloudflared is already installed in system PATH
    system_cf = shutil.which("cloudflared")
    if system_cf:
        return system_cf

    # 2. Check in local bin/ directory
    os.makedirs(BIN_DIR, exist_ok=True)
    url, filename = get_cloudflared_download_info()
    local_path = os.path.join(BIN_DIR, filename)

    if os.path.exists(local_path) and os.path.getsize(local_path) > 1000000:
        return local_path

    # 3. Download cloudflared binary
    print(f"[*] Downloading Cloudflare Tunnel binary for {platform.system()} ({platform.machine()})...")
    print(f"[*] Source: {url}")

    try:
        ctx = get_ssl_context()
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, context=ctx, timeout=30) as resp:
            total_size = int(resp.headers.get("content-length", 0))
            downloaded = 0
            with open(local_path, "wb") as f_out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f_out.write(chunk)
                    downloaded += len(chunk)
                    if total_size > 0:
                        pct = int((downloaded / total_size) * 100)
                        mb = downloaded / (1024 * 1024)
                        print(f"\r[*] Progress: {pct}% ({mb:.1f} MB)", end="", flush=True)
            print("\n[✔] Cloudflare binary downloaded successfully.")

        # On Unix, ensure executable permissions
        if platform.system() != "Windows":
            os.chmod(local_path, 0o755)

        return local_path
    except Exception as e:
        print(f"\n[!] Failed to download cloudflared: {e}")
        if os.path.exists(local_path):
            try:
                os.remove(local_path)
            except OSError:
                pass
        return None


class TunnelManager:
    """Manages the lifecycle of a Cloudflare Quick Tunnel."""

    def __init__(self, local_port: int):
        self.local_port = local_port
        self.proc: Optional[subprocess.Popen] = None
        self.public_url: Optional[str] = None
        atexit.register(self.stop)

    def start(self, timeout: int = 25) -> Optional[str]:
        """Starts the tunnel and blocks until public URL is obtained or timeout expires."""
        binary_path = ensure_cloudflared_binary()
        if not binary_path:
            return None

        cmd = [binary_path, "tunnel", "--url", f"http://127.0.0.1:{self.local_port}"]
        print(f"[*] Starting Cloudflare Quick Tunnel on port {self.local_port}...")

        # Hide console window on Windows if needed, but keep stdout pipe
        creationflags = 0
        if platform.system() == "Windows" and hasattr(subprocess, "CREATE_NO_WINDOW"):
            creationflags = subprocess.CREATE_NO_WINDOW

        self.proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            creationflags=creationflags
        )

        start_time = time.time()
        url_pattern = re.compile(r"https://[a-zA-Z0-9-]+\.trycloudflare\.com")

        while time.time() - start_time < timeout:
            if self.proc.poll() is not None:
                print(f"[!] Tunnel process exited unexpectedly with code {self.proc.returncode}")
                break

            line = self.proc.stdout.readline()
            if not line:
                time.sleep(0.1)
                continue

            match = url_pattern.search(line)
            if match:
                self.public_url = match.group(0)
                return self.public_url

        print("[!] Timed out waiting for Cloudflare Tunnel public URL.")
        return None

    def stop(self):
        """Terminates the tunnel process cleanly."""
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2)
            except Exception:
                try:
                    self.proc.kill()
                except Exception:
                    pass
            finally:
                self.proc = None
