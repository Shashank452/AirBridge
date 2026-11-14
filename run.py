#!/usr/bin/env python3
"""
AirBridge — Universal Cross-Platform Remote File Bridge
Seamless zero-configuration file and folder transfers between Windows and Unix (Ubuntu / Debian / macOS).

Usage:
  # 1. As Sender (shares folder and gives public URL):
  python run.py -u send
  python run.py -u send "C:\\path\\to\\folder"
  python run.py -u send /home/ubuntu/data -p 8080

  # 2. As Receiver (connects to sender public URL):
  # On Windows: automatically opens browser file explorer
  # On Ubuntu: launches interactive terminal file navigator (ls, cd, pull)
  python run.py -u recv "https://xxxx.trycloudflare.com?token=TOKEN"

  # 3. Interactive prompt (if no arguments passed):
  python run.py
"""

import argparse
import os
import platform
import secrets
import socket
import sys
import time

# Ensure local packages can be imported
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

BANNER = r"""
======================================================================
     ___    _        ____         _       __            
    /   |  (_)____  / __ ) _____ (_)____ / /____ _ ___  
   / /| | / // __ \/ __  |/ ___// // __  // __ `// _ \ 
  / ___ |/ // /_/ / /_/ // /   / // /_/ // /_/ //  __/ 
 /_/  |_/_/ \__, /_____//_/   /_/ \__,_/ \__, / \___/  
           /____/                       /____/         
  Universal Cross-Platform File Bridge (Windows <-> Unix)
======================================================================
"""

EPILOG = """
Terminal Suggestions & Workflow Examples:
----------------------------------------------------------------------
1. Share a folder from Windows Laptop -> Pull onto Ubuntu VM:
   Windows PC (Sender):
     python run.py -u send "D:\\MyProject"
     -> Generates public URL: https://xxxx.trycloudflare.com/?token=TOKEN

   Ubuntu VM (Receiver):
     # Option A: Interactive terminal shell (navigate & pull):
     python3 run.py -u recv "https://xxxx.trycloudflare.com/?token=TOKEN"

     # Option B: One-command non-interactive pull & extract:
     python3 run.py -u recv "https://xxxx.trycloudflare.com/?token=TOKEN" --pull-all

     # Option C: Direct bash one-liner (Zero Python required on Ubuntu!):
     curl -sL "https://xxxx.trycloudflare.com/api/tar?token=TOKEN" | tar -xz

----------------------------------------------------------------------
2. Share results from Ubuntu VM -> Download onto Windows Laptop:
   Ubuntu VM (Sender):
     python3 run.py -u send /home/ubuntu/results

   Windows PC (Receiver):
     python run.py -u recv "https://xxxx.trycloudflare.com/?token=TOKEN"
     -> Automatically launches your Web Browser to browse & download!

----------------------------------------------------------------------
3. Useful Receiver Flags:
   --dest <dir>         Save downloaded files into a specific local folder
   --pull <remote_path> Pull a specific subfolder or file non-interactively
   --pull-all           Pull the entire shared folder non-interactively
   --cli, --terminal    Force terminal explorer mode (even on Windows)
   --browser            Force browser mode (even on Linux)
   --no-tunnel          Run in local LAN mode without public tunnel
======================================================================
"""


def find_free_port(start_port: int = 8000) -> int:
    """Finds an available local TCP port."""
    for port in range(start_port, start_port + 100):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            if s.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start_port


