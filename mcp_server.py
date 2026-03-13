#!/usr/bin/env python3
"""
conference-session-agent — mcp_server.py

MCP server for Cursor and other MCP-compatible AI agents.
Exposes two tools:
  - fetch_conference_sessions: runs fetch_sessions.py subprocess
  - get_sessions_json: reads the cached sessions JSON

Setup in Cursor (.cursor/mcp.json or settings):
  {
    "mcpServers": {
      "conference-session-agent": {
        "command": "python",
        "args": ["mcp_server.py"],
        "cwd": "/path/to/conference-session-agent"
      }
    }
  }
"""

import json
import subprocess
import sys
from pathlib import Path

# MCP server implementation using stdio transport (works with Cursor + Claude Code)
# Uses the minimal MCP protocol over stdin/stdout

def handle_request(request: dict) -> dict:
    method = request.get("method", "")
    request_id = request.get("id")

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": "conference-session-agent",
                    "version": "1.0.0"
                }
            }
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "tools": [
                    {
                        "name": "fetch_conference_sessions",
                        "description": (
                            "Fetch all sessions from a Rainfocus conference portal. "
                            "Opens a browser for login if needed, clicks 'Show more' until "
                            "all sessions are loaded, and saves structured JSON locally. "
                            "Uses cached data if available and fresh (< 24 hours)."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "conference_id": {
                                    "type": "string",
                                    "description": "Conference ID matching a file in conferences/ (e.g. 'gtc2026')"
                                },
                                "force_refresh": {
                                    "type": "boolean",
                                    "description": "If true, re-fetch even if cached data exists",
                                    "default": False
                                }
                            },
                            "required": ["conference_id"]
                        }
                    },
                    {
                        "name": "get_sessions_json",
                        "description": (
                            "Read the cached sessions JSON for a conference. "
                            "Returns the full session list as structured data. "
                            "Call fetch_conference_sessions first if no cache exists."
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "conference_id": {
                                    "type": "string",
                                    "description": "Conference ID (e.g. 'gtc2026')"
                                }
                            },
                            "required": ["conference_id"]
                        }
                    }
                ]
            }
        }

    if method == "tools/call":
        tool_name = request.get("params", {}).get("name", "")
        arguments = request.get("params", {}).get("arguments", {})

        if tool_name == "fetch_conference_sessions":
            return _fetch_sessions(request_id, arguments)
        elif tool_name == "get_sessions_json":
            return _get_sessions_json(request_id, arguments)
        else:
            return _error(request_id, -32601, f"Unknown tool: {tool_name}")

    if method == "notifications/initialized":
        return None  # No response needed

    return _error(request_id, -32601, f"Unknown method: {method}")


def _fetch_sessions(request_id, args: dict) -> dict:
    conference_id = args.get("conference_id", "")
    force_refresh = args.get("force_refresh", False)

    if not conference_id:
        return _error(request_id, -32602, "conference_id is required")

    cmd = [sys.executable, "fetch_sessions.py", "--conference", conference_id]
    if force_refresh:
        cmd.append("--no-cache")

    try:
        result = subprocess.run(
            cmd,
            cwd=Path(__file__).parent,
            capture_output=True,
            text=True,
            timeout=700
        )
        output = result.stdout + result.stderr
        success = result.returncode == 0

        # Read result count from saved JSON
        output_path = Path(__file__).parent / "sessions" / f"{conference_id}.json"
        session_count = 0
        if output_path.exists():
            try:
                with open(output_path) as f:
                    data = json.load(f)
                session_count = data["meta"]["total_sessions"]
            except Exception:
                pass

        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "status": "ok" if success else "error",
                        "session_count": session_count,
                        "output_path": str(output_path),
                        "message": output.strip()
                    }, indent=2)
                }]
            }
        }
    except subprocess.TimeoutExpired:
        return _error(request_id, -32000, "Fetch timed out after 700s")
    except Exception as e:
        return _error(request_id, -32000, str(e))


def _get_sessions_json(request_id, args: dict) -> dict:
    conference_id = args.get("conference_id", "")
    if not conference_id:
        return _error(request_id, -32602, "conference_id is required")

    output_path = Path(__file__).parent / "sessions" / f"{conference_id}.json"
    if not output_path.exists():
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps({
                        "status": "not_found",
                        "message": f"No cached sessions for '{conference_id}'. Run fetch_conference_sessions first."
                    })
                }]
            }
        }

    try:
        with open(output_path, encoding="utf-8") as f:
            data = json.load(f)
        return {
            "jsonrpc": "2.0",
            "id": request_id,
            "result": {
                "content": [{
                    "type": "text",
                    "text": json.dumps(data, ensure_ascii=False)
                }]
            }
        }
    except Exception as e:
        return _error(request_id, -32000, f"Failed to read sessions: {e}")


def _error(request_id, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message}
    }


def main():
    import io
    # Use line-buffered stdout for MCP stdio transport
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, line_buffering=True)

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
            response = handle_request(request)
            if response is not None:
                print(json.dumps(response), flush=True)
        except json.JSONDecodeError as e:
            err = _error(None, -32700, f"Parse error: {e}")
            print(json.dumps(err), flush=True)
        except Exception as e:
            err = _error(None, -32603, f"Internal error: {e}")
            print(json.dumps(err), flush=True)


if __name__ == "__main__":
    main()