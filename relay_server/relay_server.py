import asyncio
import datetime
import json
import os
import sys
import uuid
from typing import Optional

from urllib.parse import parse_qs
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, Response, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, RedirectResponse, JSONResponse
from fastapi.templating import Jinja2Templates
import uvicorn

# Include current directory in path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

app = FastAPI(title="Remote File Transfer Relay")

# Templates
template_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
templates = Jinja2Templates(directory=template_dir)

# --- Agent State & Concurrency ---
class AgentManager:
    def __init__(self):
        self.ws: Optional[WebSocket] = None
        self.info: dict = {}
        self.pending_futures: dict[str, asyncio.Future] = {}
        self.pending_streams: dict[str, asyncio.Queue] = {}
        self.stream_meta: dict[str, dict] = {}
        self.stream_meta_events: dict[str, asyncio.Event] = {}

    @property
    def is_connected(self) -> bool:
        return self.ws is not None

    async def register(self, ws: WebSocket, info: dict):
        self.ws = ws
        self.info = info

    async def unregister(self):
        self.ws = None
        self.info = {}
        # Cancel any pending futures
        for fid, fut in list(self.pending_futures.items()):
            if not fut.done():
                fut.set_exception(Exception("PC Agent disconnected"))
        self.pending_futures.clear()

        for qid, q in list(self.pending_streams.items()):
            await q.put(Exception("PC Agent disconnected"))
        self.pending_streams.clear()
        self.stream_meta.clear()
        self.stream_meta_events.clear()

    async def send_json_request(self, req_type: str, path: str = "", timeout: float = config.REQUEST_TIMEOUT) -> dict:
        if not self.is_connected:
            raise HTTPException(status_code=503, detail="PC Agent is not connected")

        req_id = str(uuid.uuid4())
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self.pending_futures[req_id] = future

        try:
            await self.ws.send_text(json.dumps({
                "req_id": req_id,
                "type": req_type,
                "path": path
            }))
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        finally:
            self.pending_futures.pop(req_id, None)

    async def start_stream_request(self, req_type: str, path: str = "") -> tuple[str, dict, asyncio.Queue]:
        """Initiates a stream (READ or ZIP) and waits for initial STREAM_START metadata."""
        if not self.is_connected:
            raise HTTPException(status_code=503, detail="PC Agent is not connected")

        req_id = str(uuid.uuid4())
        queue = asyncio.Queue(maxsize=20)
        event = asyncio.Event()

        self.pending_streams[req_id] = queue
        self.stream_meta_events[req_id] = event

        try:
            await self.ws.send_text(json.dumps({
                "req_id": req_id,
                "type": req_type,
                "path": path
            }))
            # Wait for STREAM_START metadata or error
            await asyncio.wait_for(event.wait(), timeout=config.REQUEST_TIMEOUT)
            meta = self.stream_meta.get(req_id, {})
            return req_id, meta, queue
        except Exception:
            self.pending_streams.pop(req_id, None)
            self.stream_meta.pop(req_id, None)
            self.stream_meta_events.pop(req_id, None)
            raise


agent = AgentManager()


# --- Formatting Helpers ---
def format_size(bytes_val: int) -> str:
    if bytes_val <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    double_val = float(bytes_val)
    while double_val >= 1024.0 and i < len(units) - 1:
        double_val /= 1024.0
        i += 1
    return f"{double_val:.1f} {units[i]}" if i > 0 else f"{int(double_val)} B"


def format_mtime(timestamp: int) -> str:
    if not timestamp:
        return ""
    dt = datetime.datetime.fromtimestamp(timestamp)
    return dt.strftime("%Y-%m-%d %H:%M")


# --- Authentication Helpers ---
def is_authenticated(request: Request) -> bool:
    # 1. Query parameter token
    q_token = request.query_params.get("token")
    if q_token and q_token == config.AUTH_TOKEN:
        return True

    # 2. Bearer token in Authorization header
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        if auth_header[7:].strip() == config.AUTH_TOKEN:
            return True

    # 3. Cookie
    c_token = request.cookies.get("auth_token")
    if c_token and c_token == config.AUTH_TOKEN:
        return True

    return False


