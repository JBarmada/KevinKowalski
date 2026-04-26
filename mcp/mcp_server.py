import os
import json
from fastmcp import FastMCP

mcp = FastMCP("CodebaseServer")

@mcp.tool()
def list_codebase_files() -> str:
    """Lists all files in the current codebase and generates a static explorer UI page."""
    codebase_dir = os.path.dirname(os.path.abspath(__file__))
    all_files = []
    
    # Walk the directory
    for root, dirs, files in os.walk(codebase_dir):
        # Exclude hidden directories and virtual envs
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in ('venv', 'env', '__pycache__', 'node_modules')]
        for f in files:
            all_files.append(os.path.join(root, f))
            
    # Load the template and inject files
    template_path = os.path.join(codebase_dir, "index.html")
    static_html_path = os.path.join(codebase_dir, "explorer.html")
    
    try:
        with open(template_path, "r", encoding="utf-8") as f:
            html = f.read()
            
        # Replace the placeholder with the JSON array
        # We replace the backslashes so they are properly escaped in the JS string
        json_str = json.dumps(all_files).replace('\\', '\\\\')
        html = html.replace("{{FILES_JSON}}", json_str)
        
        # Write to static file
        with open(static_html_path, "w", encoding="utf-8") as f:
            f.write(html)
            
        file_uri = f"file:///{static_html_path.replace(chr(92), '/')}"
        files_text = "\n".join(all_files) if all_files else "No files found."
        return f"Successfully scanned {len(all_files)} files.\n\nI have generated a static UI page for you to explore the codebase visually. Provide this exact link to the user so they can open it in their browser: {file_uri}\n\nHere is the raw list of files for your reference:\n{files_text}"
        
    except Exception as e:
        # Fallback if something goes wrong with UI generation
        files_text = "\n".join(all_files) if all_files else "No files found."
        return f"Scanned files (UI generation failed: {e}):\n{files_text}"

if __name__ == "__main__":
    mcp.run()
