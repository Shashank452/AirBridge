# Python Remote File Explorer & Transfer System

## 1. Project Overview

Build a Python-based remote file browsing and transfer system that allows a user to access files and folders stored on their Windows PC through an Oracle Cloud VM.

The Windows PC acts as the file provider, while the Oracle Cloud VM acts as the public relay server.

The main objective is to browse the PC's shared directory from a web browser and remotely download files and folders to the Oracle VM without requiring Cloudflare, ngrok, a domain, or an additional SSH session.

## 2. Main Objectives

1. Share a configurable directory from a Windows PC.
2. Browse all shared files and folders remotely.
3. Navigate through nested directories.
4. Preview supported text files.
5. Download individual files.
6. Download complete folders recursively using wget.
7. Transfer large files without loading them entirely into RAM.
8. Maintain a persistent connection between the PC and Oracle VM.
9. Protect the file-sharing service using authentication.
10. Preserve the user's existing SSH connection.

## 3. System Architecture

The project consists of two independent Python applications.

### A. Windows PC — PC Agent

File: `pc_agent.py`

Responsibilities:

- Read files from the configured Windows directory.
- Establish an outbound WebSocket connection to the Oracle VM.
- Maintain the connection persistently.
- Receive file and directory requests from the relay.
- Return directory listings and file contents.
- Stream large files in chunks.
- Handle multiple simultaneous requests.
- Reconnect automatically if the connection drops.

Configuration:

```python
SHARE_PATH = r"D:\Your\Project\Folder"

SERVER_HOST = "YOUR_VM_PUBLIC_IP"
SERVER_PORT = 8001

AUTH_TOKEN = "CHANGE_THIS_SECRET"
```

The user must be able to change the shared directory without modifying the application logic.

### B. Oracle Cloud VM — Relay Server

File: `relay_server.py`

Responsibilities:

- Run an HTTP server on port 8001.
- Accept authenticated WebSocket connections from PC agents.
- Maintain the active agent connection.
- Provide a web-based file explorer.
- Forward browser file requests to the connected PC.
- Stream file responses back to browsers and wget.
- Handle multiple simultaneous downloads.
- Display connection status.
- Return appropriate errors when the PC is disconnected.

Configuration:

```python
HOST = "0.0.0.0"
PORT = 8001

AUTH_TOKEN = "CHANGE_THIS_SECRET"
```

The relay must not access the Windows PC's files directly. All file operations must go through the authenticated PC agent.

## 4. Communication Architecture

The PC agent initiates an outbound WebSocket connection to the Oracle VM.

```text
                 WINDOWS PC
              ┌───────────────┐
              │  pc_agent.py  │
              │               │
              │ Shared Folder │
              └───────┬───────┘
                      │
                      │ Persistent outbound
                      │ WebSocket connection
                      │
                      ▼
              ┌───────────────┐
              │ ORACLE CLOUD  │
              │               │
              │ relay_server  │
              │               │
              │ HTTP Server   │
              └───────┬───────┘
                      │
             ┌────────┴────────┐
             │                 │
             ▼                 ▼
        Web Browser         wget / curl
        File Explorer       File Downloads
```

### Communication protocol

Use WebSocket for communication between the PC agent and relay.

Each request must contain:

- Unique request ID.
- Operation type.
- Relative file or folder path.
- Required metadata.

Supported operations:

| Operation | Description |
|---|---|
| LIST | Retrieve directory contents |
| STAT | Retrieve file metadata |
| READ | Stream file contents |
| PREVIEW | Read supported text files |
| PING | Check agent availability |

Use request IDs to associate responses with the correct requests.

Use JSON control messages and binary data frames for file streaming.

Large files must be transferred in chunks with appropriate backpressure and cancellation handling.

## 5. Web-Based File Explorer

The relay server must provide a browser-based interface.

Example:

```text
http://YOUR_VM_PUBLIC_IP:8001/
```

### Dashboard

Display:

- Agent connection status.
- Shared directory name.
- Current directory.
- Total number of visible files and folders.
- Available file operations.

### File Browser

Example directory structure:

```text
Shared Files/
│
├── Project_A/
│   ├── main.py
│   ├── requirements.txt
│   ├── README.md
│   │
│   ├── models/
│   │   ├── best.pt
│   │   └── model.tflite
│   │
│   └── outputs/
│       └── results.csv
│
├── Project_B/
│   ├── app.py
│   └── config.json
│
└── notes.txt
```

Features:

- Click folders to navigate.
- Navigate to parent directories.
- Display file sizes.
- Display modification timestamps.
- Sort by name, size, and modification date.
- Search filenames within the current directory.
- Download individual files.
- Preview supported text files.
- Display clear errors for unavailable files.

The browser must never expose files outside the configured shared directory.

## 6. File Download System

