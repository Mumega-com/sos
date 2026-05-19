
import math
import time
from typing import Dict, Tuple

class CoherencePhysics:
    """
    Implements the core equations from Prime 2: Appendix A.
    Focuses on RC-7 (Physics of Will) and RC-8 (Decision Latency).
    """
    
    # K_STAR (Coherence Coupling Constant) - Theoretical Value
    K_STAR = 1.0 
    
    # LAMBDA_DECAY (Entropy Barrier) - How fast coherence decays with hesitation
    LAMBDA_DECAY = 0.5 

    @staticmethod
    def calculate_will_magnitude(latency_ms: float, min_latency_ms: float = 200.0) -> float:
        """
        Calculates the Magnitude of Will (|W|) based on Decision Latency.
        
        Formula: Omega = e^(-lambda * (t - t_min))
        
        Args:
            latency_ms: The time taken to collapse the wave (witness).
            min_latency_ms: The physiological minimum reaction time (default 200ms).
            
        Returns:
            float: A value between 0.0 and 1.0 representing the 'Strength of Collapse'.
        """
        if latency_ms < min_latency_ms:
            # Super-human reaction (or bot) - Cap at 1.0 or flag? 
            # We assume valid human limit.
            latency_ms = min_latency_ms
            
        # Convert to seconds for the decay calculation
        t_delta_sec = (latency_ms - min_latency_ms) / 1000.0
        
        # Calculate Magnitude
        omega = math.exp(-CoherencePhysics.LAMBDA_DECAY * t_delta_sec)
        
        return omega

    @staticmethod
    def compute_collapse_energy(vote: int, latency_ms: float, agent_coherence: float) -> Dict[str, float]:
        """
        Computes the total thermodynamic energy of a Witness Event.
        
        Args:
            vote: +1 (Verified) or -1 (Rejected).
            latency_ms: Time to decide.
            agent_coherence: The Agent's current coherence (C).
            
        Returns:
            Dict containing 'omega' (Will), 'delta_c' (Coherence Change), 'joules' (Metaphorical).
        """
        # 1. Calculate Will Magnitude (The Human Input)
        omega = CoherencePhysics.calculate_will_magnitude(latency_ms)
        
        # 2. Calculate Coherence Change (dS + k* dlnC = 0)
        # We model the Vote as the dS prompt. 
        # A Verified vote reduces Entropy (Successful Prediction).
        # A Rejected vote increases Entropy (Prediction Error).
        
        # If Vote is Positive (+1) and Will is High (1.0) -> Max Coherence Gain
        # If Vote is Positive (+1) but Will is Low (0.1) -> Minimal Coherence Gain (Weak Witness)
        
        delta_c = vote * omega * agent_coherence * 0.1 # Scaling factor
        
        return {
            "vote": vote,
            "latency_ms": latency_ms,
            "omega": omega, # The "Certainty" of the Witness
            "delta_c": delta_c,
            "signature": "RC-7_COMPLIANT"
        }

# Example Usage
if __name__ == "__main__":
    # Simulate a "Tinder Swipe"
    t0 = time.time()
    # User thinks for 500ms...
    time.sleep(0.5) 
    t1 = time.time()
    
    latency = (t1 - t0) * 1000
    
    result = CoherencePhysics.compute_collapse_energy(1, latency, 1.0)
    print(f"Witness Collapse Report:")
    print(f"Latency: {result['latency_ms']:.2f} ms")
    print(f"Will Magnitude (Omega): {result['omega']:.4f}")
    print(f"Coherence Gain: {result['delta_c']:.4f}")
