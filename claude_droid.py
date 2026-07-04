#!/usr/bin/env python3
"""
Claude Droid
------------
A pure-stdlib MCP server (no pip packages at all) + a free cloudflared quick
tunnel (no signup needed). Built for phones where compiling anything is
painful.

Run:
    python claude_droid.py

It starts the server, launches a Cloudflare quick tunnel, and prints the
URL to paste into Claude's "Remote MCP server URL" field (append "/mcp").

Tools exposed: run_command, read_file, write_file, append_file, list_dir,
make_dir, delete_path, move_path, file_info
"""

import json
import os
import re
import shutil
import subprocess
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("PORT", 8000))
COMMAND_TIMEOUT_SECONDS = 60
SESSION_ID = str(uuid.uuid4())


def _resolve(path: str) -> str:
    return os.path.abspath(os.path.expanduser(path))


# ---------------- Tool implementations ----------------

def tool_run_command(args):
    command = args.get("command", "")
    timeout = int(args.get("timeout_seconds", COMMAND_TIMEOUT_SECONDS))
    try:
        result = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=os.path.expanduser("~"),
        )
        output = result.stdout or ""
        if result.stderr:
            output += f"\n[stderr]\n{result.stderr}"
        output += f"\n[exit code: {result.returncode}]"
        return output.strip()
    except subprocess.TimeoutExpired:
        return f"Command timed out after {timeout}s"
    except Exception as e:
        return f"Error running command: {e}"


def tool_read_file(args):
    try:
        with open(_resolve(args["path"]), "r", errors="replace") as f:
            return f.read()
    except Exception as e:
        return f"Error reading file: {e}"


def tool_write_file(args):
    try:
        full_path = _resolve(args["path"])
        os.makedirs(os.path.dirname(full_path) or ".", exist_ok=True)
        with open(full_path, "w") as f:
            f.write(args.get("content", ""))
        return f"Wrote {len(args.get('content', ''))} characters to {full_path}"
    except Exception as e:
        return f"Error writing file: {e}"


def tool_append_file(args):
    try:
        full_path = _resolve(args["path"])
        os.makedirs(os.path.dirname(full_path) or ".", exist_ok=True)
        with open(full_path, "a") as f:
            f.write(args.get("content", ""))
        return f"Appended {len(args.get('content', ''))} characters to {full_path}"
    except Exception as e:
        return f"Error appending to file: {e}"


def tool_list_dir(args):
    try:
        full_path = _resolve(args.get("path", "~"))
        entries = sorted(os.listdir(full_path))
        if not entries:
            return "(empty directory)"
        lines = [n + ("/" if os.path.isdir(os.path.join(full_path, n)) else "") for n in entries]
        return "\n".join(lines)
    except Exception as e:
        return f"Error listing directory: {e}"


def tool_make_dir(args):
    try:
        full_path = _resolve(args["path"])
        os.makedirs(full_path, exist_ok=True)
        return f"Created directory {full_path}"
    except Exception as e:
        return f"Error creating directory: {e}"


def tool_delete_path(args):
    try:
        full_path = _resolve(args["path"])
        if os.path.isdir(full_path):
            shutil.rmtree(full_path)
            return f"Deleted directory {full_path}"
        os.remove(full_path)
        return f"Deleted file {full_path}"
    except Exception as e:
        return f"Error deleting path: {e}"


def tool_move_path(args):
    try:
        src = _resolve(args["source"])
        dst = _resolve(args["destination"])
        os.makedirs(os.path.dirname(dst) or ".", exist_ok=True)
        shutil.move(src, dst)
        return f"Moved {src} -> {dst}"
    except Exception as e:
        return f"Error moving path: {e}"


def tool_file_info(args):
    try:
        full_path = _resolve(args["path"])
        st = os.stat(full_path)
        kind = "directory" if os.path.isdir(full_path) else "file"
        modified = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(st.st_mtime))
        return f"path: {full_path}\ntype: {kind}\nsize: {st.st_size} bytes\nmodified: {modified}"
    except Exception as e:
        return f"Error getting file info: {e}"


