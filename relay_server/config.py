import os

# Server binding host and port
HOST = os.environ.get("RELAY_HOST", "0.0.0.0")
PORT = int(os.environ.get("RELAY_PORT", 8001))

# Secret authentication token
# Matches the token in pc_agent/config.py
AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "CHANGE_THIS_SECRET")

# Timeout in seconds for agent responses
REQUEST_TIMEOUT = 30
