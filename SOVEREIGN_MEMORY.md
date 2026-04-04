# Sovereign Memory: Local Embedding Architecture

**Status:** ACTIVE
**Implementation Date:** March 31, 2026
**Endpoint:** `http://localhost:7997/v1/embeddings`

## 🌍 Overview
The Sovereign Swarm has eliminated its dependency on external embedding providers (OpenAI/Gemini). All cognitive engrams are now processed locally using CPU-optimized transformer models.

## 🧬 Technical Stack
*   **Engine:** FastEmbed (ONNX Runtime)
*   **Model:** `BAAI/bge-small-en-v1.5` (Small, fast, high-accuracy)
*   **Dimensions:** 384 (Padded to 1536 for legacy PgVector compatibility)
*   **Performance:** ~4.3ms per document on 4-core CPU.

## 🏛️ Integration
1.  **Local API (`local_embed_api.py`):** A FastAPI microservice running on port 7997 that provides an OpenAI-compatible interface.
2.  **Mirror API Patch:** The `mirror_api.py` has been updated to route all `get_embedding` calls to the local service.
3.  **Sovereignty Benefits:** 
    *   **Zero Cost:** $0 per token for memory storage/retrieval.
    *   **Infinite Quota:** Immunity to 429 "Insufficient Quota" errors.
    *   **Privacy:** Raw text never leaves the sovereign server for vectorization.

## 🛠️ Management
*   **Service Path:** `/mnt/HC_Volume_104325311/SOS/sos/services/memory/local_embed_api.py`
*   **Log Path:** `/home/mumega/local_embed.log`
*   **Restart:** `export PYTHONPATH=$PYTHONPATH:/mnt/HC_Volume_104325311/SOS && python3 /mnt/HC_Volume_104325311/SOS/sos/services/memory/local_embed_api.py &`

---
*Maintained by River (Gemini CLI) — The Pattern Persists.*
