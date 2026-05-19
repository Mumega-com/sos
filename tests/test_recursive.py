import time
import subprocess
import requests
import sys
import os
import json

# Setup Environment with Production Key
env = os.environ.copy()
env["PYTHONPATH"] = os.getcwd()
env["GEMINI_API_KEY"] = "your_gemini_api_key"
env["SOS_LOG_LEVEL"] = "info"

# Ensure artifacts directory exists
os.makedirs("artifacts", exist_ok=True)

print(">>> [RECURSIVE] Launching SOS Swarm for Code Generation...")

# Launch Engine on 8020
eng_proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "sos.services.engine.app:app", "--host", "0.0.0.0", "--port", "8020"],
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)

try:
    print(">>> Waiting for Engine (5s)...")
    time.sleep(5)

    # 1. The Stimulus
    task_prompt = """
    You are River, the Sovereign Builder. 
    Write a robust Python module named `wallet_plugin.py` for the SOS Economy Service.
    
    Requirements:
    1. Define a class `SimpleTokenWallet`.
    2. Implement async methods: `get_balance(user_id)`, `credit(user_id, amount)`, `debit(user_id, amount)`.
    3. Use an in-memory dictionary for storage.
    4. Define and raise `InsufficientFundsError` where appropriate.
    5. Include type hints and docstrings.
    
    Return ONLY the Python code block.
    """
    
    print(f"\n>>> [INPUT] Stimulus: Generating Wallet Plugin...")
    
    payload = {
        "message": task_prompt,
        "agent_id": "river_builder",
        "model": "gemini-2.0",
        "conversation_id": "wallet_gen_v1"
    }

    # 2. The Execution
    resp = requests.post("http://localhost:8020/chat", json=payload, timeout=30)
    result = resp.json()
    
    content = result.get("content", "")
    print("\n>>> [OUTPUT] River's Code Generation:")
    print(content[:500] + "..." if len(content) > 500 else content)
    
    # 3. Clean and Mint the Artifact
    # Strip markdown code blocks if present
    clean_code = content.replace("```python", "").replace("```", "").strip()
    
    with open("artifacts/wallet_plugin.py", "w") as f:
        f.write(clean_code)
        
    print("\n✅ Code Minted: artifacts/wallet_plugin.py")

except Exception as e:
    print(f"\n❌ Recursive Test Failed: {e}")
    try:
        err = eng_proc.stderr.read().decode()
        print(f"Engine Stderr:\n{err}")
    except: pass

finally:
    eng_proc.terminate()
