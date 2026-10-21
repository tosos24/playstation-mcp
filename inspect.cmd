@echo off
rem Opens the MCP Inspector on this server, using the project venv (no uv needed).
set MCP_INSPECTOR_SECRET_STORE=memory
cd /d "%~dp0"
npx -y @modelcontextprotocol/inspector "%~dp0venv\Scripts\python.exe" "%~dp0server.py"