All downloads must be streamed from the PC through the relay.

### Individual file download

Example:

```bash
wget "http://YOUR_VM_PUBLIC_IP:8001/files/Project_A/main.py"
```

### Folder download

Directory listings must contain links to nested folders and downloadable files.

Example:

```bash
wget -r -np -nH \
"http://YOUR_VM_PUBLIC_IP:8001/files/Project_A/"
```

The relay must support:

- Recursive directory traversal by wget.
- Correct URL encoding for filenames.
- Nested directories.
- Large file downloads.
- Interrupted connection handling.
- Appropriate HTTP status codes.
- Correct Content-Length and Content-Disposition headers.

Authentication must also work with wget, preferably through an HTTP authorization header.

## 7. Security Requirements

Security is important because the Oracle VM is publicly accessible.

### Authentication

Use a shared secret between the PC agent and relay.

Requirements:

- Authenticate WebSocket connections.
- Authenticate browser sessions and downloads.
- Do not expose the agent connection endpoint publicly without authentication.
- Never display the shared secret in directory listings or logs.

### Directory Security

- Read-only access.
- No file uploads.
- No file deletion.
- No file modification.
- Prevent directory traversal attacks.
- Reject absolute paths.
- Prevent symlink access outside the configured shared directory.
- Validate every requested path on the PC agent.

### Network Security

- Use HTTPS/WSS in production.
- Do not transmit authentication secrets over unencrypted public HTTP.
- Restrict access where practical.
- Apply request size and concurrency limits.
- Do not expose arbitrary filesystem operations.

## 8. Error Handling

The system must handle:

- PC agent disconnected.
- WebSocket connection interrupted.
- File not found.
- Folder not found.
- Permission denied.
- Invalid authentication.
- Invalid paths.
- File deleted during download.
- Browser connection interrupted.
- Large file transfer failure.
- Multiple simultaneous requests.

The browser should show useful error messages rather than server tracebacks.

## 9. Project Structure

```text
remote-file-sharing/
│
├── pc_agent/
│   ├── pc_agent.py
│   ├── config.py
│   └── requirements.txt
│
├── relay_server/
│   ├── relay_server.py
│   ├── config.py
│   ├── agent_manager.py
│   ├── request_handler.py
│   ├── file_routes.py
│   ├── auth.py
│   ├── requirements.txt
│   │
│   └── templates/
│       └── index.html
│
├── README.md
└── idea.md
```

## 10. Deployment

### Oracle Cloud VM

Run the relay server:

```bash
python3 relay_server.py
```

The relay listens on:

```text
0.0.0.0:8001
```

Port 8001 is already configured in the Oracle Cloud ingress rules and firewalld.

The existing application on port 8000 and SSH on port 22 must remain untouched.

### Windows PC

Install the PC agent dependencies:

```powershell
pip install -r requirements.txt
```

Start the agent:

```powershell
python pc_agent.py
```

The PC agent establishes the outbound WebSocket connection to the Oracle VM.

No second SSH session is required.

## 11. Implementation Phases

### Phase 1 — Connectivity

- Implement the Oracle relay.
- Implement the Windows PC agent.
- Authenticate the WebSocket connection.
- Maintain the persistent connection.
- Display agent connection status.

### Phase 2 — Remote File Explorer

- Implement directory listing.
- Implement nested folder navigation.
- Display filenames, sizes, and timestamps.
- Implement text file previews.
- Build the browser interface.

### Phase 3 — File Downloads

- Implement individual file downloads.
- Implement streaming.
- Add recursive wget support.
- Support large files.
- Handle concurrent requests.

### Phase 4 — Security and Reliability

- Add browser authentication.
- Add download authentication.
- Validate all file paths.
- Implement automatic reconnection.
- Add request timeouts and cancellation.
- Add secure production transport.

### Phase 5 — Final Testing

Test the following:

- PC agent connects to the Oracle VM.
- Browser can display the shared directory.
- Nested directories can be opened.
- Correct files can be identified.
- Text previews work.
- Individual downloads work.
- Recursive wget downloads work.
- Large files transfer successfully.
- Existing SSH and port 8000 application remain operational.

## 12. Final Expected Result

The finished system should allow the user to:

1. Start the relay server on Oracle Cloud.
2. Start the PC agent on Windows.
3. Open the Oracle VM's public IP on port 8001.
4. Browse all files and folders inside the configured PC directory.
5. Preview files before downloading.
6. Identify the correct project and its contents.
7. Use wget from the Oracle VM to pull selected files or complete folders.

The system must operate without Cloudflare, ngrok, a purchased domain, router port forwarding, or an additional SSH session.

**Core design principle:** The PC initiates the connection, while the Oracle VM handles browsing and download requests through that existing persistent connection. All file access remains read-only and restricted to the configured shared directory.
