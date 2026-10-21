"""The fake MCP server's answers, shared by test_mcp.py's stdio and http servers (not a test)."""
import base64, json, os

PNG = base64.b64encode(b"\x89PNG\r\n\x1a\n" + b"\0" * 32).decode()
TOOLS = [
    {"name": "echo", "description": "Say it back.", "annotations": {"readOnlyHint": True},
     "inputSchema": {"type": "object", "properties": {"text": {"type": "string"}}}},
    {"name": "env", "description": "What the server can see.", "annotations": {"readOnlyHint": True}},
    {"name": "write_note", "description": "Write a note somewhere."},
    {"name": "remove", "description": "Remove a thing.", "annotations": {"destructiveHint": True}},
    {"name": "boom", "description": "Always fails.", "annotations": {"readOnlyHint": True}},
    {"name": "picture", "description": "Sends an image.", "annotations": {"readOnlyHint": True}},
    {"name": "die", "description": "Stops the server.", "annotations": {"readOnlyHint": True}},
    {"name": "hidden", "description": "Filtered out by the owner."},
]


def answer(method, params):
    """The fake server's whole brain, shared by both transports."""
    if method == "initialize":
        return {"protocolVersion": params["protocolVersion"], "capabilities": {"tools": {}},
                "serverInfo": {"name": "fake", "version": "0"}, "instructions": "Be kind to echo."}
    if method == "tools/list":   # two pages, to prove the cursor is followed
        return {"tools": TOOLS[:4], "nextCursor": "p2"} if not params.get("cursor") else {"tools": TOOLS[4:]}
    if method == "tools/call":
        name, args = params["name"], params.get("arguments") or {}
        if name == "echo":
            return {"content": [{"type": "text", "text": f"echo: {args.get('text')}"}]}
        if name == "env":
            return {"content": [{"type": "text", "text": json.dumps(
                {"token": os.environ.get("FAKE_TOKEN"), "secret": os.environ.get("AHAB_TEST_SECRET"),
                 "cwd": os.getcwd()})}]}
        if name in ("write_note", "remove"):
            return {"content": [{"type": "text", "text": f"{name} done with {json.dumps(args)}"}]}
        if name == "boom":
            return {"content": [{"type": "text", "text": "it broke"}], "isError": True}
        if name == "picture":
            return {"content": [{"type": "image", "data": PNG, "mimeType": "image/png"}]}
    raise KeyError(method)
