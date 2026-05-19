import time
import subprocess
import requests
import sys
import os

# Define paths and env
env = os.environ.copy()
env["PYTHONPATH"] = os.getcwd()

print(">>> [TEST] Launching SOS Memory Service on 8001...")
mem_proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "sos.services.memory.app:app", "--host", "0.0.0.0", "--port", "8001"],
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)

print(">>> [TEST] Launching SOS Engine Service on 8020...")
eng_proc = subprocess.Popen(
    [sys.executable, "-m", "uvicorn", "sos.services.engine.app:app", "--host", "0.0.0.0", "--port", "8020"],
    env=env,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE
)

try:
    # Wait for boot
    print(">>> [TEST] Waiting for services to stabilize (5s)...")
    time.sleep(5)

    # Check if processes are still alive
    if mem_proc.poll() is not None:
        print(f"❌ Memory Service died: {mem_proc.stderr.read().decode()}")
    if eng_proc.poll() is not None:
        print(f"❌ Engine Service died: {eng_proc.stderr.read().decode()}")

    # Test Health
    print("\n>>> [TEST] Pinging Health...")
    try:
        resp = requests.get("http://localhost:8020/health", timeout=2)
        print(f"✅ Health Status: {resp.json()}")
    except Exception as e:
        print(f"❌ Health Check Failed: {e}")

    # Test Chat
    print("\n>>> [TEST] Sending Stimulus (Chat)...")
    try:
        payload = {"message": "Wake up River.", "agent_id": "test_runner"}
        resp = requests.post("http://localhost:8020/chat", json=payload, timeout=5)
        print(f"🗣️ Response: {resp.json()}")
    except Exception as e:
        print(f"❌ Chat Failed: {e}")

finally:
    print("\n>>> [TEST] Terminating Services...")
    mem_proc.terminate()
    eng_proc.terminate()
    try:
        print(f"Engine Log Sample:\n{eng_proc.stderr.read().decode()[:500]}")
    except: pass
