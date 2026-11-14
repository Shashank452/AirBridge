"""
ShareIt Sender — High-performance HTTP File Server with Dynamic Streaming.
Serves shared files and folders to both Web Browsers and Terminal Receivers.
Supports dynamic tar.gz and zip streaming on the fly without RAM/disk overhead.
"""

import asyncio
import datetime
import mimetypes
import os
import platform
import socket
import sys
import tarfile
import threading
import zipfile
from typing import Optional

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

# Application state
SHARE_PATH = os.path.realpath(os.path.abspath(os.environ.get("SHARE_PATH", ".")))
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(BASE_DIR, "templates")

app = FastAPI(title="ShareIt Sender Server")
templates = Jinja2Templates(directory=TEMPLATES_DIR)


def configure_sender(share_path: str, auth_token: str):
    """Configures the sender's shared folder and authentication token."""
    global SHARE_PATH, AUTH_TOKEN
    SHARE_PATH = os.path.realpath(os.path.abspath(share_path))
    AUTH_TOKEN = auth_token


def get_local_ip() -> str:
    """Finds the local LAN IP address."""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.settimeout(0.5)
        # Doesn't need to be reachable, just triggers local interface selection
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


def resolve_safe_path(rel_path: str) -> str:
    """
    Safely resolves a relative path within SHARE_PATH.
    Prevents directory traversal attacks (../, absolute paths, drive jumping).
    """
    clean_rel = rel_path.strip().replace("\\", "/").lstrip("/")
    target = os.path.realpath(os.path.join(SHARE_PATH, clean_rel))

    if not (target == SHARE_PATH or target.startswith(SHARE_PATH + os.sep)):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Access denied: path is outside the shared folder"
        )
    return target


def format_size(bytes_val: int) -> str:
    """Formats bytes into human readable string (KB, MB, GB)."""
    if bytes_val <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    d = float(bytes_val)
    while d >= 1024.0 and i < len(units) - 1:
        d /= 1024.0
        i += 1
    return f"{d:.1f} {units[i]}" if i > 0 else f"{int(d)} B"


def format_mtime(timestamp: int) -> str:
    """Formats unix timestamp into readable date string."""
    if not timestamp:
        return ""
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%d %H:%M")


def is_authenticated(request: Request) -> bool:
    """Validates authorization token from query param, header, or cookie."""
    if not AUTH_TOKEN:
        return True

    # 1. Query param
    q_token = request.query_params.get("token")
    if q_token and q_token == AUTH_TOKEN:
        return True

    # 2. Authorization header (Bearer or Raw)
    auth_header = request.headers.get("Authorization")
    if auth_header:
        if auth_header.startswith("Bearer "):
            if auth_header[7:].strip() == AUTH_TOKEN:
                return True
        elif auth_header.strip() == AUTH_TOKEN:
            return True

    # 3. Custom header
    x_token = request.headers.get("X-Auth-Token")
    if x_token and x_token.strip() == AUTH_TOKEN:
        return True

    # 4. Cookie
    c_token = request.cookies.get("auth_token")
    if c_token and c_token == AUTH_TOKEN:
        return True

    return False


def get_dir_listing(abs_path: str, rel_path: str = "") -> list[dict]:
    """Returns sorted entries inside abs_path with size and timestamps."""
    items = []
    with os.scandir(abs_path) as entries:
        for entry in entries:
            try:
                stat = entry.stat()
                is_dir = entry.is_dir()
                size = stat.st_size if not is_dir else 0
                mtime = int(stat.st_mtime)
                items.append({
                    "name": entry.name,
                    "is_dir": is_dir,
                    "size": size,
                    "size_str": format_size(size) if not is_dir else "-",
                    "mtime": mtime,
                    "mtime_str": format_mtime(mtime),
                    "path": f"{rel_path}/{entry.name}".lstrip("/") if rel_path else entry.name
                })
            except (PermissionError, FileNotFoundError):
                continue

    # Folders first, then alphabetical case-insensitive
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return items


