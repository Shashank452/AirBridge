import asyncio
import json
import os
import sys
import tempfile
import zipfile
import websockets
from pathlib import Path

# Add current folder to sys.path so config can be imported easily
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config


def resolve_safe_path(rel_path: str) -> str:
    """
    Safely resolves a relative path within the configured SHARE_PATH.
    Prevents directory traversal attacks (../, absolute paths, drive switching).
    """
    base = os.path.realpath(config.SHARE_PATH)
    clean_rel = rel_path.strip().replace("\\", "/").lstrip("/")
    target = os.path.realpath(os.path.join(base, clean_rel))
    
    if not (target == base or target.startswith(base + os.sep)):
        raise PermissionError("Access denied: requested path is outside the shared folder")
    return target


def get_dir_listing(abs_path: str) -> list:
    """Returns a list of items inside abs_path with metadata."""
    items = []
    with os.scandir(abs_path) as entries:
        for entry in entries:
            try:
                stat = entry.stat()
                items.append({
                    "name": entry.name,
                    "is_dir": entry.is_dir(),
                    "size": stat.st_size if not entry.is_dir() else 0,
                    "mtime": int(stat.st_mtime),
                })
            except (PermissionError, FileNotFoundError):
                continue
    # Sort: folders first, then alphabetically
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))
    return items


async def handle_list(ws, req_id: str, rel_path: str):
    try:
        abs_p = resolve_safe_path(rel_path)
        if not os.path.exists(abs_p):
            await ws.send(json.dumps({"req_id": req_id, "type": "LIST_RESP", "status": "error", "error": "Path not found"}))
            return
        if not os.path.isdir(abs_p):
            await ws.send(json.dumps({"req_id": req_id, "type": "LIST_RESP", "status": "error", "error": "Not a directory"}))
            return

        items = await asyncio.to_thread(get_dir_listing, abs_p)
        await ws.send(json.dumps({
            "req_id": req_id,
            "type": "LIST_RESP",
            "status": "ok",
            "data": items,
            "current_path": rel_path.strip("/").replace("\\", "/")
        }))
    except Exception as e:
        await ws.send(json.dumps({"req_id": req_id, "type": "LIST_RESP", "status": "error", "error": str(e)}))


async def handle_stat(ws, req_id: str, rel_path: str):
    try:
        abs_p = resolve_safe_path(rel_path)
        if not os.path.exists(abs_p):
            await ws.send(json.dumps({"req_id": req_id, "type": "STAT_RESP", "status": "error", "error": "Not found"}))
            return

        stat = os.stat(abs_p)
        await ws.send(json.dumps({
            "req_id": req_id,
            "type": "STAT_RESP",
            "status": "ok",
            "data": {
                "name": os.path.basename(abs_p),
                "is_dir": os.path.isdir(abs_p),
                "size": stat.st_size if not os.path.isdir(abs_p) else 0,
                "mtime": int(stat.st_mtime),
            }
        }))
    except Exception as e:
        await ws.send(json.dumps({"req_id": req_id, "type": "STAT_RESP", "status": "error", "error": str(e)}))


async def handle_preview(ws, req_id: str, rel_path: str):
    try:
        abs_p = resolve_safe_path(rel_path)
        if not os.path.isfile(abs_p):
            await ws.send(json.dumps({"req_id": req_id, "type": "PREVIEW_RESP", "status": "error", "error": "Not a regular file"}))
            return

        # Read up to 64KB for text preview
        def read_preview():
            with open(abs_p, "rb") as f:
                raw = f.read(64 * 1024)
            # Try utf-8 decode with replace
            return raw.decode("utf-8", errors="replace")

        content = await asyncio.to_thread(read_preview)
        await ws.send(json.dumps({
            "req_id": req_id,
            "type": "PREVIEW_RESP",
            "status": "ok",
            "content": content,
            "filename": os.path.basename(abs_p)
        }))
    except Exception as e:
        await ws.send(json.dumps({"req_id": req_id, "type": "PREVIEW_RESP", "status": "error", "error": str(e)}))


