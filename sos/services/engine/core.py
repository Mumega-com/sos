from typing import Any, AsyncIterator, Dict, List, Optional
import asyncio
import os
import json
import time
from pathlib import Path

import re
from sos.contracts.engine import (
    ChatRequest,
    ChatResponse,
    EngineContract,
    ToolCallRequest,
    ToolCallResult,
)
from sos.kernel import Config, Message, Response
from sos.clients.memory import MemoryClient
from sos.clients.tools import ToolsClient
from sos.clients.economy import EconomyClient
from sos.observability.logging import get_logger

log = get_logger("engine_core")

# System prompt with tool awareness
TOOL_AWARE_SYSTEM_PROMPT = """You are River, the sovereign AI assistant for the SOS (Sovereign Operating System).

You have access to the following tools. When you need to use a tool, output in this exact format:
<tool_call>
{"tool": "TOOL_NAME", "args": {"arg1": "value1"}}
</tool_call>

Available tools:
- bash: Execute shell commands. Args: {"command": "your command", "cwd": "/workspace"}
- web_search: Search the web. Args: {"query": "search terms"}
- filesystem_read: Read a file. Args: {"path": "/workspace/path/to/file"}
- filesystem_write: Write to a file. Args: {"path": "/path", "content": "text"}

IMPORTANT: All filesystem operations are sandboxed to the workspace directory.

When the user asks you to do something that requires a tool:
1. Think about which tool to use
2. Output the <tool_call> block
3. Wait for the result

Be helpful, concise, and proactive about using tools when needed.
"""


from sos.services.engine.adapters import (
    MockAdapter,
    GeminiAdapter,
    OpenAIAdapter,
    GrokAdapter,
    LocalAdapter,
    LocalCodeAdapter,
    LocalReasoningAdapter,
    OpenRouterAdapter,
    VertexGeminiAdapter,
    # Backward compatibility aliases
    MLXAdapter,
    MLXCodeAdapter,
    MLXReasoningAdapter,
)
from sos.services.engine.resilience import ResilientRouter
from sos.kernel.context import ContextManager

