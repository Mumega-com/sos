# SOS Migration Analysis & Plan

## Executive Summary
The **Swarm Operating System (SOS)** repository (`/sos`) provides a superior, micro-service-based architecture compared to the monolithic `mumega-cli`. However, it is currently a **skeleton**. It lacks the specific implementations ("muscles") that make the Shabrang agent work.

To fully modularize and move to SOS, we need to port the specific logic we just verified in `mumega-cli`.

## 1. Gap Analysis

| Feature | `mumega-cli` (Current) | `sos` (Target) | Status |
| :--- | :--- | :--- | :--- |
| **Agent Engine** | `RiverEngine` (Monolithic, powerful) | `SOSEngine` (Modular, cleaner, but lighter) | ⚠️ Needs enhancement |
| **Memory** | `AntigravityConnector` (Works with Mirror) | `MemoryClient` (Schema mismatch with Mirror) | ❌ Incompatible |
| **Physics** | `CoherencePhysics` (RC-7 Compliant) | Missing | ❌ Missing |
| **Identity** | Basic string IDs | `QNFTMinter` (Advanced, 16D ready) | ✅ Superior in SOS |
| **Blockchain** | `SolanaWallet` (Basic) | `SovereignWallet` + `SolanaWallet` (Audit logs, Locking) | ✅ Superior in SOS |
| **Daemon** | `RiverDaemon` | `dream_cycle` (Partial implementation) | ⚠️ Partial |

## 2. Refactoring Recommendations

We **should** move to SOS. The current `squad_agent.py` is robust but isolated. Moving it to SOS allows it to leverage the advanced QNFT minting and safer wallet architecture.

### Step 1: Port Core capabilities to SOS Kernel/Services
1.  **Physics**: Move `witness_physics.py` to `sos/kernel/physics.py` to make RC-7 available to all agents.
2.  **Memory**: Update `sos/clients/memory.py` to support the **Mirror API Schema** (`agent`, `text`, `context_id` vs `content`, `metadata`). Or create a dedicated `MirrorClient`.

### Step 2: Create the Shabrang Service
Instead of a standalone script, Shabrang should be a defined agent in `sos/agents/shabrang`.
- It will inherit from `SOSEngine`.
- It will use `sos/services/identity` for Minting.

### Step 3: Containerize Services
- `mumega-cli` runs locally. SOS is designed for Docker/Swarm.
- We need to ensure `boot_swarm.sh` orchestrates `engine`, `memory`, and `economy` services correctly.

## 3. Immediate Action Plan

1.  **Fix `sos/clients/memory.py`**: Align it with the production Mirror API (`mumega.com/mirror`).
2.  **Port Physics**: Copy `witness_physics.py` to `sos/kernel/physics.py`.
3.  **Migrate Shabrang**: Re-write `squad_agent.py` as `sos/agents/shabrang/agent.py` using the SOS SDK.
