import unittest
import time
from sos.services.memory.monitor import CoherenceMonitor

class TestCoherenceMonitor(unittest.TestCase):
    def test_stable_regime(self):
        monitor = CoherenceMonitor(tau=0.9, norm_window=10)
        print("\n--- Testing Stable Regime (High Coherence) ---")
        
        # Simulate high similarity (distance ~0.2 -> coherence ~0.8)
        for i in range(20):
            state = monitor.update(0.8) # Coherence score input
            print(f"Step {i}: Alpha={state.alpha_norm:.4f}, Regime={state.regime}")
        
        # Should be stable
        self.assertEqual(state.regime, "stable")

    def test_plasticity_trigger(self):
        monitor = CoherenceMonitor(tau=0.8, norm_window=10)
        print("\n--- Testing Plasticity Trigger (Surprise) ---")
        
        # 1. Establish baseline (High Coherence)
        for _ in range(20):
            monitor.update(0.9)
            
        # 2. Introduce Shock (Low Coherence)
        print(">>> INTRODUCING SHOCK (Novelty)")
        # Sudden drop in coherence (e.g., input is very different)
        state = monitor.update(0.2) 
        print(f"Shock: Alpha={state.alpha_norm:.4f}, Regime={state.regime}")
        
        # Should trigger low alpha (large negative)
        self.assertTrue(state.alpha_norm < -1.5, "Should detect negative alpha drift")
        self.assertEqual(state.regime, "plastic")

if __name__ == "__main__":
    unittest.main()
