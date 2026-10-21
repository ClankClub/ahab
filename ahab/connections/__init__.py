"""Outside services Ahab connects to: a file per service, its docstring saying how it gets in (n8n relay,
direct, local files). A service that gets a second way in with code of its own becomes a folder, one file per
way (notion/n8n.py, notion/direct.py), gathered by its __init__.py. An MCP server is config in home.json, not
a file, so it never counts as a second way."""
