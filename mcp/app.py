from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastmcp import Client
import os

app = FastAPI()

@app.get("/")
async def get_frontend():
    with open("index.html", "r") as f:
        return HTMLResponse(f.read())

@app.get("/api/run")
async def run_mcp():
    try:
        # Connect to the local MCP server using STDIO
        # Note: The server is the python script 'mcp_server.py'
        async with Client("mcp_server.py") as client:
            # Call the tool exposed by the server
            result = await client.call_tool("list_codebase_files", {})
            
            # Postprocessing to show capability
            files = result.split("\n")
            postprocessed = f"🔍 MCP Tool Executed Successfully!\n"
            postprocessed += f"Found {len(files)} files in the codebase.\n"
            postprocessed += "="*60 + "\n\n"
            
            for f in files:
                filename = os.path.basename(f)
                postprocessed += f"📄 {filename} \n   └─ Path: {f}\n\n"
                
            return {"result": postprocessed}
    except Exception as e:
        return {"result": f"Error interacting with MCP Server: {str(e)}"}

if __name__ == "__main__":
    import uvicorn
    # Make sure to run this using `python app.py`
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
