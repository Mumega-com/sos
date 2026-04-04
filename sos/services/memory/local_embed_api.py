"""
SOS Local Embedding Service

Provides OpenAI-compatible embedding endpoint using FastEmbed (CPU optimized).
Eliminates reliance on external providers for engram storage.
"""

import os
import time
import logging
from typing import List, Union, Dict, Any
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from fastembed import TextEmbedding

# Configure logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
logger = logging.getLogger("sos.local_embed")

app = FastAPI(title="SOS Local Embedding API")

# Load model on startup
# BAAI/bge-small-en-v1.5 is small (67MB) and highly accurate
MODEL_NAME = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
logger.info(f"🧬 Initializing local embedding model: {MODEL_NAME}")
model = TextEmbedding(model_name=MODEL_NAME)

class EmbeddingRequest(BaseModel):
    input: Union[str, List[str]]
    model: str = "local"

class EmbeddingData(BaseModel):
    object: str = "embedding"
    embedding: List[float]
    index: int

class Usage(BaseModel):
    prompt_tokens: int = 0
    total_tokens: int = 0

class EmbeddingResponse(BaseModel):
    object: str = "list"
    data: List[EmbeddingData]
    model: str
    usage: Usage

@app.get("/health")
async def health():
    return {"status": "ok", "model": MODEL_NAME}

@app.post("/v1/embeddings", response_model=EmbeddingResponse)
async def create_embeddings(request: EmbeddingRequest):
    """OpenAI-compatible embeddings endpoint."""
    try:
        start_time = time.time()
        
        # Handle single string input
        inputs = [request.input] if isinstance(request.input, str) else request.input
        
        # Generate embeddings
        # FastEmbed returns a generator, we cast to list
        embeddings_list = list(model.embed(inputs))
        
        data = []
        for i, emb in enumerate(embeddings_list):
            data.append(EmbeddingData(
                embedding=emb.tolist(),
                index=i
            ))
            
        duration = time.time() - start_time
        logger.info(f"✨ Processed {len(inputs)} docs in {duration*1000:.2f}ms")
        
        return EmbeddingResponse(
            data=data,
            model=MODEL_NAME,
            usage=Usage(prompt_tokens=0, total_tokens=0) # We don't care about tokens locally
        )
        
    except Exception as e:
        logger.error(f"Embedding failed: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("LOCAL_EMBED_PORT", 7997))
    uvicorn.run(app, host="0.0.0.0", port=port)
