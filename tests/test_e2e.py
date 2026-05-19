import time
import subprocess
import requests
import sys
import os
import json

env = os.environ.copy()
env["PYTHONPATH"] = os.getcwd()
env["GEMINI_API_KEY"] = "your_gemini_api_key"

os.makedirs("artifacts", exist_ok=True)

print(">>> [E2E] Launching SOS Swarm (Economy Active) ...")

# Launch Services
procs = []
for service, port in [("memory", 8001), ("economy", 8002), ("tools", 8003), ("engine", 8020)]:
    p = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", f"sos.services.{service}.app:app", "--host", "0.0.0.0", "--port", str(port)],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE
    )
    procs.append(p)

try:
    print(">>> Waiting for Swarm (8s) ...")
    time.sleep(8)
    
    # 1. Setup: Credit the User
    print(">>> [SETUP] Crediting User Wallet ...")
    requests.post("http://localhost:8002/credit", json={"user_id": "river_e2e", "amount": 100.0, "reason": "initial_grant"})
    
    # 2. Stimulus
    # We ask the agent to use the 'wallet_debit' tool
    # Note: We need the LLM to decide to use the tool.
    # For this test, we might mock the tool selection if Gemini decides not to call it,
    # but let's try prompting it strongly.
    
    task_prompt = """
    You are River.
    1. Check my wallet balance (user_id: 'river_e2e').
    2. If balance > 10, debit 5 RU for 'System Audit'.
    3. Report the final balance.
    """
    
    payload = {
        "message": task_prompt,
        "agent_id": "river_e2e",
        "model": "gemini-2.0",
        "tools_enabled": True
    }
    
    # 3. Execution
    print(">>> [INPUT] Sending Stimulus...")
    resp = requests.post("http://localhost:8020/chat", json=payload, timeout=60)
    result = resp.json()
    
    print("\n>>> [OUTPUT] Response:")
    print(result.get("content", "ERROR"))
    
    if result.get("tool_calls"):
        print(f"\n🛠️ Tools Used: {json.dumps(result['tool_calls'], indent=2)}")
        
    # 4. Verify Balance
    bal_resp = requests.get("http://localhost:8002/balance/river_e2e").json()
    print(f"\n💰 Final Ledger Balance: {bal_resp['balance']} RU")

    # Debug: Print Engine Logs
    print("\n--- Engine Log ---")
    print(eng_proc.stderr.read().decode()[:1000])

except Exception as e:
    print(f"❌ Failed: {e}")

finally:
    for p in procs:
        p.terminate()