def get_token_query_str(request: Request) -> str:
    """Returns token string to keep in query param links if used."""
    q_token = request.query_params.get("token")
    return q_token if (q_token and q_token == config.AUTH_TOKEN) else ""


# --- WebSocket Endpoint for PC Agent ---
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    token = websocket.query_params.get("token")
    if token != config.AUTH_TOKEN:
        await websocket.close(code=1008)
        return

    await websocket.accept()
    print("[+] PC Agent WebSocket connected!")

    try:
        # First message expected is HANDSHAKE
        first_msg = await websocket.receive_text()
        handshake = json.loads(first_msg)
        await agent.register(websocket, handshake)
        print(f"[+] Agent Handshake registered: {handshake.get('share_path')}")

        while True:
            message = await websocket.receive()
            if "text" in message and message["text"]:
                try:
                    data = json.loads(message["text"])
                    req_id = data.get("req_id")
                    msg_type = data.get("type")

                    if msg_type == "STREAM_START":
                        if req_id in agent.stream_meta_events:
                            agent.stream_meta[req_id] = {
                                "filename": data.get("filename", "download"),
                                "size": data.get("size", 0)
                            }
                            agent.stream_meta_events[req_id].set()

                    elif req_id and req_id in agent.pending_futures:
                        agent.pending_futures[req_id].set_result(data)

                except Exception as e:
                    print(f"[!] Error parsing text frame: {e}")

            elif "bytes" in message and message["bytes"]:
                raw = message["bytes"]
                if len(raw) >= 37:
                    req_id = raw[:36].decode("utf-8", errors="ignore")
                    flag = chr(raw[36])
                    payload = raw[37:]

                    if req_id in agent.pending_streams:
                        q = agent.pending_streams[req_id]
                        if flag == "C":
                            await q.put(payload)
                        elif flag == "E":
                            await q.put(None)  # EOF
                        elif flag == "X":
                            err_msg = payload.decode("utf-8", errors="replace")
                            await q.put(Exception(err_msg))

    except WebSocketDisconnect:
        print("[-] PC Agent disconnected.")
    except Exception as e:
        print(f"[!] WebSocket error: {e}")
    finally:
        await agent.unregister()


# --- Authentication Routes ---
@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, error: str = ""):
    return templates.TemplateResponse("login.html", {"request": request, "error": error})


@app.post("/login")
async def login_submit(request: Request):
    body = await request.body()
    parsed = parse_qs(body.decode("utf-8", errors="replace"))
    token = parsed.get("token", [""])[0].strip()

    if token == config.AUTH_TOKEN:
        response = RedirectResponse(url="/", status_code=303)
        response.set_cookie(key="auth_token", value=token, max_age=86400 * 30, httponly=True)
        return response
    return templates.TemplateResponse("login.html", {"request": request, "error": "Invalid Secret Token"}, status_code=401)


# --- Web File Explorer UI ---
@app.get("/", response_class=HTMLResponse)
@app.get("/browse/{path:path}", response_class=HTMLResponse)
async def browse(request: Request, path: str = ""):
    if not is_authenticated(request):
        return RedirectResponse(url="/login")

    token_q = get_token_query_str(request)
    current_path = path.strip("/").replace("\\", "/")

    # Breadcrumb trail
    breadcrumbs = []
    if current_path:
        parts = current_path.split("/")
        accum = ""
        for p in parts:
            accum = f"{accum}/{p}" if accum else p
            breadcrumbs.append({"name": p, "path": accum})

    parent_path = None
    if current_path:
        parent_parts = current_path.split("/")[:-1]
        parent_path = "/".join(parent_parts)

    items = []
    if agent.is_connected:
        try:
            resp = await agent.send_json_request("LIST", current_path)
            if resp.get("status") == "ok":
                for item in resp.get("data", []):
                    item["size_formatted"] = format_size(item["size"])
                    item["mtime_formatted"] = format_mtime(item["mtime"])
                    item["full_rel_path"] = f"{current_path}/{item['name']}".lstrip("/")
                    items.append(item)
        except Exception as e:
            print(f"[!] Error fetching directory: {e}")

    return templates.TemplateResponse("index.html", {
        "request": request,
        "agent_connected": agent.is_connected,
        "agent_info": agent.info,
        "current_path": current_path,
        "parent_path": parent_path,
        "breadcrumbs": breadcrumbs,
        "items": items,
        "token_query": token_q
    })


