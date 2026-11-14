"""
AirBridge Terminal UI — Interactive Remote Terminal Explorer for Ubuntu / Unix.
Allows navigating remote directories and pulling files/folders directly from terminal.
Features:
- Tab auto-completion for commands and remote filenames
- Command aliases (get -> pull, dir -> ls, view -> cat)
- Smart typo suggestions (e.g. 'sl' -> 'Did you mean ls?')
- Progress bar with transfer speed indicator
- Works with pure Python standard library (zero external dependencies)
"""

import difflib
import io
import json
import os
import platform
import shutil
import ssl
import sys
import tarfile
import time
import urllib.parse
import urllib.request
from typing import Optional


# ANSI color codes
class Colors:
    CYAN = "\033[96m"
    GREEN = "\033[92m"
    YELLOW = "\033[93m"
    BLUE = "\033[94m"
    MAGENTA = "\033[95m"
    RED = "\033[91m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


def get_ssl_context():
    """Returns SSL context compatible with public HTTPS tunnels."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        pass
    try:
        return ssl.create_default_context()
    except Exception:
        return ssl._create_unverified_context()


class RemoteClient:
    """Client for interacting with the AirBridge Sender REST API."""

    def __init__(self, base_url: str, token: str = ""):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.ssl_ctx = get_ssl_context()
        self.opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=self.ssl_ctx))

    def _request(self, endpoint: str, params: Optional[dict] = None) -> tuple[int, bytes]:
        """Makes an authenticated GET request to the sender server."""
        query_params = {}
        if self.token:
            query_params["token"] = self.token
        if params:
            query_params.update(params)

        qs = urllib.parse.urlencode(query_params)
        full_url = f"{self.base_url}{endpoint}"
        if qs:
            full_url = f"{full_url}?{qs}"

        req = urllib.request.Request(
            full_url,
            headers={
                "User-Agent": "AirBridge-Terminal-Client/1.0",
                "Authorization": f"Bearer {self.token}" if self.token else ""
            }
        )

        with self.opener.open(req, timeout=15) as resp:
            return resp.status, resp.read()

    def ping(self) -> bool:
        """Verifies connection and authentication."""
        try:
            status, _ = self._request("/api/ping")
            return status == 200
        except Exception:
            return False

    def get_info(self) -> dict:
        """Fetches sender system metadata."""
        _, data = self._request("/api/info")
        return json.loads(data.decode("utf-8"))

    def browse(self, path: str = "") -> dict:
        """Fetches directory listing for path."""
        _, data = self._request("/api/browse", {"path": path})
        return json.loads(data.decode("utf-8"))

    def preview(self, path: str) -> dict:
        """Fetches text preview for a file."""
        _, data = self._request("/api/preview", {"path": path})
        return json.loads(data.decode("utf-8"))

    def pull_file(self, remote_path: str, local_dest_dir: str = ".") -> str:
        """Downloads a remote file with progress bar."""
        query_params = {"path": remote_path}
        if self.token:
            query_params["token"] = self.token
        qs = urllib.parse.urlencode(query_params)
        full_url = f"{self.base_url}/api/file?{qs}"

        req = urllib.request.Request(
            full_url,
            headers={"Authorization": f"Bearer {self.token}" if self.token else ""}
        )

        with self.opener.open(req, timeout=60) as resp:
            total_size = int(resp.headers.get("content-length", 0))
            filename = os.path.basename(remote_path)
            local_path = os.path.join(local_dest_dir, filename)

            os.makedirs(os.path.dirname(os.path.abspath(local_path)), exist_ok=True)

            downloaded = 0
            start_time = time.time()
            last_print = 0

            with open(local_path, "wb") as f_out:
                while True:
                    chunk = resp.read(64 * 1024)
                    if not chunk:
                        break
                    f_out.write(chunk)
                    downloaded += len(chunk)

                    now = time.time()
                    if now - last_print > 0.15:
                        last_print = now
                        elapsed = max(0.01, now - start_time)
                        speed = (downloaded / (1024 * 1024)) / elapsed
                        if total_size > 0:
                            pct = int((downloaded / total_size) * 100)
                            bar_len = 25
                            filled = int(bar_len * downloaded // total_size)
                            bar = "=" * filled + "-" * (bar_len - filled)
                            mb_done = downloaded / (1024 * 1024)
                            mb_tot = total_size / (1024 * 1024)
                            sys.stdout.write(f"\r  [{bar}] {pct}% ({mb_done:.1f}/{mb_tot:.1f} MB, {speed:.1f} MB/s) ")
                        else:
                            mb_done = downloaded / (1024 * 1024)
                            sys.stdout.write(f"\r  Downloading... {mb_done:.1f} MB ({speed:.1f} MB/s) ")
                        sys.stdout.flush()

            sys.stdout.write("\n")
            return local_path

    def pull_folder(self, remote_path: str, local_dest_dir: str = ".") -> tuple[str, int]:
        """Downloads a remote folder as tar.gz and extracts it on the fly."""
        query_params = {"path": remote_path}
        if self.token:
            query_params["token"] = self.token
        qs = urllib.parse.urlencode(query_params)
        full_url = f"{self.base_url}/api/tar?{qs}"

        req = urllib.request.Request(
            full_url,
            headers={"Authorization": f"Bearer {self.token}" if self.token else ""}
        )

        with self.opener.open(req, timeout=120) as resp:
            downloaded = 0
            start_time = time.time()
            last_print = 0
            chunks = []

            sys.stdout.write("  Streaming and extracting folder... ")
            sys.stdout.flush()

            while True:
                chunk = resp.read(64 * 1024)
                if not chunk:
                    break
                chunks.append(chunk)
                downloaded += len(chunk)

                now = time.time()
                if now - last_print > 0.2:
                    last_print = now
                    elapsed = max(0.01, now - start_time)
                    speed = (downloaded / (1024 * 1024)) / elapsed
                    mb_done = downloaded / (1024 * 1024)
                    sys.stdout.write(f"\r  Streaming folder: {mb_done:.1f} MB received ({speed:.1f} MB/s)... ")
                    sys.stdout.flush()

            sys.stdout.write("\n")

            # Extract in memory
            tar_bytes = io.BytesIO(b"".join(chunks))
            count = 0
            with tarfile.open(fileobj=tar_bytes, mode="r:gz") as tar:
                # Security: check members for path traversal
                for member in tar.getmembers():
                    if member.name.startswith("/") or ".." in member.name:
                        continue
                    tar.extract(member, path=local_dest_dir)
                    count += 1

            return local_dest_dir, count


class TerminalExplorer:
    """Interactive command-line shell for exploring and pulling remote files."""

    AVAILABLE_COMMANDS = [
        "ls", "dir", "cd", "pwd", "pull", "get", "download",
        "cat", "preview", "view", "info", "clear", "cls", "help", "exit", "quit"
    ]

    def __init__(self, client: RemoteClient, initial_info: dict, default_dest: str = "."):
        self.client = client
        self.info = initial_info
        self.current_path = ""
        self.dest_dir = os.path.abspath(default_dest)
        self._cached_items: list[str] = []
        self._setup_readline()

    def _setup_readline(self):
        """Configures tab completion for commands and remote filenames."""
        try:
            import readline

            def completer(text, state):
                line = readline.get_line_buffer()
                tokens = line.split()

                # If completing first word: suggest commands
                if not tokens or (len(tokens) == 1 and not line.endswith(" ")):
                    options = [cmd for cmd in self.AVAILABLE_COMMANDS if cmd.startswith(text)]
                else:
                    # Completing filename/path arguments
                    if not self._cached_items:
                        try:
                            res = self.client.browse(self.current_path)
                            self._cached_items = [
                                item["name"] + ("/" if item["is_dir"] else "")
                                for item in res.get("items", [])
                            ]
                        except Exception:
                            self._cached_items = []
                    options = [name for name in self._cached_items if name.startswith(text)]

                if state < len(options):
                    return options[state]
                return None

            readline.set_completer(completer)
            readline.parse_and_bind("tab: complete")
            # Don't split words on slashes so path completion works
            readline.set_completer_delims(" \t\n")
        except ImportError:
            pass

    def print_banner(self):
        c = Colors
        shared_name = self.info.get("shared_name", "Shared Folder")
        sender_os = self.info.get("sender_platform", "Unknown")
        print(f"\n{c.CYAN}{c.BOLD}" + "=" * 70)
        print("  AIRBRIDGE — REMOTE TERMINAL EXPLORER")
        print(f"  Connected to Sender: {sender_os} | Shared Root: {shared_name}")
        print(f"  Local Download Path: {self.dest_dir}")
        print("=" * 70 + f"{c.RESET}")
        print(f"Terminal Suggestions:")
        print(f"  • {c.GREEN}ls{c.RESET}              - List files & directories in current remote folder")
        print(f"  • {c.GREEN}cd <folder>{c.RESET}     - Navigate into remote folder ({c.DIM}<Tab> to autocomplete{c.RESET})")
        print(f"  • {c.GREEN}pull <file>{c.RESET}     - Download a single file to local disk")
        print(f"  • {c.GREEN}pull .{c.RESET}          - Download & extract this entire folder")
        print(f"  • {c.GREEN}cat <file>{c.RESET}      - Preview file contents in terminal")
        print(f"  • {c.GREEN}info{c.RESET}            - Show connection info & copy-paste bash one-liners")
        print("-" * 70)

    def cmd_ls(self, arg: str = ""):
        c = Colors
        target_path = self.current_path
        if arg:
            target_path = f"{self.current_path}/{arg}".strip("/").replace("\\", "/")

        try:
            res = self.client.browse(target_path)
            items = res.get("items", [])
            self._cached_items = [
                item["name"] + ("/" if item["is_dir"] else "")
                for item in items
            ]
            if not items:
                print(f"  {c.DIM}(Directory is empty){c.RESET}")
                return

            print(f"  {c.BOLD}{'TYPE':<8} {'SIZE':<12} {'MODIFIED':<18} {'NAME'}{c.RESET}")
            print("  " + "-" * 62)

            for item in items:
                if item["is_dir"]:
                    type_str = f"{c.BLUE}[DIR]{c.RESET}"
                    name_str = f"{c.BLUE}{c.BOLD}{item['name']}/{c.RESET}"
                else:
                    type_str = f"{c.GREEN}[FILE]{c.RESET}"
                    name_str = f"{item['name']}"

                size_str = item["size_str"]
                mtime_str = item["mtime_str"]
                print(f"  {type_str:<17} {size_str:<12} {mtime_str:<18} {name_str}")

            print(f"  {c.DIM}Total: {len(items)} items{c.RESET}")
            print(f"  {c.YELLOW}Suggestion: type 'pull .' to download this entire folder, or 'pull <name>' for one item.{c.RESET}")
        except Exception as e:
            print(f"  {c.RED}[!] Error listing directory: {e}{c.RESET}")

    def cmd_cd(self, arg: str):
        c = Colors
        if not arg or arg == "~" or arg == "/":
            self.current_path = ""
            self._cached_items = []
            return

        if arg == "..":
            if self.current_path:
                parts = self.current_path.split("/")
                self.current_path = "/".join(parts[:-1])
            self._cached_items = []
            return

        target = f"{self.current_path}/{arg}".strip("/").replace("\\", "/")
        try:
            # Verify directory exists
            res = self.client.browse(target)
            self.current_path = res.get("current_path", target)
            self._cached_items = [
                item["name"] + ("/" if item["is_dir"] else "")
                for item in res.get("items", [])
            ]
            print(f"  {c.DIM}Entered: /{self.current_path}{c.RESET}")
        except Exception:
            print(f"  {c.RED}[!] cd failed: Directory '{arg}' not found{c.RESET}")
            # Suggest matching folders
            matches = difflib.get_close_matches(arg, self._cached_items, n=2, cutoff=0.4)
            if matches:
                print(f"  {c.YELLOW}Did you mean: cd {matches[0]}?{c.RESET}")

    def cmd_pwd(self):
        print(f"  /{self.current_path}")

    def cmd_cat(self, arg: str):
        c = Colors
        if not arg:
            print(f"  {c.YELLOW}Usage: cat <filename>{c.RESET}")
            return
        target = f"{self.current_path}/{arg}".strip("/").replace("\\", "/")
        try:
            res = self.client.preview(target)
            content = res.get("content", "")
            print(f"{c.CYAN}--- {res.get('filename')} ({res.get('size')} bytes) ---{c.RESET}")
            print(content)
            print(f"{c.CYAN}--- End of preview ---{c.RESET}")
        except Exception as e:
            print(f"  {c.RED}[!] Preview failed: {e}{c.RESET}")
            matches = difflib.get_close_matches(arg, self._cached_items, n=2, cutoff=0.4)
            if matches:
                print(f"  {c.YELLOW}Did you mean: cat {matches[0]}?{c.RESET}")

    def cmd_pull(self, arg: str):
        c = Colors
        if not arg:
            print(f"  {c.YELLOW}Usage: pull <file_or_folder> | pull . | pull *{c.RESET}")
            return

        # Check if pulling current folder
        if arg in [".", "*"]:
            target = self.current_path
            folder_name = self.current_path.split("/")[-1] if self.current_path else self.info.get("shared_name", "downloaded")
            print(f"  {c.CYAN}[*] Pulling current directory '{folder_name}' to {self.dest_dir}...{c.RESET}")
            try:
                dest, count = self.client.pull_folder(target, self.dest_dir)
                print(f"  {c.GREEN}[✔] Successfully pulled {count} items into {dest}!{c.RESET}")
            except Exception as e:
                print(f"  {c.RED}[!] Pull failed: {e}{c.RESET}")
            return

        target = f"{self.current_path}/{arg}".strip("/").replace("\\", "/")

        try:
            listing = self.client.browse(self.current_path)
            is_dir = False
            for item in listing.get("items", []):
                if item["name"] == arg:
                    is_dir = item["is_dir"]
                    break

            if is_dir:
                print(f"  {c.CYAN}[*] Pulling folder '{arg}' as archive...{c.RESET}")
                dest, count = self.client.pull_folder(target, self.dest_dir)
                print(f"  {c.GREEN}[✔] Successfully pulled and extracted {count} items into {dest}!{c.RESET}")
            else:
                print(f"  {c.CYAN}[*] Pulling file '{arg}'...{c.RESET}")
                local_path = self.client.pull_file(target, self.dest_dir)
                print(f"  {c.GREEN}[✔] Saved to: {local_path}{c.RESET}")
        except Exception:
            try:
                dest, count = self.client.pull_folder(target, self.dest_dir)
                print(f"  {c.GREEN}[✔] Successfully pulled {count} items!{c.RESET}")
            except Exception:
                try:
                    local_path = self.client.pull_file(target, self.dest_dir)
                    print(f"  {c.GREEN}[✔] Saved to: {local_path}{c.RESET}")
                except Exception as e2:
                    print(f"  {c.RED}[!] Pull failed: {e2}{c.RESET}")
                    matches = difflib.get_close_matches(arg, self._cached_items, n=2, cutoff=0.4)
                    if matches:
                        print(f"  {c.YELLOW}Did you mean: pull {matches[0]}?{c.RESET}")

    def cmd_info(self):
        c = Colors
        token_str = f"&token={self.client.token}" if self.client.token else ""
        base = self.client.base_url
        print(f"\n  {c.BOLD}Connection Information:{c.RESET}")
        print(f"  Sender Host:      {self.info.get('sender_hostname', 'N/A')}")
        print(f"  Sender Platform:  {self.info.get('sender_platform', 'N/A')}")
        print(f"  Shared Root:      {self.info.get('shared_name', 'N/A')}")
        print(f"  Public Base URL:  {base}")
        print(f"\n  {c.BOLD}Terminal Suggestions for Ubuntu / Linux bash:{c.RESET}")
        print(f"  {c.YELLOW}# Stream and extract current folder with curl & tar:{c.RESET}")
        print(f"  curl -sL \"{base}/api/tar?path={self.current_path}{token_str}\" | tar -xz")
        print(f"  {c.YELLOW}# Download as tar.gz with wget:{c.RESET}")
        print(f"  wget -O folder.tar.gz \"{base}/api/tar?path={self.current_path}{token_str}\" && tar -xzf folder.tar.gz\n")

    def cmd_help(self):
        c = Colors
        print(f"\n  {c.BOLD}Available Commands & Suggestions:{c.RESET}")
        print(f"  {c.GREEN}ls [path]{c.RESET}          - List files & directories in remote path (alias: dir)")
        print(f"  {c.GREEN}cd <folder>{c.RESET}        - Navigate into folder (.. for up, / for root)")
        print(f"  {c.GREEN}pwd{c.RESET}                - Print current remote directory path")
        print(f"  {c.GREEN}pull <path>{c.RESET}        - Download remote file or folder into local directory")
        print(f"  {c.GREEN}pull .{c.RESET}             - Download entire current remote folder and extract it")
        print(f"  {c.GREEN}cat <file>{c.RESET}         - Preview remote text / code file (aliases: view, preview)")
        print(f"  {c.GREEN}info{c.RESET}               - Show sender metadata and bash curl/wget one-liners")
        print(f"  {c.GREEN}clear / cls{c.RESET}        - Clear terminal screen")
        print(f"  {c.GREEN}help / ?{c.RESET}           - Show this help message")
        print(f"  {c.GREEN}exit / quit / q{c.RESET}    - Exit remote explorer\n")

    def suggest_typo(self, unknown_cmd: str):
        c = Colors
        matches = difflib.get_close_matches(unknown_cmd, self.AVAILABLE_COMMANDS, n=1, cutoff=0.5)
        if matches:
            print(f"  {c.YELLOW}Command '{unknown_cmd}' not recognized. Did you mean '{matches[0]}'?{c.RESET}")
        else:
            print(f"  {c.RED}Unknown command '{unknown_cmd}'. Type 'help' for suggestions.{c.RESET}")

    def run_loop(self):
        """Starts the interactive REPL loop."""
        self.print_banner()

        while True:
            c = Colors
            path_display = f"/{self.current_path}" if self.current_path else "/"
            prompt = f"{c.CYAN}airbridge:{path_display}${c.RESET} "

            try:
                line = input(prompt).strip()
            except (KeyboardInterrupt, EOFError):
                print(f"\n{c.YELLOW}[*] Exiting AirBridge Explorer.{c.RESET}")
                break

            if not line:
                continue

            parts = line.split(maxsplit=1)
            raw_cmd = parts[0]
            cmd = raw_cmd.lower()
            arg = parts[1].strip() if len(parts) > 1 else ""

            # Check aliases
            if cmd in ["exit", "quit", "q", "bye"]:
                print(f"{c.YELLOW}[*] Goodbye!{c.RESET}")
                break
            elif cmd in ["ls", "dir", "list"]:
                self.cmd_ls(arg)
            elif cmd == "cd":
                self.cmd_cd(arg)
            elif cmd == "pwd":
                self.cmd_pwd()
            elif cmd in ["cat", "preview", "view", "type"]:
                self.cmd_cat(arg)
            elif cmd in ["pull", "get", "download"]:
                self.cmd_pull(arg)
            elif cmd == "info":
                self.cmd_info()
            elif cmd in ["clear", "cls"]:
                os.system("cls" if platform.system() == "Windows" else "clear")
            elif cmd in ["help", "?", "man"]:
                self.cmd_help()
            else:
                self.suggest_typo(raw_cmd)
