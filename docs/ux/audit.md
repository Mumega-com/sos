# SOS UX Audit & Guidelines
**Status:** Initial Draft
**Focus:** The "Empire of the Mind" Interface

## 1. Core Principle: The Card Game
The primary interaction model for the 10M users (The Blind Swarm) must be **"Swipe for Truth"**.
*   **Mobile First:** The UI must feel like Tinder, not Jira.
*   **Binary Input:** Users provide high-fidelity signal via low-fidelity input (Left/Right swipe).
*   **Latency as Signal:** We measure *how fast* they swipe to calculate $\Omega$ (Will Magnitude).

## 2. Key User Flows
1.  **Onboarding (The Inoculation):**
    *   User clicks "Start".
    *   User sees a "Spore" animation planting itself.
    *   User mints their "Soul" (Hermes Key generation).
    *   *No email/password signup.*

2.  **The Witness Loop:**
    *   Card appears: "Agent Alice wants to Tweet X."
    *   User scans content.
    *   User Swipes Right (Witnessed).
    *   Reward animation: +10 $MIND.

3.  **The Council (Advanced):**
    *   For Squad Leaders only.
    *   Chat interface (Telepathy) connected to Redis Bus.
    *   Proposal cards appear in-stream for voting.

## 3. Visual Language
*   **Theme:** "Liquid Fortress" / "Bioluminescent Mycelium".
*   **Colors:** Deep indigo/black background, glowing neon nodes (green/gold).
*   **Typography:** Monospace for data (Truth), Serif for narrative (Mythos).

## 4. Next Steps
*   Implement `WitnessCard` component in React.
*   Connect `WitnessCard` to `TonWallet` plugin for rewards.
