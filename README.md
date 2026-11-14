# ShareIt — Remote File Explorer & Transfer System

A **ShareIt-style**, zero-configuration file sharing and exploration system between **Windows** and **Ubuntu (Linux)**.

---

## Why this design? (Problem Solved)

Previously, connecting directly to a cloud instance (e.g., Oracle Cloud VM) failed because cloud providers and routers block inbound ports by default (VCN Security Lists, NAT, iptables/firewall).

With this **ShareIt-style architecture**:
- **No one connects directly to an IP or open port.**
- Whichever machine is the **Sender** automatically creates an instant, secure **Public HTTPS URL** via a Cloudflare Quick Tunnel (`trycloudflare.com`). No account, domain, or port forwarding required.
- The **Receiver** connects to this public URL.
- The application **auto-detects the platform (Windows vs Ubuntu/Unix)**:
  - **Windows as Receiver:** Automatically opens your default **Web Browser** to a sleek, dark-themed File Explorer UI.
  - **Ubuntu as Receiver:** Launches an **Interactive Remote Terminal Explorer** where you can navigate (`ls`, `cd`, `pwd`, `cat`) and pull files/folders (`pull <folder>`) right from your SSH terminal!

---

## Quick Start

The exact same script `run.py` is used on both platforms. Use the `-u` option to specify whether you want to **send** or **receive**.

```text
python run.py -u {send,recv} [target]
```

---

### Scenario A: Sending from Windows Laptop -> Receiving on Ubuntu (Oracle VM)

#### 1. On your Windows Laptop (Sender):
Run `run.py` with `-u send` and optionally specify the folder:

```powershell
# Share current directory:
python run.py -u send

# Or share a specific project folder:
python run.py -u send "C:\Users\aaa\OneDrive\Desktop\Trading"
```

The sender outputs a secure public URL:
```text
======================================================================
  SENDER IS READY AND SHARING FOLDER
======================================================================
  Platform:        Windows
  Shared Folder:   C:\Users\aaa\OneDrive\Desktop\Trading
  Security Token:  a4e1b8c2d9f0
----------------------------------------------------------------------
  PUBLIC URL (Internet-wide):
  https://funny-cat-1234.trycloudflare.com/?token=a4e1b8c2d9f0
======================================================================
```

#### 2. On your Ubuntu Terminal (Receiver):
Run `run.py` with `-u recv` and the public URL:

```bash
python3 run.py -u recv "https://funny-cat-1234.trycloudflare.com/?token=a4e1b8c2d9f0"
```

The application detects **Ubuntu / Linux** and automatically launches the **Interactive Remote Terminal Navigator**:

```text
======================================================================
  SHAREIT REMOTE TERMINAL EXPLORER
  Connected to Sender: Windows | Shared Root: Trading
======================================================================
remote:/$ ls
  [DIR]   src/
  [DIR]   data/
  [FILE]  run.py (5.8 KB)
  [FILE]  README.md (7.1 KB)

remote:/$ cd src
remote:/src$ ls
  [FILE]  model.pt (45.2 MB)
  [FILE]  train.py (3.2 KB)

# Pull a single file:
remote:/src$ pull model.pt
  [*] Pulling file 'model.pt'...
  [=========================] 100% (45.2/45.2 MB, 14.5 MB/s)
  [✔] Saved to: ./model.pt

# Pull an entire folder (automatically streams .tar.gz and extracts):
remote:/src$ pull .
  [*] Pulling current directory 'src' to .
  Streaming and extracting folder...
  [✔] Successfully pulled 12 items into ./src!

remote:/src$ cat train.py
  ... (displays file preview in terminal) ...

remote:/src$ cd ..
remote:/$ pwd
  /
remote:/$ exit
```

#### 3. Or Pull Directly Without Python (Ubuntu One-Liner):
If Python is not installed on Ubuntu, you can use standard `curl` or `wget`:

```bash
# Stream and extract entire folder directly:
curl -sL "https://funny-cat-1234.trycloudflare.com/api/tar?token=a4e1b8c2d9f0" | tar -xz

# Or using wget:
wget -O files.tar.gz "https://funny-cat-1234.trycloudflare.com/api/tar?token=a4e1b8c2d9f0" && tar -xzf files.tar.gz
```

