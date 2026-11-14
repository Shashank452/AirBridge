import os

# Root directory on Windows PC to expose to the relay server
# Defaults to current directory or can be customized
SHARE_PATH = os.environ.get("SHARE_PATH", r"c:\Users\aaa\OneDrive\Desktop\Trading")

# Oracle Cloud VM Relay address
RELAY_HOST = os.environ.get("RELAY_HOST", "YOUR_VM_PUBLIC_IP")
RELAY_PORT = int(os.environ.get("RELAY_PORT", 8001))

# Shared secret token for authentication
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "CHANGE_THIS_SECRET")

# Reconnect delay in seconds if connection drops
RECONNECT_INTERVAL = 3

# Chunk size for streaming file transfers (64 KB)
CHUNK_SIZE = 64 * 1024
