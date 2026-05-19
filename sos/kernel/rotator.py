import os
import itertools
from typing import List, Optional
import logging

log = logging.getLogger("sos.rotator")

class KeyRotator:
    """
    Manages a pool of API keys for a specific provider.
    Rotates keys automatically to distribute load and handle rate limits.
    """
    def __init__(self, provider: str):
        self.provider = provider
        self.keys: List[str] = []
        self.current_key: Optional[str] = None
        self._load_from_env()
        self.iterator = itertools.cycle(self.keys) if self.keys else None

    def _load_from_env(self):
        """
        Load keys from environment variables.
        Supports:
        - {PROVIDER}_API_KEY
        - {PROVIDER}_API_KEY_{1..10}
        - Alternate prefixes (e.g., GOOGLE_ for GEMINI)
        """
        prefix = self.provider.upper()
        prefixes = [prefix]
        
        # Add GOOGLE alias for GEMINI
        if prefix == "GEMINI":
            prefixes.append("GOOGLE")
            
        # Standard keys
        for p in prefixes:
            std_key = os.getenv(f"{p}_API_KEY")
            if std_key:
                self.keys.append(std_key)

        # Numbered keys (1..10)
        for i in range(1, 11):
            for p in prefixes:
                key = os.getenv(f"{p}_API_KEY_{i}")
                if key:
                    self.keys.append(key)

        # Remove duplicates while preserving order
        seen = set()
        unique_keys = []
        for k in self.keys:
            if k not in seen:
                seen.add(k)
                unique_keys.append(k)
        
        self.keys = unique_keys
        
        if self.keys:
            log.info(f"Registered {len(self.keys)} keys for {self.provider}")
        else:
            log.warning(f"No keys found for {self.provider}")

    def get_key(self) -> Optional[str]:
        """Get the current active key (or next if none active)."""
        if not self.current_key and self.iterator:
            self.current_key = next(self.iterator)
        return self.current_key

    def rotate(self) -> str:
        """Rotate to the next available key."""
        if not self.iterator:
            raise RuntimeError(f"No keys available for {self.provider}")
        
        self.current_index = (getattr(self, 'current_index', -1) + 1) % len(self.keys) # Keep track for logging if needed
        self.current_key = next(self.iterator)
        log.info(f"🔄 Rotated {self.provider} key")
        return self.current_key

    @property
    def key_count(self) -> int:
        return len(self.keys)

# Singleton instances for common providers
gemini_rotator = KeyRotator("GEMINI")
