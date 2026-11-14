"""
ShareIt Receiver Client.
Automatically detects platform (Windows vs Ubuntu/Unix) and launches the appropriate interface:
- Windows: Opens Web Browser to view and download files.
- Ubuntu / Unix: Launches Interactive Remote Terminal File Navigator (ls, cd, pull).
"""

import os
import platform
import sys
import urllib.parse
import webbrowser
from typing import Optional

from receiver.terminal_ui import Colors, RemoteClient, TerminalExplorer


def parse_sender_url(raw_url: str, explicit_token: Optional[str] = None) -> tuple[str, str]:
    """
    Parses a sender URL and extracts base URL and token.
    Supports formats like:
      https://something.trycloudflare.com?token=xyz
      https://something.trycloudflare.com
      http://192.168.1.100:8000?token=xyz
    """
    raw_url = raw_url.strip()
    if not raw_url.startswith("http://") and not raw_url.startswith("https://"):
        raw_url = "https://" + raw_url

    parsed = urllib.parse.urlparse(raw_url)
    base_url = f"{parsed.scheme}://{parsed.netloc}"

    token = explicit_token or ""
    if not token and parsed.query:
        qs = urllib.parse.parse_qs(parsed.query)
        if "token" in qs and qs["token"]:
            token = qs["token"][0]

    return base_url, token


def run_receiver(
    url: str,
    token: Optional[str] = None,
    force_mode: Optional[str] = None,
    pull_target: Optional[str] = None,
    pull_all: bool = False,
    dest_dir: str = "."
):
    """
    Main receiver entrypoint.
    Detects platform, connects to sender, and starts the appropriate interface.
    """
    c = Colors
    base_url, auth_token = parse_sender_url(url, token)

    system_name = platform.system()
    is_windows = system_name == "Windows"
    is_unix = system_name in ["Linux", "Darwin"]

    print(f"[*] Detected Platform: {c.BOLD}{system_name}{c.RESET}")
    print(f"[*] Connecting to Sender: {base_url}...")

    client = RemoteClient(base_url, auth_token)

    # Validate connection and token
    try:
        info = client.get_info()
        print(f"[✔] Successfully connected to Sender ({info.get('sender_platform', 'Unknown')})!")
        print(f"[*] Shared Folder: {c.GREEN}{info.get('shared_name', 'Unknown')}{c.RESET}")
    except Exception as e:
        print(f"[!] Warning: Could not verify connection metadata ({e}).")
        info = {"shared_name": "Shared Folder", "sender_platform": "Remote"}

    # Full browser URL with token
    full_url = f"{base_url}/"
    if auth_token:
        full_url = f"{base_url}/?token={auth_token}"

    # Determine mode: "browser" or "terminal"
    mode = "terminal"
    if force_mode == "browser":
        mode = "browser"
    elif force_mode == "terminal" or force_mode == "cli":
        mode = "terminal"
    elif is_windows:
        mode = "browser"
    else:  # Ubuntu, Linux, macOS
        mode = "terminal"

    # Non-interactive pull mode
    if pull_all or pull_target is not None:
        target = "" if pull_all else (pull_target or "")
        print(f"[*] Non-interactive mode: pulling '{target or '.'}' to '{dest_dir}'...")
        try:
            # Check if directory or file
            try:
                res = client.browse(target)
                dest, count = client.pull_folder(target, dest_dir)
                print(f"[✔] Successfully pulled {count} items into {dest}!")
            except Exception:
                saved = client.pull_file(target, dest_dir)
                print(f"[✔] Successfully pulled file to {saved}!")
            return
        except Exception as e:
            print(f"[!] Pull error: {e}")
            sys.exit(1)

    # 1. WINDOWS RECEIVER -> BROWSER MODE
    if mode == "browser":
        print("\n" + "=" * 70)
        print(f"  {c.CYAN}{c.BOLD}AIRBRIDGE RECEIVER — WINDOWS (Browser Mode){c.RESET}")
        print("=" * 70)
        print(f"[*] Opening your default Web Browser:")
        print(f"    {c.GREEN}{full_url}{c.RESET}")
        print("\n[*] You can browse folders, view files, and download items as ZIP / TAR.")
        print("[*] (Tip: Run with --cli if you want to use the terminal explorer instead)")
        print("=" * 70)

        try:
            webbrowser.open(full_url)
        except Exception as e:
            print(f"[!] Could not open browser automatically: {e}")
            print(f"[*] Please open this URL manually: {full_url}")

        print("\n[+] Browser session active. Press Enter to exit or type 'cli' to open terminal shell:")
        try:
            choice = input("> ").strip().lower()
            if choice in ["cli", "terminal", "t"]:
                explorer = TerminalExplorer(client, info, default_dest=dest_dir)
                explorer.run_loop()
        except (KeyboardInterrupt, EOFError):
            pass

    # 2. UBUNTU / UNIX RECEIVER -> TERMINAL MODE
    else:
        explorer = TerminalExplorer(client, info, default_dest=dest_dir)
        explorer.run_loop()
