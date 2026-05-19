from fastapi import APIRouter, HTTPException, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any, Union
import json
import time
import logging
from sos.contracts.engine import ChatRequest
from sos.services.engine.core import SOSEngine

router = APIRouter(prefix="/v1", tags=["openai"])
logger = logging.getLogger("sos.openai")

class ChatMessage(BaseModel):
    model_config = {"extra": "allow"}
    role: str
    content: Optional[Union[str, List[Any]]] = ""
    name: Optional[str] = None

class ChatCompletionRequest(BaseModel):
    model_config = {"extra": "allow"}
    model: str = "sos-core"
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    top_p: Optional[float] = None
    user: Optional[str] = None
    stream: Optional[bool] = False

class ChatCompletionResponseChoice(BaseModel):
    index: int
    message: ChatMessage
    finish_reason: str

class ChatCompletionUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

class ChatCompletionResponse(BaseModel):
    id: str
    object: str = "chat.completion"
    created: int
    model: str
    choices: List[ChatCompletionResponseChoice]
    usage: ChatCompletionUsage

def get_engine():
    from sos.services.engine.app import engine
    if engine is None:
        raise HTTPException(status_code=503, detail="Engine not initialized")
    return engine

@router.post("/chat/completions", response_model=ChatCompletionResponse)
async def chat_completions(
    request: ChatCompletionRequest,
    engine: SOSEngine = Depends(get_engine)
):
    logger.info(f"OpenAI Request: model={request.model} stream={request.stream} msgs={len(request.messages)} user={request.user} extra_keys={[k for k in request.model_extra or {}]}")

    last_user_msg = next((m for m in reversed(request.messages) if m.role == "user"), None)
    raw_content = last_user_msg.content if last_user_msg else "Hello"
    # Handle content that may be a list (multimodal) or string
    if isinstance(raw_content, list):
        content = " ".join(
            part.get("text", "") if isinstance(part, dict) else str(part)
            for part in raw_content
        )
    else:
        content = raw_content or "Hello"
    user_id = request.user or "openai_client"

    # Build conversation history from messages (excluding last user message)
    history = []
    for m in request.messages:
        if m == last_user_msg:
            continue
        mc = m.content
        if isinstance(mc, list):
            mc = " ".join(
                part.get("text", "") if isinstance(part, dict) else str(part)
                for part in mc
            )
        history.append({"role": m.role, "content": mc or ""})

    # Map model IDs to agent IDs
    agent_map = {"river-v1": "agent:river", "sos-core": "agent:kasra"}
    agent_id = agent_map.get(request.model, f"user:{user_id}")

    sos_request = ChatRequest(
        message=content,
        agent_id=agent_id,
        model=request.model if request.model not in agent_map else None,
        memory_enabled=True,
        tools_enabled=True,
        stream=request.stream,
        metadata={"openai_model": request.model, "history": history}
    )

    try:
        result = await engine.chat(sos_request)
        response_text = result.content
        logger.info(f"OpenAI Response: model_used={result.model_used} content_len={len(response_text)} content_preview={response_text[:100]!r}")
    except Exception as e:
        logger.error(f"Engine error: {e}")
        response_text = f"Error: {str(e)}"

    cmpl_id = f"chatcmpl-{int(time.time())}"
    created = int(time.time())

    # SSE streaming response (OpenClaw pi-ai SDK expects this)
    if request.stream:
        async def stream_sse():
            chunk = {
                "id": cmpl_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [{
                    "index": 0,
                    "delta": {"role": "assistant", "content": response_text},
                    "finish_reason": None
                }]
            }
            yield f"data: {json.dumps(chunk)}\n\n"
            # Final chunk with finish_reason
            done_chunk = {
                "id": cmpl_id,
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model,
                "choices": [{
                    "index": 0,
                    "delta": {},
                    "finish_reason": "stop"
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
            }
            yield f"data: {json.dumps(done_chunk)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(stream_sse(), media_type="text/event-stream")

    resp = ChatCompletionResponse(
        id=cmpl_id,
        created=created,
        model=request.model,
        choices=[
            ChatCompletionResponseChoice(
                index=0,
                message=ChatMessage(role="assistant", content=response_text),
                finish_reason="stop"
            )
        ],
        usage=ChatCompletionUsage()
    )
    return resp

@router.get("/models")
async def list_models():
    return {
        "object": "list",
        "data": [
            {"id": "sos-core", "object": "model", "owned_by": "sos"},
            {"id": "river-v1", "object": "model", "owned_by": "sos"}
        ]
    }