---

### Scenario B: Sending from Ubuntu (Oracle VM) -> Receiving on Windows Laptop

#### 1. On your Ubuntu Server (Sender):
```bash
python3 run.py -u send /home/ubuntu/results
```
It gives a public URL: `https://xxxx.trycloudflare.com/?token=...`

#### 2. On your Windows Laptop (Receiver):
```powershell
python run.py -u recv "https://xxxx.trycloudflare.com/?token=..."
```
The application detects **Windows** and **automatically opens your default Web Browser**:
- Browse directories with breadcrumbs.
- Search and filter files in real time.
- Click **"Download .ZIP"** or **".TAR.GZ"** to download entire folders.
- Click **"Preview"** to read source code and text files inline.
- Click **"Download"** on any file to save it.

---

## Interactive Menu (No Flags)

If you run `python run.py` without arguments, it interactively prompts you:

```text
[*] Detected Platform: Windows

Please select what you want to do:
  [1] Send folder    (Share a local folder and get a public URL)
  [2] Receive files  (Connect to a public URL to browse and pull)

Enter choice [1 or 2]: 
```

---

## Command Reference

```text
usage: run.py [-h] [-u {send,recv}] [-p PORT] [-t TOKEN] [--dest DEST]
              [--pull PULL_TARGET] [--pull-all] [--cli] [--browser] [--no-tunnel]
              [target]
```

| Flag | Description |
| :--- | :--- |
| `-u {send,recv}` | **Required**. `send` to share a folder, `recv` to connect and download. |
| `[target]` | In `send` mode: folder path (default: `.`). In `recv` mode: sender public URL. |
| `-p, --port` | Local port for the sender server (default: `8000` or next free port). |
| `-t, --token` | Security token. (Sender auto-generates one if omitted). |
| `--dest` | Destination directory on receiver for saved files (default: `.`). |
| `--pull <path>` | Non-interactive mode on receiver: pull remote path and exit. |
| `--pull-all` | Non-interactive mode on receiver: pull entire folder and exit. |
| `--cli, --terminal` | Force terminal explorer mode (even on Windows). |
| `--browser` | Force browser mode (even on Linux). |
| `--no-tunnel` | LAN-only mode without Cloudflare public tunnel. |

---

## Terminal Explorer Commands (Ubuntu)

Inside the interactive terminal explorer:

| Command | Action |
| :--- | :--- |
| `ls [path]` | List files and folders with size and timestamps |
| `cd <folder>` | Navigate into folder (`..` for parent, `/` for root) |
| `pwd` | Print current remote directory path |
| `pull <item>` | Download file or folder to local directory |
| `pull .` | Pull entire current remote directory and unpack |
| `cat <file>` | Preview remote text / code file in terminal |
| `info` | Show sender metadata and copy-paste bash one-liners |
| `clear` / `cls` | Clear terminal screen |
| `help` | Show command reference |
| `exit` / `quit` | Exit remote explorer |

---

## Verification & Tests

The test suite is organized inside the [tests/](file:///c:/Users/aaa/OneDrive/Desktop/Trading/remote_access/tests) directory.

To run the automated end-to-end integration and unit tests:

```bash
# Using pytest (recommended):
pytest -v tests/

# Or run the test script directly:
python tests/test_airbridge.py
```
Verifies authentication, directory traversal prevention (403 Forbidden), single file pull byte matching, dynamic `.tar.gz` and `.zip` streaming, and client pulling.

---

## Continuous Integration & Automated Merge Protection

A GitHub Actions workflow is provided at [.github/workflows/ci.yml](file:///c:/Users/aaa/OneDrive/Desktop/Trading/remote_access/.github/workflows/ci.yml).

- Runs tests automatically on every `push` and `pull_request` across both **Ubuntu** and **Windows** on Python 3.10, 3.11, and 3.12.
- To prevent broken code from being merged, enable **Branch Protection** in GitHub:
  1. Go to repository **Settings** -> **Branches**.
  2. Add branch protection rule for `main`.
  3. Check **"Require status checks to pass before merging"** and select the CI test checks.
  4. Now GitHub will reject any PR or merge if any test fails!
