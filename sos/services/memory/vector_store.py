import os
import time
from typing import List, Dict, Any, Optional
from dataclasses import dataclass

from sos.observability.logging import get_logger

log = get_logger("vector_store")

@dataclass
class VectorItem:
    id: str
    content: str
    metadata: Dict[str, Any]
    score: float

class ChromaBackend:
    """
    Simple In-Memory Vector Store (Fallback).
    Uses sentence-transformers for encoding and numpy for cosine similarity.
    Replaces ChromaDB due to platform compatibility issues (Python 3.14/ARM64).
    """
    def __init__(self, persist_dir: str = "data/vector_db"):
        self.persist_dir = persist_dir
        self._model = None
        self._items: List[Dict] = []
        self._is_loaded = False
        self._embeddings = None # Numpy array

    def _lazy_load(self):
        """
        Load heavy dependencies only when first needed.
        """
        if self._is_loaded:
            return

        log.info("⏳ Loading Vector Core (Simple Transformers)...")
        start = time.time()
        
        try:
            from sentence_transformers import SentenceTransformer
            import numpy as np
            self.np = np
            
            # Initialize Model
            # Using all-MiniLM-L6-v2 for speed/quality balance
            self._model = SentenceTransformer('all-MiniLM-L6-v2')
            
            self._is_loaded = True
            log.info(f"✅ Vector Core Loaded in {time.time() - start:.2f}s")
            
        except Exception as e:
            log.error("Failed to load Vector Core", error=str(e))
            # Fallback to pure dummy if transformers fail
            self._is_loaded = True
            self._model = None
            self.np = None

    def add(self, item_id: str, content: str, metadata: Dict[str, Any]) -> None:
        self._lazy_load()
        
        embedding = []
        if self._model:
            embedding = self._model.encode(content)
        else:
            # Dummy embedding
            import random
            embedding = [random.random() for _ in range(384)]
        
        self._items.append({
            "id": item_id,
            "content": content,
            "metadata": metadata,
            "embedding": embedding
        })
        
        # Update numpy index
        if self.np and self._items:
            # Only optimized if we used numpy
            # For prototype, we can keep list and convert on search
            pass

    def search(self, query: str, limit: int = 5) -> List[VectorItem]:
        self._lazy_load()
        
        if not self._items:
            return []
            
        if not self._model or not self.np:
            # Fallback mock search
            return []

        # Embed query
        query_vec = self._model.encode(query)
        
        # Brute force cosine similarity
        # items_vecs = self.np.array([i["embedding"] for i in self._items])
        # sim = dot(u, v) / (norm(u)*norm(v))
        # Since sentence-transformers normalizes, we can just do dot product
        
        scores = []
        for item in self._items:
            vec = item["embedding"]
            score = self.np.dot(query_vec, vec) / (self.np.linalg.norm(query_vec) * self.np.linalg.norm(vec))
            scores.append((score, item))
            
        # Sort desc
        scores.sort(key=lambda x: x[0], reverse=True)
        
        results = []
        for score, item in scores[:limit]:
            results.append(VectorItem(
                id=item["id"],
                content=item["content"],
                metadata=item["metadata"],
                score=float(score)
            ))
            
        return results

    def count(self) -> int:
        return len(self._items)