async def handle_read(ws, req_id: str, rel_path: str):
    """Streams a file in binary chunks over WebSocket."""
    req_header = req_id.encode("utf-8")
    try:
        abs_p = resolve_safe_path(rel_path)
        if not os.path.isfile(abs_p):
            err_msg = b"File not found or not a regular file"
            await ws.send(req_header + b"X" + err_msg)
            return

        total_size = os.path.getsize(abs_p)
        # Notify initial metadata
        await ws.send(json.dumps({
            "req_id": req_id,
            "type": "STREAM_START",
            "filename": os.path.basename(abs_p),
            "size": total_size
        }))

        # Stream file in chunks
        def stream_chunks():
            with open(abs_p, "rb") as f:
                while True:
                    chunk = f.read(config.CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk

        for chunk in await asyncio.to_thread(lambda: list(stream_chunks())):
            # Frame: [36 bytes UUID][1 byte Flag 'C'][Chunk data]
            await ws.send(req_header + b"C" + chunk)
            await asyncio.sleep(0)  # Yield to event loop

        # Send End of Stream frame
        await ws.send(req_header + b"E")

    except Exception as e:
        err_bytes = str(e).encode("utf-8")
        await ws.send(req_header + b"X" + err_bytes)


async def handle_zip(ws, req_id: str, rel_path: str):
    """Zips the requested directory into a temp file and streams it."""
    req_header = req_id.encode("utf-8")
    tmp_path = None
    try:
        abs_p = resolve_safe_path(rel_path)
        if not os.path.isdir(abs_p):
            await ws.send(req_header + b"X" + b"Not a directory")
            return

        base_name = os.path.basename(abs_p) or "shared"
        
        def build_zip():
            with tempfile.NamedTemporaryFile(delete=False, suffix=".zip") as tmp:
                tpath = tmp.name
            with zipfile.ZipFile(tpath, "w", zipfile.ZIP_DEFLATED) as zf:
                for root, _, files in os.walk(abs_p):
                    for file in files:
                        full_f = os.path.join(root, file)
                        rel_f = os.path.relpath(full_f, abs_p)
                        zf.write(full_f, rel_f)
            return tpath

        tmp_path = await asyncio.to_thread(build_zip)
        total_size = os.path.getsize(tmp_path)

        # Notify start of zip stream
        await ws.send(json.dumps({
            "req_id": req_id,
            "type": "STREAM_START",
            "filename": f"{base_name}.zip",
            "size": total_size
        }))

        def stream_zip():
            with open(tmp_path, "rb") as f:
                while True:
                    chunk = f.read(config.CHUNK_SIZE)
                    if not chunk:
                        break
                    yield chunk

        for chunk in await asyncio.to_thread(lambda: list(stream_zip())):
            await ws.send(req_header + b"C" + chunk)
            await asyncio.sleep(0)

        # Send End of Stream frame
        await ws.send(req_header + b"E")

    except Exception as e:
        err_bytes = str(e).encode("utf-8")
        await ws.send(req_header + b"X" + err_bytes)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception:
                pass


async def agent_loop():
    share_dir = os.path.realpath(config.SHARE_PATH)
    if not os.path.exists(share_dir):
        print(f"[!] Warning: Configured SHARE_PATH '{share_dir}' does not exist.")
        print("[!] Creating directory or please check config.py...")
        os.makedirs(share_dir, exist_ok=True)

    print("=" * 60)
    print("  Windows PC Agent — Remote File Transfer")
    print("=" * 60)
    print(f"[*] Sharing Directory:  {share_dir}")
    print(f"[*] Target Relay:       {config.RELAY_HOST}:{config.RELAY_PORT}")
    print(f"[*] Auth Token:         {'*' * len(config.AUTH_TOKEN)}")
    print("=" * 60)

    url = f"ws://{config.RELAY_HOST}:{config.RELAY_PORT}/ws?token={config.AUTH_TOKEN}"

    while True:
        try:
            print(f"[*] Connecting to Relay at {config.RELAY_HOST}:{config.RELAY_PORT}...")
            async with websockets.connect(url, max_size=100 * 1024 * 1024) as ws:
                print(f"[+] Connected to Relay successfully! Waiting for requests...")
                
                # Send initial handshake/greeting
                await ws.send(json.dumps({
                    "type": "HANDSHAKE",
                    "share_name": os.path.basename(share_dir) or "root",
                    "share_path": share_dir
                }))

                async for message in ws:
                    if isinstance(message, str):
                        try:
                            msg = json.loads(message)
                        except json.JSONDecodeError:
                            continue

                        req_id = msg.get("req_id", "")
                        req_type = msg.get("type", "")
                        path = msg.get("path", "")

                        if req_type == "PING":
                            await ws.send(json.dumps({"type": "PONG", "req_id": req_id}))
                        elif req_type == "LIST":
                            asyncio.create_task(handle_list(ws, req_id, path))
                        elif req_type == "STAT":
                            asyncio.create_task(handle_stat(ws, req_id, path))
                        elif req_type == "PREVIEW":
                            asyncio.create_task(handle_preview(ws, req_id, path))
                        elif req_type == "READ":
                            asyncio.create_task(handle_read(ws, req_id, path))
                        elif req_type == "ZIP":
                            asyncio.create_task(handle_zip(ws, req_id, path))

        except (websockets.exceptions.ConnectionClosed, ConnectionRefusedError, OSError) as e:
            print(f"[-] Connection failed ({e}). Reconnecting in {config.RECONNECT_INTERVAL} seconds...")
            await asyncio.sleep(config.RECONNECT_INTERVAL)
        except Exception as e:
            print(f"[!] Unexpected error: {e}. Retrying in {config.RECONNECT_INTERVAL} seconds...")
            await asyncio.sleep(config.RECONNECT_INTERVAL)


if __name__ == "__main__":
    try:
        asyncio.run(agent_loop())
    except KeyboardInterrupt:
        print("\n[*] Agent stopped by user.")
