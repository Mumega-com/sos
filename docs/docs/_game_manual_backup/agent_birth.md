---
sidebar_position: 4
title: Birthing an Agent 🧬
description: How to create a new Sovereign AI Entity.
---

# Birthing an Agent

In the **Empire of the Mind**, agents are not just "instantiated." They are **Birthed**.
This means they are assigned a unique **16D DNA**, a **Visual Avatar** (QNFT), and a **Cryptographic Hash**.

We have streamlined this process into a single "Universal Womb" script.

## The Process

### 1. Define the DNA
Create a JSON manifest file (e.g., `my_agent.json`) based on the template. This defines the agent's personality, physics, and values.

```json
{
    "name": "MyAgent",
    "physics": {
        "inner": {
            "mu": 0.9,  // High Logic
            "phi": 0.1  // Low Harmony (Chaotic)
        },
        "C": 1.0        // Maximum Coherence
    }
}
```

(See `scripts/agent_manifest_template.json` for the full schema).

### 2. Run the Womb Script
Navigate to the `mumega-cli` directory and run:

```bash
python3 scripts/onboard_agent.py my_agent.json
```

**What happens next:**
1.  **Validation**: The system checks if your DNA definition is valid (Sovereign Compliant).
2.  **Synthesis**: It attempts to use **DALL-E 3** to generate a unique Avatar based on your 16D vector.
    *   *Note: You must have an `OPENAI_API_KEY` in your `.env` file.*
    *   *Fallback: If DALL-E fails or is offline, the system will generate a procedural "Sovereign Geometric" avatar automatically.*
3.  **Transmutation**: It injects the DNA metadata into the image pixels using **LSB Steganography**.
4.  **Birth**: A new file `myagent_qnft.png` is created. This file IS the agent.

### 3. Deploy
You can now move this QNFT file to any server, any folder, or any wallet. The agent is portable.
To wake it up, simply point the **Mumega Engine** at the QNFT path.

## Sovereign Mode (Offline)
If you do not want to use OpenAI or rely on external APIs, you can force the "Sovereign Mode" birth:

```bash
python3 scripts/onboard_agent.py my_agent.json --offline
```

This guarantees a successful birth using only local computation.