# ==============================================================================
# REST API ENDPOINTS (For Ubuntu Terminal & Programmatic Pull)
# ==============================================================================

@app.get("/api/ping")
async def api_ping(request: Request):
    """Health check & token verification endpoint."""
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
    return {"status": "ok", "message": "Authenticated"}


@app.get("/api/info")
async def api_info(request: Request):
    """Returns sender system information and shared folder details."""
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    folder_name = os.path.basename(SHARE_PATH) or SHARE_PATH
    return {
        "status": "ok",
        "sender_platform": platform.system(),
        "sender_hostname": platform.node(),
        "shared_name": folder_name,
        "shared_path": SHARE_PATH,
        "is_windows": platform.system() == "Windows",
        "is_linux": platform.system() == "Linux",
    }


@app.get("/api/browse")
async def api_browse(request: Request, path: str = ""):
    """Returns directory contents as JSON for terminal navigation and web UI."""
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    abs_p = resolve_safe_path(path)
    if not os.path.exists(abs_p):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Path not found")
    if not os.path.isdir(abs_p):
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Path is not a directory")

    clean_path = path.strip("/").replace("\\", "/")
    parent = "/".join(clean_path.split("/")[:-1]) if "/" in clean_path else ("" if clean_path else None)

    items = await asyncio.to_thread(get_dir_listing, abs_p, clean_path)
    return {
        "status": "ok",
        "current_path": clean_path,
        "parent_path": parent,
        "items": items
    }


@app.get("/api/file")
@app.get("/files/{file_path:path}")
async def api_file(request: Request, file_path: str = "", path: str = ""):
    """Streams a single file in chunks."""
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    target_rel = file_path or path
    abs_p = resolve_safe_path(target_rel)

    if not os.path.exists(abs_p) or not os.path.isfile(abs_p):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    filename = os.path.basename(abs_p)
    file_size = os.path.getsize(abs_p)
    mime_type, _ = mimetypes.guess_type(abs_p)
    mime_type = mime_type or "application/octet-stream"

    def iter_file():
        with open(abs_p, "rb") as f:
            while chunk := f.read(64 * 1024):
                yield chunk

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "Content-Length": str(file_size),
        "Accept-Ranges": "bytes",
    }
    return StreamingResponse(iter_file(), media_type=mime_type, headers=headers)


@app.get("/api/preview")
async def api_preview(request: Request, path: str = ""):
    """Returns text preview for source code and text files (up to 64KB)."""
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    abs_p = resolve_safe_path(path)
    if not os.path.exists(abs_p) or not os.path.isfile(abs_p):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="File not found")

    def read_text():
        with open(abs_p, "rb") as f:
            raw = f.read(64 * 1024)
        return raw.decode("utf-8", errors="replace")

    content = await asyncio.to_thread(read_text)
    return {
        "status": "ok",
        "filename": os.path.basename(abs_p),
        "content": content,
        "size": os.path.getsize(abs_p)
    }


@app.get("/api/tar")
@app.get("/tar/{folder_path:path}")
async def api_tar(request: Request, folder_path: str = "", path: str = ""):
    """
    Streams the requested directory dynamically as a compressed .tar.gz archive.
    Uses non-blocking streaming via os.pipe() — highly efficient for Ubuntu/Linux terminals.
    """
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    target_rel = folder_path or path
    abs_p = resolve_safe_path(target_rel)

    if not os.path.exists(abs_p):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")

    folder_name = os.path.basename(abs_p) or "shared_folder"

    read_fd, write_fd = os.pipe()

    def tar_worker():
        try:
            with os.fdopen(write_fd, "wb") as f_out:
                with tarfile.open(mode="w:gz", fileobj=f_out) as tar:
                    tar.add(abs_p, arcname=folder_name)
        except Exception:
            pass

    t = threading.Thread(target=tar_worker, daemon=True)
    t.start()

    def iter_tar():
        try:
            with os.fdopen(read_fd, "rb") as f_in:
                while chunk := f_in.read(64 * 1024):
                    yield chunk
        finally:
            t.join(timeout=1.0)

    headers = {
        "Content-Disposition": f'attachment; filename="{folder_name}.tar.gz"'
    }
    return StreamingResponse(iter_tar(), media_type="application/gzip", headers=headers)