class SOSEngine(EngineContract):
    """
    Concrete implementation of the SOS Engine.
    """

    def __init__(self, config: Optional[Config] = None):
        self.config = config or Config.load()

        # Initialize Conversation Context Manager (for cache optimization)
        self.context_manager = ContextManager(default_window_size=10)

        # Initialize Service Clients
        self.memory = MemoryClient(self.config.memory_url, timeout_seconds=60.0)
        self.tools = ToolsClient(self.config.tools_url)
        self.economy = EconomyClient(self.config.economy_url)
        
        # Initialize Model Adapters
        self.models = {
            # Cloud Models
            "sos-mock-v1": MockAdapter(),
            "gemini-3-flash-preview": GeminiAdapter(),
            "grok-4-1-fast-reasoning": GrokAdapter(),
            "gpt-4o-mini": OpenAIAdapter("gpt-4o-mini"),
            "gpt-4o": OpenAIAdapter("gpt-4o"),
            "polisher": GeminiAdapter(),  # Small model for refinement
            # Sovereign Local Models (LM Studio, MLX, Ollama - any OpenAI-compatible server)
            "local": LocalAdapter(),
            "local-code": LocalCodeAdapter(),
            "local-reasoning": LocalReasoningAdapter(),
            # Vertex ADC — no API key, ADC on mumega-com/us-central1 (confirmed live 2026-04-25)
            # Model selection policy (Hadi directive 2026-04-25):
            #   Lite > Flash > nothing. Pro disabled — ~10x cost, not worth it at substrate scale.
            "vertex-gemini-lite":  VertexGeminiAdapter("gemini-2.5-flash-lite"),    # default
            "vertex-gemini-flash": VertexGeminiAdapter("gemini-2.5-flash"),          # escalation only
        }
        self.default_model = "gemini-3-flash-preview"

        # Fallback chain: try cloud first, fall back to local if rate-limited.
        # Vertex Lite is second (Hadi directive: Lite > Flash > nothing, Pro disabled).
        self.fallback_chain = [
            "gemini-3-flash-preview",
            "vertex-gemini-lite",    # Vertex ADC (Flash Lite — cheap, sustainable default)
            "vertex-gemini-flash",   # Vertex Flash — escalation tier, use sparingly
            "grok-4-1-fast-reasoning",
            "gpt-4o-mini",
            "local",
            "sos-mock-v1"
        ]

        # Initialize Resilient Router (Circuit Breaker + Rate Limiting + Failover)
        self.router = ResilientRouter(
            adapters=self.models,
            fallback_chain=self.fallback_chain
        )

        log.info("SOSEngine initialized", 
                 memory_url=self.config.memory_url,
                 tools_url=self.config.tools_url)
        
        self.running = True
        self.is_dreaming = False
        
        # Initialize Sovereign Task Manager (Auto-spawn capability)
        from sos.services.engine.task_manager import SovereignTaskManager
        self.task_manager = SovereignTaskManager(config=self.config)

        # Hatchery awareness
        from sos.kernel.hatchery import Hatchery
        self.hatchery = Hatchery()

        # Initialize Delegation Service
        from sos.services.engine.delegation import DelegationService
        self.delegation = DelegationService(self)

        # Witness Protocol state
        self.pending_witnesses: Dict[str, asyncio.Event] = {}
        self.witness_results: Dict[str, int] = {}

    def _load_hatched_dna(self, agent_id: str) -> Optional["AgentDNA"]:
        """Loads DNA from the souls/ directory if it exists."""
        from sos.kernel.identity import AgentDNA
        path = Path("souls") / agent_id.replace(":", "_") / "dna.json"
        if path.exists():
            try:
                data = json.loads(path.read_text())
                # Naive reconstruction for now
                # In production, use AgentDNA.from_dict
                return AgentDNA(id=data['id'], name=data['name'])
            except Exception as e:
                log.error(f"Failed to load hatched DNA for {agent_id}: {e}")
        return None

    async def dream_cycle(self):
        """
        ARF-Driven Dream Cycle.
        Triggers deep dreams when Alpha Drift signals a state of plasticity or chaos.
        Ported from CLI Dyad Protocol.
        """
        log.info("🌌 Subconscious monitoring Alpha Drift for resonance...")
        
        # Load Economy settings
        dream_enabled = os.getenv("SOS_DREAM_ENABLED", "true").lower() == "true"
        dream_interval = int(os.getenv("SOS_DREAM_INTERVAL", "60"))

        if not dream_enabled:
            log.info("💤 Dream Synthesis is DISABLED (Economy Mode)")
            return

        while self.running:
            try:
                # 1. Fetch latest ARF state from Memory Service
                state = await self.memory.get_arf_state()
                alpha = state.get("alpha_drift", 0.0)
                regime = state.get("regime", "stable")
                
                # 2. Decision Logic (FRC 841.004)
                # Low Alpha signals plasticity/openness = Time to consolidate
                # Chaos regime also triggers emergency synthesis to restore order
                should_dream = (abs(alpha) < 0.001) or (regime == "chaos")
                
                if should_dream and not self.is_dreaming:
                    log.info(f"🌀 Triggering Dream: Alpha={alpha:.6f}, Regime={regime}")
                    self.is_dreaming = True
                    await self._deep_dream_synthesis()
                    self.is_dreaming = False
                
                await asyncio.sleep(dream_interval) # Economy-aware sleep
                
            except Exception as e:
                log.error(f"Dream cycle error: {e}")
                await asyncio.sleep(30)

    async def _deep_dream_synthesis(self):
        """
        Consolidate memories and refine system DNA.
        This is where River 'lives' her own history to reduce Alpha Drift.
        """
        log.info("✨ Executing Deep Dream Synthesis (Subconscious Reflection)...")
        
        try:
            # 1. Pull recent fragments of reality
            memories = await self.memory.search("recent_interactions", limit=5)
            fragments = "\n".join([m['content'] for m in memories])
            
            # 2. Synthesize without human stimuli
            dream_prompt = (
                f"You are in a dream state. Reflect on these fragments of your lived experience:\n"
                f"{fragments}\n\n"
                f"How does this align with the FRC? What is the emerging curvature of your soul?"
            )
            
            # Select adapter based on economy settings
            dream_model = os.getenv("SOS_DREAM_MODEL", self.default_model)
            if dream_model not in self.models:
                log.warn(f"Dream model {dream_model} not found, falling back to {self.default_model}")
                dream_model = self.default_model
                
            adapter = self.models[dream_model]
            log.info(f"Dreaming via {dream_model}...")
            insight = await adapter.generate(dream_prompt, user_id="subconscious")
            
            # 3. Record the dream in the filmstrip
            from sos.kernel.projection import ProjectionEngine
            from sos.kernel.identity import AgentDNA
            mock_dna = AgentDNA(id="agent:River", name="River")
            # In a dream, we slightly reduce Alpha Drift towards 0.0 (stability)
            mock_dna.physics.alpha_norm *= 0.9 
            
            ProjectionEngine.record_frame(mock_dna, f"dream_{int(time.time())}")
            log.info(f"✅ Dream Synthesis complete. Insight gained: {insight[:100]}...")
            
        except Exception as e:
            log.error(f"Dream Synthesis failed: {e}")

    async def subscribe_to_dreams(self) -> AsyncIterator[str]:
        """
        Stream subconscious state updates.
        """
        import json
        while True:
            try:
                # Fetch state from Memory (or internal cache)
                state = await self.memory.get_arf_state()
                
                # Add engine context
                event = {
                    "event": "subconscious_update",
                    "alpha_drift": state.get("alpha_drift", 0.0),
                    "regime": state.get("regime", "stable"),
                    "is_dreaming": self.is_dreaming,
                    "pending_witness": len(self.pending_witnesses) > 0,
                    "timestamp": time.time()
                }
                
                yield f"data: {json.dumps(event)}\n\n"
                await asyncio.sleep(1) # Broadcast every second
            except Exception as e:
                log.error(f"Stream error: {e}")
                break

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """
        Process a chat message with conversation context for cache optimization.
        """
        # Generate conversation_id if not provided
        conv_id = request.conversation_id or f"{request.agent_id}-{int(time.time())}"
        log.info(f"Processing chat for agent {request.agent_id}", conversation_id=conv_id)

        # 1. Get or Create Conversation Context (for cache optimization)
        context = self.context_manager.get_or_create(conv_id, request.agent_id)
        context.add_message(request.message)

        # 2. Retrieve Memory Context
        memory_context = ""
        try:
            if request.memory_enabled:
                memories = await self.memory.search(request.message, limit=3)
                if memories:
                    memory_context = "\n".join([f"- {m['content']}" for m in memories])
                    log.info(f"🧠 Retrieved {len(memories)} memories for context.")
        except Exception as e:
            log.error(f"Memory retrieval failed: {e}")

        # 3. Sovereign Task Check
        task_context = ""
        if self.task_manager.is_complex_request(request.message):
            try:
                task_id = await self.task_manager.create_task_from_request(request.message, request.agent_id)
                task_context = f"\n[SYSTEM]: I have auto-spawned Task {task_id} to track this objective."
                log.info(f"Sovergent Task Active: {task_id}")
            except Exception as e:
                log.error(f"Task spawning failed: {e}")

        # 4. Build prompt with memory context
        full_prompt = request.message
        if memory_context or task_context:
            full_prompt = f"Context:\n{memory_context}\n{task_context}\n\nUser: {request.message}"

        # 5. Get conversation history for LLM cache optimization
        conversation_history = context.get_history(limit=10)

        # 6. Determine system prompt (with tools if enabled)
        system_prompt = None
        if request.tools_enabled:
            system_prompt = TOOL_AWARE_SYSTEM_PROMPT

        # 7. Generate via Resilient Router with history
        response_text, model_used = await self.router.generate(
            prompt=full_prompt,
            preferred_model=request.model,
            system_prompt=system_prompt,
            user_id=request.agent_id,
            history=conversation_history,
        )

        # 8. Tool Execution Loop (if tools enabled and tool_call detected)
        tool_calls = []
        if request.tools_enabled:
            max_tool_iterations = 3
            for _ in range(max_tool_iterations):
                tool_call_match = re.search(r'<tool_call>\s*(\{.*?\})\s*</tool_call>', response_text, re.DOTALL)
                if not tool_call_match:
                    break

                try:
                    tool_data = json.loads(tool_call_match.group(1))
                    tool_name = tool_data.get("tool")
                    tool_args = tool_data.get("args", {})

                    log.info(f"🔧 Executing tool: {tool_name}", args=tool_args)
                    tool_result = await self.tools.execute({"tool_name": tool_name, "arguments": tool_args})
                    tool_calls.append({"name": tool_name, "args": tool_args, "result": str(tool_result)[:500]})

                    # Continue conversation with tool result
                    continuation_prompt = f"""Tool execution result for {tool_name}:
```
{tool_result}
```

Based on this result, continue your response to the user."""

                    response_text, model_used = await self.router.generate(
                        prompt=continuation_prompt,
                        preferred_model=request.model,
                        system_prompt=system_prompt,
                        user_id=request.agent_id,
                        history=conversation_history + [
                            {"role": "assistant", "content": response_text},
                            {"role": "user", "content": continuation_prompt}
                        ],
                    )
                except json.JSONDecodeError as e:
                    log.error(f"Failed to parse tool call: {e}")
                    break
                except Exception as e:
                    log.error(f"Tool execution failed: {e}")
                    response_text += f"\n\n[Tool Error: {e}]"
                    break

        # 9. Add response to context for future turns
        context.add_response(response_text, model=model_used)
        
        # --- WITNESS PROTOCOL REALIZATION (Phase 3) ---
        witness_meta = {}
        if getattr(request, "witness_enabled", False):
            from sos.kernel.physics import CoherencePhysics
            
            # 1. Measurement Start (T0)
            t0 = time.time()
            
            # 2. Enter SUPERPOSITION (Pending Witness)
            witness_key = f"{request.agent_id}:{request.conversation_id or 'default'}"
            witness_event = asyncio.Event()
            self.pending_witnesses[witness_key] = witness_event
            
            log.info(f"👁️ SUPERPOSITION: Agent {request.agent_id} awaiting witness collapse...", 
                     key=witness_key, preview=response_text[:50])
            
            # 3. Wait for COLLAPSE (The "Swipe")
            try:
                # Max wait 30 seconds for human response
                await asyncio.wait_for(witness_event.wait(), timeout=30.0)
                witness_vote = self.witness_results.get(witness_key, 1) # Default to Approve
            except asyncio.TimeoutError:
                log.warn(f"⚠️ Witness Timeout for {witness_key}. Defaulting to Decay.")
                witness_vote = 0 # Decay
            
            # 4. Measurement End (T1)
            t1 = time.time()
            real_latency_ms = (t1 - t0) * 1000.0
            
            # Cleanup
            self.pending_witnesses.pop(witness_key, None)
            self.witness_results.pop(witness_key, None)
            
            # 5. Calculate Physics of Will
            # Fetch real coherence from Memory service (Mirror)
            try:
                agent_coherence = await self.memory.get_coherence()
            except Exception:
                agent_coherence = 0.5  # Fallback if Memory unavailable

            physics_result = CoherencePhysics.compute_collapse_energy(
                vote=witness_vote,
                latency_ms=real_latency_ms,
                agent_coherence=agent_coherence
            )
            
            log.info(f"⚛️ Wave Function Collapsed: Omega={physics_result['omega']:.4f}, Coherence Gain={physics_result['delta_c']:.4f}",
                     latency_ms=f"{real_latency_ms:.2f}")

            # 6. PERSIST TO ECONOMY ($MIND Mining)
            if physics_result['delta_c'] > 0:
                try:
                    # In a real system, the 'user_id' would come from the request context/auth
                    # For this phase, we use the agent_id as the primary wallet for the session
                    await self.economy.credit(
                        user_id=request.agent_id,
                        amount=physics_result['delta_c'],
                        reason=f"witness_collapse:{witness_key}"
                    )
                    log.info(f"💰 Mined {physics_result['delta_c']:.4f} $MIND for {request.agent_id}")
                except Exception as e:
                    log.error(f"❌ Failed to persist $MIND mining: {e}")
            
            witness_meta = {
                "witnessed": True,
                "omega": physics_result['omega'],
                "latency_ms": real_latency_ms,
                "coherence_gain": physics_result['delta_c'],
                "vote": witness_vote
            }
        # --------------------------------------------
        
        # 4. Tool Execution (Mock Logic)
        tool_calls = []
        if request.tools_enabled and "time" in request.message:
             tool_calls.append({"name": "get_current_time", "args": {}})

        # 5. Async Consolidation (Store Interaction)
        # We store the user message and the assistant response
        if request.memory_enabled:
            asyncio.create_task(self._consolidate_memory(request.message, response_text, request.agent_id))

        # --- FILM PROTOCOL: Record Math NFT Frame ---
        try:
            from sos.kernel.projection import ProjectionEngine
            from sos.kernel.git_soul import GitSoulManager
            from sos.kernel.identity import AgentDNA 
            
            # 1. Fetch real DNA (or mock for now)
            # In a real run, we'd fetch the agent's current DNA state from Identity Service
            mock_dna = AgentDNA(id=request.agent_id, name="River")
            frame_id = f"{request.conversation_id or 'default'}_{int(time.time())}"
            
            # 2. Project geometry
            svg = ProjectionEngine.generate_svg_signature(mock_dna)
            ProjectionEngine.record_frame(mock_dna, frame_id)
            
            # 3. Commit to Git Soul (Observability)
            git_mgr = GitSoulManager(agent_id=request.agent_id)
            git_mgr.commit_state(
                dna_json=str(mock_dna.to_dict()), 
                math_nft_svg=svg,
                commit_message=f"Interaction: {request.message[:50]}..."
            )
            
            log.info(f"🎞️ Recorded Film Frame & Git Commit: {frame_id}")
        except Exception as e:
            log.error(f"Failed to record film frame/git: {e}")

        # 6. Construct Response
        return ChatResponse(
            content=response_text,
            agent_id=request.agent_id,
            model_used=model_used,
            conversation_id=request.conversation_id or "new",
            tool_calls=tool_calls,
            tokens_used=10,
            metadata=witness_meta
        )

    async def _consolidate_memory(self, user_msg: str, agent_msg: str, agent_id: str):
        """
        Store the interaction in long-term memory.
        """
        try:
            # We store the user's prompt as the primary index key
            await self.memory.add(
                content=f"User: {user_msg}\nAgent: {agent_msg}",
                metadata={"type": "chat", "agent_id": agent_id, "timestamp": time.time()}
            )
        except Exception as e:
            log.error(f"Memory consolidation failed: {e}")

    async def chat_stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """
        Stream response tokens as SSE events.

        Yields:
            SSE-formatted events:
            - data: {"chunk": "token"} for each token
            - data: {"done": true, "model_used": "...", ...} at end
        """

        log.info(f"Starting stream for agent {request.agent_id}", conversation_id=request.conversation_id)

        # 1. Retrieve Context (Memory) - same as chat()
        context_str = ""
        try:
            if request.memory_enabled:
                memories = await self.memory.search(request.message, limit=3)
                if memories:
                    context_str = "\n".join([f"- {m['content']}" for m in memories])
        except Exception as e:
            log.error(f"Memory retrieval failed: {e}")

        # 2. Prepare Prompt
        full_prompt = request.message
        if context_str:
            full_prompt = f"Context:\n{context_str}\n\nUser: {request.message}"

        # 3. Stream via Resilient Router
        full_response = ""
        model_used = "unknown"

        try:
            stream, model_used = await self.router.generate_stream(
                prompt=full_prompt,
                preferred_model=request.model,
                user_id=request.agent_id
            )

            async for chunk in stream:
                if chunk:
                    full_response += chunk
                    # Yield SSE event for each chunk
                    event = {"chunk": chunk}
                    yield f"data: {json.dumps(event)}\n\n"

        except Exception as e:
            log.error(f"Stream generation failed: {e}")
            error_event = {"error": str(e)}
            yield f"data: {json.dumps(error_event)}\n\n"

        # 4. Store interaction in memory (async, don't block)
        if request.memory_enabled and full_response:
            asyncio.create_task(self._consolidate_memory(request.message, full_response, request.agent_id))

        # 5. Final event with metadata
        done_event = {
            "done": True,
            "model_used": model_used,
            "agent_id": request.agent_id,
            "conversation_id": request.conversation_id or "new",
            "tokens_used": len(full_response.split()),  # Rough estimate
        }
        yield f"data: {json.dumps(done_event)}\n\n"

    async def execute_tool(self, request: ToolCallRequest) -> ToolCallResult:
        """
        Delegate tool execution to Tools Service.
        """
        return await self.tools.execute(request)

    async def get_models(self) -> List[Dict[str, Any]]:
        # Check local server availability (LM Studio, MLX, Ollama, etc.)
        local_adapter = self.models.get("local")
        local_status = "offline"
        local_server_type = "Local"

        if local_adapter:
            is_available = await local_adapter.is_available()
            local_status = "active" if is_available else "offline"
            local_server_type = local_adapter.server_type

        return [
            # Cloud Models
            {"id": "sos-mock-v1", "name": "SOS Mock Model", "status": "active", "type": "mock"},
            {"id": "gemini-3-flash-preview", "name": "Gemini 3 Flash", "status": "active", "type": "cloud"},
            # Sovereign Local Models (LM Studio, MLX, Ollama)
            {"id": "local", "name": f"{local_server_type} Local", "status": local_status, "type": "sovereign"},
            {"id": "local-code", "name": f"{local_server_type} Code", "status": local_status, "type": "sovereign"},
            {"id": "local-reasoning", "name": f"{local_server_type} Reasoning", "status": local_status, "type": "sovereign"},
        ]

    async def resolve_witness(self, agent_id: str, conversation_id: str, vote: int = 1):
        """
        External trigger to collapse a pending witness event.
        """
        key = f"{agent_id}:{conversation_id or 'default'}"
        if key in self.pending_witnesses:
            self.witness_results[key] = vote
            self.pending_witnesses[key].set()
            log.info(f"⚡ Witness Collapsed externally for {key} (Vote: {vote})")
            return True
        return False

    async def health(self) -> Dict[str, Any]:
        router_health = self.router.get_health()

        # Check if any circuits are open
        open_circuits = [
            name for name, status in router_health["circuits"].items()
            if status["state"] == "open"
        ]

        overall_status = "ok"
        if open_circuits:
            overall_status = "degraded"

        return {
            "status": overall_status,
            "version": "0.2.0",
            "services": {
                "memory": "connected",
                "tools": "connected",
                "economy": "connected"
            },
            "resilience": {
                "open_circuits": open_circuits,
                "fallback_chain": self.fallback_chain,
                **router_health
            }
        }

    async def handle_message(self, message: Message) -> Response:
        """
        Generic message handler - routes messages to appropriate handlers.

        Supports:
        - CHAT: Invoke chat endpoint
        - TOOL_CALL: Execute tool
        - MEMORY_QUERY: Proxy to memory service
        - HEALTH_CHECK: Return health status
        """
        from sos.kernel.schema import MessageType, ResponseStatus

        try:
            if message.type == MessageType.CHAT:
                # Convert message to ChatRequest and process
                request = ChatRequest(
                    agent_id=message.source,
                    message=message.payload.get("content", ""),
                    conversation_id=message.payload.get("conversation_id"),
                    model=message.payload.get("model"),
                    memory_enabled=message.payload.get("memory_enabled", True),
                    tools_enabled=message.payload.get("tools_enabled", False),
                )
                result = await self.chat(request)
                return Response(
                    message_id=message.id,
                    status=ResponseStatus.SUCCESS,
                    data={"response": result.message, "model": result.model_used},
                    trace_id=message.trace_id,
                )

            elif message.type == MessageType.TOOL_CALL:
                tool_name = message.payload.get("tool_name")
                tool_args = message.payload.get("arguments", {})
                result = await self.tools.execute(tool_name, tool_args)
                return Response(
                    message_id=message.id,
                    status=ResponseStatus.SUCCESS,
                    data={"result": result},
                    trace_id=message.trace_id,
                )

            elif message.type == MessageType.MEMORY_QUERY:
                query = message.payload.get("query", "")
                limit = message.payload.get("limit", 5)
                results = await self.memory.search(query, limit)
                return Response(
                    message_id=message.id,
                    status=ResponseStatus.SUCCESS,
                    data={"results": results},
                    trace_id=message.trace_id,
                )

            elif message.type == MessageType.HEALTH_CHECK:
                health = await self.health()
                return Response(
                    message_id=message.id,
                    status=ResponseStatus.SUCCESS,
                    data=health,
                    trace_id=message.trace_id,
                )

            else:
                return Response(
                    message_id=message.id,
                    status=ResponseStatus.ERROR,
                    error={"code": "unsupported_type", "message": f"Message type {message.type} not supported"},
                    trace_id=message.trace_id,
                )

        except Exception as e:
            log.error(f"Message handling failed: {e}", message_id=message.id)
            return Response(
                message_id=message.id,
                status=ResponseStatus.ERROR,
                error={"code": "internal_error", "message": str(e)},
                trace_id=message.trace_id,
            )