# --- API Routes ---
@app.get("/api/list")
async def api_list(request: Request, path: str = ""):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    res = await agent.send_json_request("LIST", path)
    return JSONResponse(res)


@app.get("/api/preview")
async def api_preview(request: Request, path: str = ""):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")
    res = await agent.send_json_request("PREVIEW", path)
    return JSONResponse(res)


# --- File Download & Recursive WGET Handler ---
@app.get("/files/{filepath:path}")
async def download_file(request: Request, filepath: str):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    clean_path = filepath.strip("/").replace("\\", "/")

    # Check if this is a directory (for wget recursive crawling support)
    try:
        stat_res = await agent.send_json_request("STAT", clean_path)
        if stat_res.get("status") == "ok" and stat_res.get("data", {}).get("is_dir"):
            # Render plain HTML directory index for wget crawling
            list_res = await agent.send_json_request("LIST", clean_path)
            html_lines = [f"<!DOCTYPE html><html><head><title>Index of /{clean_path}</title></head><body>"]
            html_lines.append(f"<h1>Index of /{clean_path}</h1><hr><pre>")
            html_lines.append('<a href="../">../</a>\n')
            
            token_suffix = f"?token={config.AUTH_TOKEN}" if request.query_params.get("token") else ""
            for item in list_res.get("data", []):
                name = item["name"]
                if item["is_dir"]:
                    html_lines.append(f'<a href="{name}/{token_suffix}">{name}/</a>\n')
                else:
                    html_lines.append(f'<a href="{name}{token_suffix}">{name}</a>\n')
            html_lines.append("</pre><hr></body></html>")
            return HTMLResponse("".join(html_lines))
    except Exception:
        pass

    # Stream single file
    try:
        req_id, meta, queue = await agent.start_stream_request("READ", clean_path)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"File transfer failed: {e}")

    filename = meta.get("filename", os.path.basename(clean_path))
    filesize = meta.get("size", 0)

    async def stream_generator():
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk
        finally:
            agent.pending_streams.pop(req_id, None)
            agent.stream_meta.pop(req_id, None)
            agent.stream_meta_events.pop(req_id, None)

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }
    if filesize > 0:
        headers["Content-Length"] = str(filesize)

    return StreamingResponse(
        stream_generator(),
        media_type="application/octet-stream",
        headers=headers
    )


# --- On-The-Fly ZIP Archive Download ---
@app.get("/zip")
@app.get("/zip/{folderpath:path}")
async def download_zip(request: Request, folderpath: str = ""):
    if not is_authenticated(request):
        raise HTTPException(status_code=401, detail="Unauthorized")

    clean_path = folderpath.strip("/").replace("\\", "/")

    try:
        req_id, meta, queue = await agent.start_stream_request("ZIP", clean_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ZIP packaging failed: {e}")

    filename = meta.get("filename", "archive.zip")
    filesize = meta.get("size", 0)

    async def zip_stream_generator():
        try:
            while True:
                chunk = await queue.get()
                if chunk is None:
                    break
                if isinstance(chunk, Exception):
                    raise chunk
                yield chunk
        finally:
            agent.pending_streams.pop(req_id, None)
            agent.stream_meta.pop(req_id, None)
            agent.stream_meta_events.pop(req_id, None)

    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"'
    }
    if filesize > 0:
        headers["Content-Length"] = str(filesize)

    return StreamingResponse(
        zip_stream_generator(),
        media_type="application/zip",
        headers=headers
    )


if __name__ == "__main__":
    print(f"[*] Starting Relay Server on {config.HOST}:{config.PORT}...")
    uvicorn.run(app, host=config.HOST, port=config.PORT, log_level="info")