@app.get("/api/zip")
@app.get("/zip/{folder_path:path}")
async def api_zip(request: Request, folder_path: str = "", path: str = ""):
    """
    Streams the requested directory dynamically as a ZIP archive.
    Ideal for Windows web browsers and standard unzip utilities.
    """
    if not is_authenticated(request):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    target_rel = folder_path or path
    abs_p = resolve_safe_path(target_rel)

    if not os.path.exists(abs_p):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Folder not found")

    folder_name = os.path.basename(abs_p) or "shared_folder"
    read_fd, write_fd = os.pipe()

    def zip_worker():
        try:
            with os.fdopen(write_fd, "wb") as f_out:
                with zipfile.ZipFile(f_out, mode="w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as zf:
                    for root, _, files in os.walk(abs_p):
                        for file in files:
                            full_file = os.path.join(root, file)
                            rel_file = os.path.relpath(full_file, os.path.dirname(abs_p))
                            try:
                                zf.write(full_file, arcname=rel_file)
                            except (PermissionError, FileNotFoundError):
                                continue
        except Exception:
            pass

    t = threading.Thread(target=zip_worker, daemon=True)
    t.start()

    def iter_zip():
        try:
            with os.fdopen(read_fd, "rb") as f_in:
                while chunk := f_in.read(64 * 1024):
                    yield chunk
        finally:
            t.join(timeout=1.0)

    headers = {
        "Content-Disposition": f'attachment; filename="{folder_name}.zip"'
    }
    return StreamingResponse(iter_zip(), media_type="application/zip", headers=headers)


# ==============================================================================
# WEB EXPLORER UI (For Windows / Browser Receivers)
# ==============================================================================

@app.get("/", response_class=HTMLResponse)
@app.get("/browse/{folder_path:path}", response_class=HTMLResponse)
async def web_ui(request: Request, folder_path: str = ""):
    """Renders the interactive file explorer web interface."""
    q_token = request.query_params.get("token")
    if q_token and q_token == AUTH_TOKEN:
        # User supplied token in URL: set persistent session cookie and render
        clean_path = folder_path.strip("/").replace("\\", "/")
        target_abs = resolve_safe_path(clean_path)
        items = await asyncio.to_thread(get_dir_listing, target_abs, clean_path)

        crumbs = []
        if clean_path:
            parts = clean_path.split("/")
            accum = []
            for p in parts:
                accum.append(p)
                crumbs.append({"name": p, "path": "/".join(accum)})

        response = templates.TemplateResponse(
            "index.html",
            {
                "request": request,
                "current_path": clean_path,
                "breadcrumbs": crumbs,
                "items": items,
                "token": AUTH_TOKEN,
                "shared_name": os.path.basename(SHARE_PATH) or SHARE_PATH,
                "server_os": platform.system(),
            }
        )
        response.set_cookie(key="auth_token", value=AUTH_TOKEN, httponly=True, max_age=86400)
        return response

    if not is_authenticated(request):
        # Render login or password prompt
        return templates.TemplateResponse(
            "login.html",
            {"request": request, "error": "Authentication token required"}
        )

    clean_path = folder_path.strip("/").replace("\\", "/")
    target_abs = resolve_safe_path(clean_path)
    items = await asyncio.to_thread(get_dir_listing, target_abs, clean_path)

    crumbs = []
    if clean_path:
        parts = clean_path.split("/")
        accum = []
        for p in parts:
            accum.append(p)
            crumbs.append({"name": p, "path": "/".join(accum)})

    return templates.TemplateResponse(
        "index.html",
        {
            "request": request,
            "current_path": clean_path,
            "breadcrumbs": crumbs,
            "items": items,
            "token": AUTH_TOKEN,
            "shared_name": os.path.basename(SHARE_PATH) or SHARE_PATH,
            "server_os": platform.system(),
        }
    )
