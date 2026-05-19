import argparse
import json
import os
import sys
from pathlib import Path

def main():
    parser = argparse.ArgumentParser(description="Manage Docusaurus Documentation")
    parser.add_argument("--action", choices=["write", "list", "read"], required=True)
    parser.add_argument("--path", help="Relative path to file in docs/")
    parser.add_argument("--content", help="Content to write")
    
    args = parser.parse_args()
    
    # Find project root (where sos/ is)
    # This script is in sos/services/tools/docker/
    # Root is ../../../..
    script_dir = Path(__file__).parent.absolute()
    project_root = script_dir.parent.parent.parent.parent
    docs_root = project_root / "docs" / "docs"
    
    try:
        if args.action == "write":
            if not args.path:
                print(json.dumps({"error": "Path required for write"}))
                return
            
            # Content might be passed as an argument, but for large content
            # it's better to read from stdin if not provided or if it's "-"
            content = args.content
            if not content:
                # Try reading from stdin
                if not sys.stdin.isatty():
                    content = sys.stdin.read()
            
            if not content:
                 print(json.dumps({"error": "Content required for write"}))
                 return

            full_path = docs_root / args.path
            
            # Security check
            if not str(full_path.resolve()).startswith(str(docs_root.resolve())):
                 print(json.dumps({"error": "Path traversal detected"}))
                 return

            full_path.parent.mkdir(parents=True, exist_ok=True)
            
            with open(full_path, "w") as f:
                f.write(content)
                
            print(json.dumps({"status": "ok", "path": str(args.path), "size": len(content)}))
            
        elif args.action == "read":
             if not args.path:
                print(json.dumps({"error": "Path required for read"}))
                return
                
             full_path = docs_root / args.path
             if not full_path.exists():
                 print(json.dumps({"error": "File not found"}))
                 return
                 
             with open(full_path, "r") as f:
                 data = f.read()
             
             print(json.dumps({"status": "ok", "content": data}))

        elif args.action == "list":
            files = []
            for root, _, filenames in os.walk(docs_root):
                for filename in filenames:
                    if filename.endswith(".md") or filename.endswith(".mdx"):
                        rel_path = os.path.relpath(os.path.join(root, filename), docs_root)
                        files.append(rel_path)
            
            print(json.dumps({"status": "ok", "files": files}))
            
    except Exception as e:
        print(json.dumps({"error": str(e)}))

if __name__ == "__main__":
    main()