def print_suggestions_card(public_url: str, local_url: str, token: str, share_path: str):
    """Prints a clear, beautifully formatted next-step suggestions card."""
    base_api = public_url.split("?")[0].rstrip("/") if public_url else local_url.split("?")[0].rstrip("/")
    folder_name = os.path.basename(share_path) or "shared_folder"

    print("\n\033[96m\033[1m" + "=" * 70)
    print("  TERMINAL SUGGESTIONS & COPY-PASTE COMMANDS")
    print("=" * 70 + "\033[0m")

    print("\n\033[93m\033[1m[A] FOR UBUNTU / LINUX RECEIVER:\033[0m")
    print("  \033[1m1. Interactive Remote Terminal Shell (navigate & pull):\033[0m")
    print(f"     \033[92mpython3 run.py -u recv \"{public_url}\"\033[0m")

    print("\n  \033[1m2. Non-Interactive Pull (scripts & automation):\033[0m")
    print(f"     \033[92mpython3 run.py -u recv \"{public_url}\" --pull-all\033[0m")

    print("\n  \033[1m3. Direct Curl + Tar Extraction (Zero Python required on Ubuntu):\033[0m")
    print(f"     \033[92mcurl -sL \"{base_api}/api/tar?token={token}\" | tar -xz\033[0m")

    print("\n  \033[1m4. Direct Wget Download:\033[0m")
    print(f"     \033[92mwget -O {folder_name}.tar.gz \"{base_api}/api/tar?token={token}\" && tar -xzf {folder_name}.tar.gz\033[0m")

    print("\n\033[93m\033[1m[B] FOR WINDOWS RECEIVER:\033[0m")
    print("  \033[1m1. Open Web Browser Explorer (Auto):\033[0m")
    print(f"     \033[92mpython run.py -u recv \"{public_url}\"\033[0m")
    print("  \033[1m2. Direct Browser Link:\033[0m")
    print(f"     \033[94m{public_url}\033[0m")

    print("\n\033[96m" + "=" * 70 + "\033[0m")


def start_sender(
    share_path: str,
    port: int,
    token: str,
    enable_tunnel: bool = True
):
    """Starts the Sender HTTP server and Cloudflare Public Tunnel."""
    import uvicorn
    from sender.server import app, configure_sender, get_local_ip
    from tunnel import TunnelManager

    share_path = os.path.abspath(share_path)
    if not os.path.exists(share_path):
        print(f"[!] Error: Path does not exist:\n    {share_path}")
        sys.exit(1)

    system_name = platform.system()
    os.environ["SHARE_PATH"] = share_path
    os.environ["AUTH_TOKEN"] = token
    configure_sender(share_path, token)

    local_ip = get_local_ip()
    local_url = f"http://{local_ip}:{port}/?token={token}"

    tunnel_mgr = None
    public_url = None

    if enable_tunnel:
        print("[*] Establishing secure Cloudflare Public Tunnel...")
        tunnel_mgr = TunnelManager(port)
        raw_url = tunnel_mgr.start(timeout=25)
        if raw_url:
            public_url = f"{raw_url}/?token={token}"
            print("[✔] Public Tunnel active!")
        else:
            print("[!] Could not establish public tunnel. Falling back to local network URL.")

    print("\n" + "=" * 70)
    print("  AIRBRIDGE SENDER IS ACTIVE")
    print("=" * 70)
    print(f"  Platform:        {system_name}")
    print(f"  Shared Folder:   {share_path}")
    print(f"  Security Token:  {token}")
    print("-" * 70)

    if public_url:
        print(f"  \033[92m\033[1mPUBLIC ACCESS URL (Global HTTPS):\033[0m")
        print(f"  \033[96m{public_url}\033[0m")
        print("-" * 70)

    print(f"  Local LAN URL:   {local_url}")
    print("=" * 70)

    # Print terminal suggestions card
    active_url = public_url if public_url else local_url
    print_suggestions_card(active_url, local_url, token, share_path)

    print("\n[+] AirBridge server is serving files. Press Ctrl+C to stop sharing.\n")

    try:
        uvicorn.run(app, host="0.0.0.0", port=port, log_level="warning")
    except KeyboardInterrupt:
        print("\n[*] Stopping AirBridge Sender...")
    finally:
        if tunnel_mgr:
            tunnel_mgr.stop()
        print("[*] AirBridge stopped cleanly.")