TOOLS = {
    "run_command": {
        "description": "Run a shell command on the device and return stdout/stderr/exit code.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "The shell command to execute."},
                "timeout_seconds": {"type": "integer", "description": "Max seconds before killing the command."},
            },
            "required": ["command"],
        },
        "fn": tool_run_command,
    },
    "read_file": {
        "description": "Read and return the full text contents of a file.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Absolute or ~-relative path."}},
            "required": ["path"],
        },
        "fn": tool_read_file,
    },
    "write_file": {
        "description": "Create a file or overwrite it entirely with the given content.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or ~-relative path."},
                "content": {"type": "string", "description": "Text content to write."},
            },
            "required": ["path", "content"],
        },
        "fn": tool_write_file,
    },
    "append_file": {
        "description": "Append text to the end of an existing (or new) file.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Absolute or ~-relative path."},
                "content": {"type": "string", "description": "Text to append."},
            },
            "required": ["path", "content"],
        },
        "fn": tool_append_file,
    },
    "list_dir": {
        "description": "List the contents of a directory (dirs marked with trailing /).",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory path. Defaults to home."}},
        },
        "fn": tool_list_dir,
    },
    "make_dir": {
        "description": "Create a directory, including any missing parent directories.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Directory path to create."}},
            "required": ["path"],
        },
        "fn": tool_make_dir,
    },
    "delete_path": {
        "description": "Delete a file or directory (recursively). Cannot be undone.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to delete."}},
            "required": ["path"],
        },
        "fn": tool_delete_path,
    },
    "move_path": {
        "description": "Move or rename a file or directory.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "destination": {"type": "string"},
            },
            "required": ["source", "destination"],
        },
        "fn": tool_move_path,
    },
    "file_info": {
        "description": "Get size, type, and last-modified time for a path.",
        "inputSchema": {
            "type": "object",
            "properties": {"path": {"type": "string", "description": "Path to inspect."}},
            "required": ["path"],
        },
        "fn": tool_file_info,
    },
}


# ---------------- Minimal MCP JSON-RPC over HTTP ----------------

class MCPHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        pass  # keep Termux console quiet

    def _send_json(self, obj, status=200, extra_headers=None):
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Mcp-Session-Id", SESSION_ID)
        if extra_headers:
            for k, v in extra_headers.items():
                self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/health"):
            self._send_json({"status": "ok"})
        else:
            self.send_response(404)
            self.end_headers()

    def do_POST(self):
        if not self.path.startswith("/mcp"):
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        raw = self.rfile.read(length) if length else b"{}"
        try:
            message = json.loads(raw)
        except json.JSONDecodeError:
            self._send_json({"jsonrpc": "2.0", "id": None,
                              "error": {"code": -32700, "message": "Parse error"}}, status=400)
            return

        method = message.get("method")
        msg_id = message.get("id")

        # Notifications (no id) get no response body, just 202
        if msg_id is None and method and method.startswith("notifications/"):
            self.send_response(202)
            self.end_headers()
            return

        if method == "initialize":
            result = {
                "protocolVersion": message.get("params", {}).get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "claude-droid", "version": "1.0.0"},
            }
            self._send_json({"jsonrpc": "2.0", "id": msg_id, "result": result})

        elif method == "tools/list":
            tools = [
                {"name": name, "description": spec["description"], "inputSchema": spec["inputSchema"]}
                for name, spec in TOOLS.items()
            ]
            self._send_json({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": tools}})

        elif method == "tools/call":
            params = message.get("params", {})
            name = params.get("name")
            args = params.get("arguments", {}) or {}
            spec = TOOLS.get(name)
            if not spec:
                self._send_json({
                    "jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32602, "message": f"Unknown tool: {name}"},
                })
                return
            try:
                text_result = spec["fn"](args)
                self._send_json({
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text", "text": str(text_result)}], "isError": False},
                })
            except Exception as e:
                self._send_json({
                    "jsonrpc": "2.0", "id": msg_id,
                    "result": {"content": [{"type": "text", "text": f"Tool error: {e}"}], "isError": True},
                })

        elif method == "ping":
            self._send_json({"jsonrpc": "2.0", "id": msg_id, "result": {}})

        else:
            self._send_json({
                "jsonrpc": "2.0", "id": msg_id,
                "error": {"code": -32601, "message": f"Method not found: {method}"},
            })


def run_server():
    server = ThreadingHTTPServer(("0.0.0.0", PORT), MCPHandler)
    server.serve_forever()


def start_cloudflared_tunnel():
    proc = subprocess.Popen(
        ["cloudflared", "tunnel", "--url", f"http://localhost:{PORT}"],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
    )
    url_pattern = re.compile(r"https://[a-zA-Z0-9\-]+\.trycloudflare\.com")
    found_url = None
    for line in proc.stdout:
        match = url_pattern.search(line)
        if match:
            found_url = match.group(0)
            break
    return proc, found_url


if __name__ == "__main__":
    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()
    time.sleep(0.5)

    print("Starting Cloudflare quick tunnel (no signup needed)...")
    proc, public_url = start_cloudflared_tunnel()

    if public_url:
        print("\n" + "=" * 60)
        print("Claude Droid is live.")
        print("Paste this into Claude's 'Remote MCP server URL' field:\n")
        print(f"  {public_url}/mcp")
        print("=" * 60 + "\n")
        print("Keep this window open (use tmux to survive backgrounding).")
        print("Press Ctrl+C to stop.\n")
    else:
        print("Could not detect tunnel URL. Check cloudflared output above.")

    try:
        proc.wait()
    except KeyboardInterrupt:
        print("\nShutting down...")
        proc.terminate()
