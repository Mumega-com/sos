#!/usr/bin/env python3
import os
import sys
from pathlib import Path

# Architecture Agreement Constraints
FORBIDDEN_IMPORTS = {
    "sos_cli.py": ["torch", "transformers", "chromadb", "sentence_transformers"],
    "sos/kernel": ["httpx", "requests", "openai", "google"]
}

REQUIRED_FILES = [
    "docs/ARCHITECTURE_AGREEMENT.md",
    "sos/kernel/identity.py",
    "sos/services/engine/app.py"
]

def audit():
    print(">>> [AUDIT] Starting SOS Architectural Integrity Check...")
    violations = 0

    # 1. Check for forbidden heavy imports in thin client
    cli_path = Path("sos_cli.py")
    if cli_path.exists():
        content = cli_path.read_text()
        for lib in FORBIDDEN_IMPORTS["sos_cli.py"]:
            if f"import {lib}" in content or f"from {lib}" in content:
                print(f"❌ VIOLATION: Thin Client 'sos_cli.py' contains heavy import '{lib}'")
                violations += 1

    # 2. Check for external deps in Kernel
    kernel_dir = Path("sos/kernel")
    for py_file in kernel_dir.glob("*.py"):
        content = py_file.read_text()
        for lib in FORBIDDEN_IMPORTS["sos/kernel"]:
            if f"import {lib}" in content or f"from {lib}" in content:
                print(f"❌ VIOLATION: Kernel file '{py_file.name}' depends on external lib '{lib}'")
                violations += 1

    # 3. Check for required infrastructure
    for req in REQUIRED_FILES:
        if not Path(req).exists():
            print(f"❌ VIOLATION: Required file missing: {req}")
            violations += 1

    if violations == 0:
        print("✅ AUDIT PASSED: System remains Sovereign and Modular.")
        return True
    else:
        print(f"⚠️ AUDIT FAILED: {violations} architectural violations found.")
        return False

if __name__ == "__main__":
    if not audit():
        sys.exit(1)