def main():
    parser = argparse.ArgumentParser(
        prog="run.py",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=BANNER + "\nAirBridge — Zero-configuration cross-platform file transfer.",
        epilog=EPILOG
    )

    parser.add_argument(
        "-u", "--role", "--mode",
        dest="role",
        choices=["send", "recv", "sender", "receiver", "s", "r", "pc", "inst"],
        default=None,
        help="Operating mode: 'send' (share folder with public URL) or 'recv' (connect and download)."
    )

    parser.add_argument(
        "target",
        nargs="?",
        default=None,
        help="In 'send' mode: folder path to share (default: current dir). In 'recv' mode: sender public URL."
    )

    parser.add_argument(
        "-p", "--port",
        dest="port",
        type=int,
        default=None,
        help="Local server port for sender (default: 8000 or first free port)."
    )

    parser.add_argument(
        "-t", "--token",
        dest="token",
        default=None,
        help="Security token for authentication. If omitted, a secure token is generated automatically."
    )

    parser.add_argument(
        "--dest",
        dest="dest",
        default=".",
        help="Destination directory to save received files into (default: current directory)."
    )

    parser.add_argument(
        "--pull",
        dest="pull_target",
        default=None,
        help="Non-interactive mode for receiver: pull specified remote path and exit."
    )

    parser.add_argument(
        "--pull-all",
        dest="pull_all",
        action="store_true",
        help="Non-interactive mode for receiver: pull entire remote folder and exit."
    )

    parser.add_argument(
        "--cli", "--terminal",
        dest="force_cli",
        action="store_true",
        help="Force terminal explorer mode (even on Windows)."
    )

    parser.add_argument(
        "--browser",
        dest="force_browser",
        action="store_true",
        help="Force browser mode (even on Linux)."
    )

    parser.add_argument(
        "--no-tunnel",
        dest="no_tunnel",
        action="store_true",
        help="Run locally without Cloudflare public tunnel (LAN mode)."
    )

    args = parser.parse_args()

    print(BANNER)

    role = args.role

    # Normalization: map synonyms
    if role in ["s", "sender", "pc"]:
        role = "send"
    elif role in ["r", "receiver", "inst"]:
        role = "recv"

    # Interactive prompt if role not specified
    if not role:
        system_name = platform.system()
        print(f"[*] Detected Platform: \033[1m{system_name}\033[0m")
        print("\nPlease select what you want to do:")
        print("  [1] Send folder    (Share a local folder and get a public URL)")
        print("  [2] Receive files  (Connect to a public URL to browse and pull)")
        while True:
            try:
                choice = input("\nEnter choice [1 or 2]: ").strip()
                if choice in ["1", "send", "s"]:
                    role = "send"
                    break
                elif choice in ["2", "recv", "r", "receive"]:
                    role = "recv"
                    break
                else:
                    print("[!] Please enter '1' or '2'.")
            except (KeyboardInterrupt, EOFError):
                print("\n[*] Exiting.")
                sys.exit(0)

    # -------------------------------------------------------------
    # SENDER MODE
    # -------------------------------------------------------------
    if role == "send":
        folder_to_share = args.target if args.target else "."
        port = args.port if args.port else find_free_port(8000)
        token = args.token if args.token else secrets.token_hex(6)

        start_sender(
            share_path=folder_to_share,
            port=port,
            token=token,
            enable_tunnel=not args.no_tunnel
        )

    # -------------------------------------------------------------
    # RECEIVER MODE
    # -------------------------------------------------------------
    elif role == "recv":
        from receiver.client import run_receiver

        sender_url = args.target
        if not sender_url:
            try:
                sender_url = input("Enter Sender Public URL (e.g. https://xxx.trycloudflare.com?token=...): ").strip()
            except (KeyboardInterrupt, EOFError):
                print("\n[*] Exiting.")
                sys.exit(0)

        if not sender_url:
            print("[!] Error: Sender URL is required in receiver mode.")
            sys.exit(1)

        force_mode = None
        if args.force_cli:
            force_mode = "terminal"
        elif args.force_browser:
            force_mode = "browser"

        run_receiver(
            url=sender_url,
            token=args.token,
            force_mode=force_mode,
            pull_target=args.pull_target,
            pull_all=args.pull_all,
            dest_dir=args.dest
        )


if __name__ == "__main__":
    main()
