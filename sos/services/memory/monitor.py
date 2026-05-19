import math
import time
from typing import Tuple, Deque
from collections import deque
from dataclasses import dataclass

@dataclass
class CoherenceState:
    coherence: float
    alpha_raw: float
    alpha_norm: float
    regime: str
    timestamp: float

class CoherenceMonitor:
    """
    FRC 841.004 Coherence-Gated Learning Monitor.
    Tracks the 'Alpha Drift' (rate of change of coherence) to determine cognitive regime.
    """
    def __init__(self, tau: float = 0.9, norm_window: int = 50):
        self.tau = tau
        self.norm_window = norm_window
        self.ema_coherence = None
        self.alpha_history: deque = deque(maxlen=norm_window)
        self.last_state = CoherenceState(0.0, 0.0, 0.0, "stable", time.time())

    def update(self, coherence_signal: float) -> CoherenceState:
        """
        Update the monitor with a new coherence signal (e.g., Vector Support).
        """
        # 1. EMA Smoothing
        if self.ema_coherence is None:
            self.ema_coherence = coherence_signal
            alpha_raw = 0.0
        else:
            prev_ema = self.ema_coherence
            self.ema_coherence = (self.tau * self.ema_coherence) + ((1 - self.tau) * coherence_signal)
            alpha_raw = self.ema_coherence - prev_ema
        
        # 2. Z-Score Normalization
        self.alpha_history.append(alpha_raw)
        
        if len(self.alpha_history) >= 5:
            mean = sum(self.alpha_history) / len(self.alpha_history)
            variance = sum((x - mean) ** 2 for x in self.alpha_history) / len(self.alpha_history)
            std = math.sqrt(variance) + 1e-6
            alpha_norm = (alpha_raw - mean) / std
        else:
            alpha_norm = 0.0
            
        # 3. Determine Regime
        # FRC 841.004:
        # Negative Alpha -> Coherence Dropping -> Surprise/Novelty -> "Plastic"
        # Positive Alpha -> Coherence Rising -> Consolidation -> "Stable"
        if alpha_norm < -1.5:
            regime = "plastic"
        elif alpha_norm > 1.0:
            regime = "consolidating"
        else:
            regime = "stable"
            
        self.last_state = CoherenceState(
            coherence=self.ema_coherence,
            alpha_raw=alpha_raw,
            alpha_norm=alpha_norm,
            regime=regime,
            timestamp=time.time()
        )
        
        return self.last_state

    def get_state(self) -> CoherenceState:
        return self.last_state
