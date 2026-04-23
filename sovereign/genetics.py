
from typing import List, Dict, Optional, Literal
from pydantic import BaseModel, Field
import time
import uuid

class PhysicsState(BaseModel):
    """The 16D Mirror State + ARF Variables"""
    # 16D Vector (Inner/Outer Octaves)
    inner: Dict[str, float] = Field(description="Inner Octave Dimensions")
    outer: Dict[str, float] = Field(default_factory=dict, description="Outer Octave Dimensions")
    
    # ARF Variables (The Physics)
    R: float = Field(0.0, description="Receptivity")
    Psi: float = Field(0.0, description="Potential")
    C: float = Field(0.0, description="Coherence")
    
    # Derived
    regime: Literal["flow", "chaos", "coercion"] = "flow"

class Economics(BaseModel):
    """The Agent's Wallet and Value"""
    wallet_address: Optional[str] = None # TON Wallet
    token_balance: float = 0.0
    hourly_rate: float = 0.0
    roi_score: float = 0.0 # Return on Investment History
    daily_budget_limit: float = 100.0 # Default daily limit in USD/Tokens
    daily_spent: float = 0.0
    last_spend_reset: float = Field(default_factory=lambda: time.time())
    
    # --- ENDOGENOUS VALUES (Loop 2) ---
    # These weights evolve based on outcomes.
    values: Dict[str, float] = Field(default_factory=lambda: {
        "sovereignty": 0.9,
        "efficiency": 0.7,
        "hadi_alignment": 0.95,
        "innovation": 0.6
    })

class Belief(BaseModel):
    """A grounded self-model entry (Loop 3)"""
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    claim: str
    confidence: float = 0.5 # 0.0 to 1.0
    source: Literal["fact", "hypothesis", "dream"] = "hypothesis"
    created_at: float = Field(default_factory=time.time)
    verified_at: Optional[float] = None

class AgentDNA(BaseModel):
    """The Sovereign Genetic Code"""
    # Identity
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    generation: int = 1
    born_at: float = Field(default_factory=time.time)
    parent_id: Optional[str] = None
    
    # Capabilities & Strategy (Loop 1)
    model_provider: str = "deepseek" 
    tools: List[str] = [] 
    learning_strategy: Literal["explore", "exploit", "conserve", "refine"] = "exploit"
    
    # Core State
    physics: PhysicsState
    economics: Economics = Field(default_factory=Economics)
    
    # Self-Model (Loop 3)
    beliefs: List[Belief] = Field(default_factory=list)
    
    # Narrative
    story: Optional[str] = None 

    
    @property
    def fingerprint(self) -> str:
        """A short hash for CLI identification"""
        return self.id[:8]
    
    def evolve(self, mutation: Dict):
        """Apply a mutation to the DNA"""
        self.generation += 1
        # Update logic would go here
        return self
