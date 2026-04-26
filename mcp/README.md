# MCP Codebase Explorer

A minimal Model Context Protocol (MCP) server that provides a single tool (`list_codebase_files`) to scan your local codebase and automatically generate a sleek, static HTML file explorer for easy navigation.

## 🛠️ Setup

To get this MCP server running locally, you need to set up a Python virtual environment and install the required dependencies.

1. Open your terminal and navigate to this `mcp` folder.
2. Create and activate a Python virtual environment:
   ```powershell
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   ```
3. Install the dependencies:
   ```powershell
   pip install -r requirements.txt
   ```

## 🔌 Linking to Claude Desktop

To allow Claude to use this server, you must add it to your Claude Desktop configuration file.

1. Open the Claude Desktop config file. You can find it at:
   `%APPDATA%\Claude\claude_desktop_config.json`
   *(Press Windows Key + R, paste the path above, and hit Enter if you can't find it).*

2. Add this server to your `mcpServers` list. **Important:** You must use the absolute paths on your system pointing directly to the `python.exe` inside your newly created `venv` folder, and the absolute path to `mcp_server.py`. 

Here is an example configuration based on the default cloning path:

```json
{
  "mcpServers": {
    "CodebaseExplorer": {
      "command": "c:/Users/maxbr/Programming/LAHacks2026/KevinKowalski/mcp/venv/Scripts/python.exe",
      "args": [
        "c:/Users/maxbr/Programming/LAHacks2026/KevinKowalski/mcp/mcp_server.py"
      ]
    }
  }
}
```

3. **Restart Claude Desktop** (completely quit it from your Windows system tray and reopen it). You should see a small plug icon in your chat input area indicating the server is connected.

## 🚀 Usage

Simply ask Claude to:
> *"List the files in my codebase using the codebase explorer."*

Claude will run the tool, output the raw files for context, and provide a `file:///.../explorer.html` link. Click that link to open the beautifully styled file tree in your web browser!
