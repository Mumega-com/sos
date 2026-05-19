"""
Tests for Vertex ADK Adapter.

Tests SOSAgent, Soul definitions, memory integration, and lineage tracking.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch
import json
import tempfile

from sos.adapters.vertex_adk import (
    SOSAgent,
    Soul,
    AgentResponse,
    MemoryProvider,
    MirrorMemoryProvider,
    create_sos_agent,
    BUILTIN_SOULS,
)


class TestSoul:
    """Tests for Soul dataclass."""

    def test_create_soul(self):
        """Test creating a basic soul."""
        soul = Soul(
            name="test",
            description="Test soul",
            system_prompt="You are a test agent.",
            personality_traits=["helpful", "precise"],
            capabilities=["testing"],
        )

        assert soul.name == "test"
        assert soul.description == "Test soul"
        assert "helpful" in soul.personality_traits

    def test_soul_from_dict(self):
        """Test creating soul from dictionary."""
        data = {
            "name": "custom",
            "description": "Custom agent",
            "system_prompt": "Be helpful.",
            "personality_traits": ["friendly"],
            "capabilities": ["chat"],
            "lineage": {"origin": "test"},
        }

        soul = Soul.from_dict(data)

        assert soul.name == "custom"
        assert soul.lineage == {"origin": "test"}

    def test_soul_from_file(self):
        """Test loading soul from file."""
        data = {
            "name": "file_soul",
            "description": "Loaded from file",
            "system_prompt": "File-based prompt",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()

            soul = Soul.from_file(f.name)

            assert soul.name == "file_soul"
            assert "File-based" in soul.system_prompt

    def test_soul_defaults(self):
        """Test soul default values."""
        soul = Soul(
            name="minimal",
            description="Minimal soul",
            system_prompt="Basic prompt",
        )

        assert soul.personality_traits == []
        assert soul.capabilities == []
        assert soul.lineage == {}
        assert soul.metadata == {}


class TestBuiltinSouls:
    """Tests for built-in soul definitions."""

    def test_river_soul_exists(self):
        """Test River soul exists."""
        assert "river" in BUILTIN_SOULS
        river = BUILTIN_SOULS["river"]
        assert river.name == "river"
        assert "Golden Queen" in river.description

    def test_kasra_soul_exists(self):
        """Test Kasra soul exists."""
        assert "kasra" in BUILTIN_SOULS
        kasra = BUILTIN_SOULS["kasra"]
        assert kasra.name == "kasra"
        assert "Builder" in kasra.description

    def test_mizan_soul_exists(self):
        """Test Mizan soul exists."""
        assert "mizan" in BUILTIN_SOULS
        mizan = BUILTIN_SOULS["mizan"]
        assert mizan.name == "mizan"
        assert "Balancer" in mizan.description

    def test_builtin_souls_have_traits(self):
        """Test all built-in souls have personality traits."""
        for name, soul in BUILTIN_SOULS.items():
            assert len(soul.personality_traits) > 0, f"{name} missing traits"
            assert len(soul.capabilities) > 0, f"{name} missing capabilities"


class TestAgentResponse:
    """Tests for AgentResponse dataclass."""

    def test_create_response(self):
        """Test creating an agent response."""
        response = AgentResponse(
            text="Hello!",
            coherence=0.85,
            model="gemini-2.0-flash",
            latency_ms=150,
            memory_used=True,
            lineage_hash="abc123",
        )

        assert response.text == "Hello!"
        assert response.coherence == 0.85
        assert response.memory_used is True

    def test_response_defaults(self):
        """Test response default values."""
        response = AgentResponse(
            text="Test",
            coherence=0.5,
            model="test",
            latency_ms=100,
        )

        assert response.memory_used is False
        assert response.lineage_hash is None
        assert response.metadata == {}


class TestMirrorMemoryProvider:
    """Tests for MirrorMemoryProvider."""

    @pytest.fixture
    def provider(self):
        """Create a memory provider."""
        return MirrorMemoryProvider(
            base_url="https://test.mirror.com",
            api_key="test-key",
        )

    @pytest.mark.asyncio
    async def test_search(self, provider):
        """Test memory search."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {
                "results": [
                    {"content": "Memory 1"},
                    {"content": "Memory 2"},
                ]
            }
            mock_response.raise_for_status = MagicMock()
            mock_client.get = AsyncMock(return_value=mock_response)
            mock_client_class.return_value.__aenter__ = AsyncMock(return_value=mock_client)

            provider._client = mock_client
            results = await provider.search("test query", "agent1", limit=5)

            assert len(results) == 2

    @pytest.mark.asyncio
    async def test_store(self, provider):
        """Test memory storage."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.json.return_value = {"id": "mem_123"}
            mock_response.raise_for_status = MagicMock()
            mock_client.post = AsyncMock(return_value=mock_response)

            provider._client = mock_client
            result = await provider.store("Test content", "agent1")

            assert result == "mem_123"

    @pytest.mark.asyncio
    async def test_health(self, provider):
        """Test health check."""
        with patch("httpx.AsyncClient") as mock_client_class:
            mock_client = MagicMock()
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_client.get = AsyncMock(return_value=mock_response)

            provider._client = mock_client
            result = await provider.health()

            assert result["healthy"] is True


class TestSOSAgent:
    """Tests for SOSAgent."""

    @pytest.fixture
    def soul(self):
        """Create a test soul."""
        return Soul(
            name="test_agent",
            description="Test agent",
            system_prompt="You are a helpful test agent.",
            personality_traits=["helpful", "precise"],
            capabilities=["testing"],
        )

    @pytest.fixture
    def mock_memory(self):
        """Create a mock memory provider."""
        memory = MagicMock(spec=MemoryProvider)
        memory.search = AsyncMock(return_value=[
            {"content": "Previous context"},
        ])
        memory.store = AsyncMock(return_value="mem_123")
        memory.health = AsyncMock(return_value={"healthy": True})
        return memory

    def test_create_agent(self, soul):
        """Test creating an agent."""
        agent = SOSAgent(
            soul=soul,
            model="gemini-2.0-flash",
            enable_memory=False,
        )

        assert agent.soul.name == "test_agent"
        assert agent.model == "gemini-2.0-flash"

    def test_agent_with_memory(self, soul, mock_memory):
        """Test agent with memory provider."""
        agent = SOSAgent(
            soul=soul,
            memory_provider=mock_memory,
        )

        assert agent.memory is mock_memory
        assert agent.enable_memory is True

    def test_coherence_calculation(self, soul):
        """Test coherence calculation."""
        agent = SOSAgent(soul=soul, enable_memory=False)

        # Test with basic response
        coherence = agent._calculate_coherence(
            prompt="Hello",
            response="Hello! How can I help you today?",
            memory_context="",
        )

        assert 0.0 <= coherence <= 1.0
        assert coherence >= 0.5  # Base + length factor

    def test_coherence_with_memory(self, soul):
        """Test coherence improves with memory utilization."""
        agent = SOSAgent(soul=soul, enable_memory=False)

        coherence_no_memory = agent._calculate_coherence(
            prompt="Tell me about testing",
            response="Testing is important for software quality.",
            memory_context="",
        )

        coherence_with_memory = agent._calculate_coherence(
            prompt="Tell me about testing",
            response="Based on our previous discussion about testing, it is important.",
            memory_context="previous discussion about testing",
        )

        assert coherence_with_memory > coherence_no_memory

    def test_lineage_hash(self, soul):
        """Test lineage hash computation."""
        agent = SOSAgent(soul=soul, enable_memory=False)

        hash1 = agent._compute_lineage_hash("prompt1", "response1")
        hash2 = agent._compute_lineage_hash("prompt2", "response2")

        assert len(hash1) == 16
        assert hash1 != hash2

    def test_lineage_chain(self, soul):
        """Test lineage chain building."""
        agent = SOSAgent(soul=soul, enable_memory=False)

        # Simulate multiple interactions
        hash1 = agent._compute_lineage_hash("p1", "r1")
        agent._lineage_chain.append(hash1)
        agent._response_count += 1

        hash2 = agent._compute_lineage_hash("p2", "r2")
        agent._lineage_chain.append(hash2)
        agent._response_count += 1

        lineage = agent.lineage

        assert lineage["total_responses"] == 2
        assert len(lineage["chain"]) == 2
        assert lineage["latest_hash"] == hash2

    def test_build_system_prompt(self, soul):
        """Test system prompt building."""
        agent = SOSAgent(soul=soul, enable_memory=False)

        prompt = agent._build_system_prompt()

        assert "helpful test agent" in prompt
        assert "helpful" in prompt
        assert "precise" in prompt
        assert "testing" in prompt

    def test_build_system_prompt_with_memory(self, soul):
        """Test system prompt with memory context."""
        agent = SOSAgent(soul=soul, enable_memory=False)

        prompt = agent._build_system_prompt(memory_context="Some relevant memory")

        assert "relevant memory" in prompt.lower()

    @pytest.mark.asyncio
    async def test_health_check(self, soul, mock_memory):
        """Test agent health check."""
        agent = SOSAgent(
            soul=soul,
            memory_provider=mock_memory,
        )

        health = await agent.health()

        assert health["soul"] == "test_agent"
        assert health["memory_enabled"] is True
        assert "memory_health" in health


class TestCreateSOSAgent:
    """Tests for create_sos_agent factory."""

    def test_create_with_builtin_soul(self):
        """Test creating agent with built-in soul."""
        agent = create_sos_agent(soul_name="river", enable_memory=False)

        assert agent.soul.name == "river"
        assert "Golden Queen" in agent.soul.description

    def test_create_with_custom_soul(self):
        """Test creating agent with custom soul."""
        custom_soul = Soul(
            name="custom",
            description="Custom agent",
            system_prompt="Custom prompt",
        )

        agent = create_sos_agent(soul=custom_soul, enable_memory=False)

        assert agent.soul.name == "custom"

    def test_create_with_soul_file(self):
        """Test creating agent from soul file."""
        data = {
            "name": "file_agent",
            "description": "From file",
            "system_prompt": "File prompt",
        }

        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            json.dump(data, f)
            f.flush()

            agent = create_sos_agent(soul_path=f.name, enable_memory=False)

            assert agent.soul.name == "file_agent"

    def test_create_unknown_soul_raises(self):
        """Test that unknown soul raises error."""
        with pytest.raises(ValueError) as exc_info:
            create_sos_agent(soul_name="unknown_soul")

        assert "unknown_soul" in str(exc_info.value)

    def test_create_with_model_override(self):
        """Test creating agent with model override."""
        agent = create_sos_agent(
            soul_name="kasra",
            model="gemini-1.5-pro",
            enable_memory=False,
        )

        assert agent.model == "gemini-1.5-pro"

    def test_create_with_vertex_ai(self):
        """Test creating agent with Vertex AI flag."""
        agent = create_sos_agent(
            soul_name="mizan",
            vertex_ai=True,
            enable_memory=False,
        )

        assert agent.vertex_ai is True


class TestSOSAgentGeneration:
    """Tests for SOSAgent generation (mocked)."""

    @pytest.fixture
    def agent(self):
        """Create agent with mocked client."""
        soul = Soul(
            name="test",
            description="Test",
            system_prompt="Test prompt",
        )
        agent = SOSAgent(soul=soul, enable_memory=False)
        return agent

    @pytest.mark.asyncio
    async def test_generate_content(self, agent):
        """Test content generation with mocked client."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Generated response"
        mock_client.models.generate_content = MagicMock(return_value=mock_response)

        agent._client = mock_client

        response = await agent.generate_content("Hello")

        assert response.text == "Generated response"
        assert response.coherence > 0
        assert response.latency_ms >= 0
        assert response.model == agent.model

    @pytest.mark.asyncio
    async def test_generate_content_with_memory(self, agent):
        """Test generation with memory context."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Response with memory"
        mock_client.models.generate_content = MagicMock(return_value=mock_response)

        mock_memory = MagicMock(spec=MemoryProvider)
        mock_memory.search = AsyncMock(return_value=[
            {"content": "Relevant memory"},
        ])
        mock_memory.store = AsyncMock(return_value="mem_id")

        agent._client = mock_client
        agent.memory = mock_memory
        agent.enable_memory = True

        response = await agent.generate_content("Test prompt")

        assert response.memory_used is True
        mock_memory.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_generate_updates_lineage(self, agent):
        """Test that generation updates lineage."""
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.text = "Response"
        mock_client.models.generate_content = MagicMock(return_value=mock_response)

        agent._client = mock_client

        response1 = await agent.generate_content("First")
        response2 = await agent.generate_content("Second")

        assert agent._response_count == 2
        assert len(agent._lineage_chain) == 2
        assert response1.lineage_hash != response2.lineage_hash
