"""
K5 Classifier unit tests.

Gate: K5 (Sprint 001 carry → Sprint 003)

Tests verify:
  - Empty transcript returns error result (no crash)
  - Well-formed model JSON → correct ClassificationResult fields
  - Markdown-fenced JSON is stripped and parsed
  - Malformed JSON → result with error, no crash
  - Low confidence (<0.6) triggers escalation to Flash model
  - High confidence → no escalation (flash-lite only)
  - Model failure → ClassificationResult with error set
  - classify_transcript selects Vertex adapter when GOOGLE_CLOUD_PROJECT set
  - classify_transcript falls back to GeminiAdapter when project not set
  - run_log populated: one record per pass (pass 1 only on high conf)
  - run_log has two records when escalation fires
  - ClassifierRunRecord fields are correct (billing_path, latency_ms, etc.)
  - FLASH_LITE_MODEL and FLASH_MODEL constants are correct

All adapter calls are mocked — no API keys or ADC required.
"""
from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from sos.services.intake.classifier import (
    ESCALATION_THRESHOLD,
    FLASH_LITE_MODEL,
    FLASH_MODEL,
    ClassificationResult,
    ClassifierRunRecord,
    Commitment,
    Opportunity,
    Participant,
    RelSignal,
    _build_result,
    _parse_model_output,
    classify_transcript,
)

# ── Fixtures ───────────────────────────────────────────────────────────────────

_SAMPLE_RAW = {
    'source_type': 'meeting',
    'confidence': 0.85,
    'participants': [
        {'name': 'Hadi Servathadi', 'role': 'host', 'email_hint': 'hadi@digid.ca'},
        {'name': "Ron O'Neil", 'role': 'client', 'email_hint': None},
    ],
    'decisions': ['Move forward with AI Security proposal'],
    'commitments': [
        {
            'owner': 'Hadi Servathadi',
            'action': 'Send term sheet by Friday',
            'due_date': '2026-04-26',
            'context': "I'll send that over by end of week",
        }
    ],
    'opportunities': [
        {
            'signal': 'lead',
            'description': 'Ron has 10 Century 21 offices needing AI integration',
            'involved': ["Ron O'Neil"],
            'value_hint': '$50K+',
        }
    ],
    'relationship_signals': [
        {
            'subject': "Ron O'Neil",
            'sentiment': 'warm',
            'evidence': "This is exactly what we've been looking for",
        }
    ],
}

_SAMPLE_TRANSCRIPT = """
Hadi: Thanks for joining Ron. Let's go over the AI Security proposal.
Ron: This is exactly what we've been looking for.
Hadi: Great. I'll send that over by end of week.
Ron: Perfect. We have 10 Century 21 offices that could use this.
""".strip()


def _make_exec_result(raw_dict: dict, success: bool = True, error: str | None = None) -> MagicMock:
    """Return a mocked ExecutionResult."""
    result = MagicMock()
    result.success = success
    result.text = json.dumps(raw_dict)
    result.error = error
    result.usage = MagicMock()
    result.usage.cost_cents = 2
    result.usage.input_tokens = 500
    result.usage.output_tokens = 200
    return result


def _make_failed_result(error: str) -> MagicMock:
    result = MagicMock()
    result.success = False
    result.error = error
    result.text = ''
    result.usage = MagicMock()
    result.usage.cost_cents = 0
    result.usage.input_tokens = 0
    result.usage.output_tokens = 0
    return result


# ── _parse_model_output ────────────────────────────────────────────────────────


class TestParseModelOutput:
    def test_valid_json(self) -> None:
        data = {'confidence': 0.9, 'participants': []}
        parsed, err = _parse_model_output(json.dumps(data))
        assert err is None
        assert parsed['confidence'] == 0.9

    def test_markdown_fenced_json(self) -> None:
        raw = '```json\n{"confidence": 0.7}\n```'
        parsed, err = _parse_model_output(raw)
        assert err is None
        assert parsed['confidence'] == 0.7

    def test_markdown_fence_no_lang(self) -> None:
        raw = '```\n{"confidence": 0.5}\n```'
        parsed, err = _parse_model_output(raw)
        assert err is None
        assert parsed['confidence'] == 0.5

    def test_malformed_json(self) -> None:
        parsed, err = _parse_model_output('NOT JSON AT ALL {broken')
        assert err is not None
        assert 'JSON parse error' in err
        assert parsed == {}

    def test_empty_string(self) -> None:
        parsed, err = _parse_model_output('')
        assert err is not None
        assert parsed == {}


# ── _build_result ──────────────────────────────────────────────────────────────


class TestBuildResult:
    def test_participants_populated(self) -> None:
        result = _build_result(_SAMPLE_RAW, model_used=FLASH_LITE_MODEL)
        assert len(result.participants) == 2
        assert result.participants[0].name == 'Hadi Servathadi'
        assert result.participants[0].email_hint == 'hadi@digid.ca'
        assert result.participants[0].resolved is False

    def test_decisions_populated(self) -> None:
        result = _build_result(_SAMPLE_RAW, model_used=FLASH_LITE_MODEL)
        assert 'Move forward with AI Security proposal' in result.decisions

    def test_commitments_populated(self) -> None:
        result = _build_result(_SAMPLE_RAW, model_used=FLASH_LITE_MODEL)
        assert len(result.commitments) == 1
        c = result.commitments[0]
        assert c.owner == 'Hadi Servathadi'
        assert c.due_date == '2026-04-26'

    def test_opportunities_populated(self) -> None:
        result = _build_result(_SAMPLE_RAW, model_used=FLASH_LITE_MODEL)
        assert len(result.opportunities) == 1
        o = result.opportunities[0]
        assert o.signal == 'lead'
        assert "Ron O'Neil" in o.involved

    def test_relationship_signals_populated(self) -> None:
        result = _build_result(_SAMPLE_RAW, model_used=FLASH_LITE_MODEL)
        assert len(result.relationship_signals) == 1
        r = result.relationship_signals[0]
        assert r.sentiment == 'warm'

    def test_confidence_and_model(self) -> None:
        result = _build_result(_SAMPLE_RAW, model_used=FLASH_LITE_MODEL)
        assert result.confidence == 0.85
        assert result.model_used == FLASH_LITE_MODEL
        assert result.escalated is False

    def test_escalated_flag_set(self) -> None:
        result = _build_result(_SAMPLE_RAW, model_used=FLASH_MODEL, escalated=True)
        assert result.escalated is True
        assert result.model_used == FLASH_MODEL

    def test_skips_participants_without_name(self) -> None:
        raw = {**_SAMPLE_RAW, 'participants': [{'name': '', 'role': 'unknown'}]}
        result = _build_result(raw, model_used=FLASH_LITE_MODEL)
        assert len(result.participants) == 0

    def test_skips_commitments_without_owner_or_action(self) -> None:
        raw = {**_SAMPLE_RAW, 'commitments': [{'owner': 'Bob', 'action': ''}]}
        result = _build_result(raw, model_used=FLASH_LITE_MODEL)
        assert len(result.commitments) == 0

    def test_empty_raw_returns_empty_result(self) -> None:
        result = _build_result({}, model_used=FLASH_LITE_MODEL)
        assert result.participants == []
        assert result.decisions == []
        assert result.confidence == 0.0


# ── classify_transcript ────────────────────────────────────────────────────────


class TestClassifyTranscript:
    @pytest.mark.asyncio
    async def test_empty_transcript_returns_error(self) -> None:
        result = await classify_transcript('')
        assert result.error is not None
        assert 'Empty' in result.error

    @pytest.mark.asyncio
    async def test_whitespace_only_returns_error(self) -> None:
        result = await classify_transcript('   \n  ')
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_successful_high_confidence_no_escalation(self) -> None:
        """High confidence → flash-lite only, no escalation call."""
        exec_result = _make_exec_result(_SAMPLE_RAW)

        with patch('sos.services.intake.classifier._make_adapter') as mock_make:
            adapter = MagicMock()
            adapter.execute = AsyncMock(return_value=exec_result)
            mock_make.return_value = (adapter, 'vertex-adc')

            result = await classify_transcript(_SAMPLE_TRANSCRIPT)

        assert result.error is None
        assert result.confidence == 0.85
        assert result.model_used == FLASH_LITE_MODEL
        assert result.escalated is False
        assert len(result.participants) == 2
        assert adapter.execute.call_count == 1

    @pytest.mark.asyncio
    async def test_run_log_populated_on_success(self) -> None:
        """run_log has one record when high confidence, no escalation."""
        exec_result = _make_exec_result(_SAMPLE_RAW)

        with patch('sos.services.intake.classifier._make_adapter') as mock_make:
            adapter = MagicMock()
            adapter.execute = AsyncMock(return_value=exec_result)
            mock_make.return_value = (adapter, 'vertex-adc')

            result = await classify_transcript(_SAMPLE_TRANSCRIPT)

        assert len(result.run_log) == 1
        rec = result.run_log[0]
        assert rec.pass_number == 1
        assert rec.model == FLASH_LITE_MODEL
        assert rec.billing_path == 'vertex-adc'
        assert rec.confidence == 0.85
        assert rec.escalated is False
        assert rec.parse_error is None
        assert rec.latency_ms >= 0
        assert rec.input_tokens == 500
        assert rec.output_tokens == 200
        assert rec.cost_cents == 2

    @pytest.mark.asyncio
    async def test_low_confidence_triggers_escalation(self) -> None:
        """Confidence < ESCALATION_THRESHOLD → second call to Flash."""
        low_conf_raw = {**_SAMPLE_RAW, 'confidence': 0.4}
        high_conf_raw = {**_SAMPLE_RAW, 'confidence': 0.88}

        first_result = _make_exec_result(low_conf_raw)
        second_result = _make_exec_result(high_conf_raw)

        with patch('sos.services.intake.classifier._make_adapter') as mock_make:
            adapter = MagicMock()
            adapter.execute = AsyncMock(side_effect=[first_result, second_result])
            mock_make.return_value = (adapter, 'vertex-adc')

            result = await classify_transcript(_SAMPLE_TRANSCRIPT)

        assert result.escalated is True
        assert result.model_used == FLASH_MODEL
        assert result.confidence == 0.88
        assert adapter.execute.call_count == 2

        # Second call used Flash
        second_call_ctx = adapter.execute.call_args_list[1][0][0]
        assert second_call_ctx.model == FLASH_MODEL

    @pytest.mark.asyncio
    async def test_run_log_has_two_records_on_escalation(self) -> None:
        """run_log has two records when escalation fires."""
        low_conf_raw = {**_SAMPLE_RAW, 'confidence': 0.4}
        high_conf_raw = {**_SAMPLE_RAW, 'confidence': 0.9}

        with patch('sos.services.intake.classifier._make_adapter') as mock_make:
            adapter = MagicMock()
            adapter.execute = AsyncMock(side_effect=[
                _make_exec_result(low_conf_raw),
                _make_exec_result(high_conf_raw),
            ])
            mock_make.return_value = (adapter, 'gemini-api')

            result = await classify_transcript(_SAMPLE_TRANSCRIPT)

        assert len(result.run_log) == 2
        assert result.run_log[0].pass_number == 1
        assert result.run_log[0].escalated is False
        assert result.run_log[0].model == FLASH_LITE_MODEL
        assert result.run_log[1].pass_number == 2
        assert result.run_log[1].escalated is True
        assert result.run_log[1].model == FLASH_MODEL
        assert result.run_log[1].billing_path == 'gemini-api'

    @pytest.mark.asyncio
    async def test_adapter_failure_returns_error_result(self) -> None:
        failed = _make_failed_result('RESOURCE_EXHAUSTED: quota exceeded')

        with patch('sos.services.intake.classifier._make_adapter') as mock_make:
            adapter = MagicMock()
            adapter.execute = AsyncMock(return_value=failed)
            mock_make.return_value = (adapter, 'vertex-adc')

            result = await classify_transcript(_SAMPLE_TRANSCRIPT)

        assert result.error is not None
        assert 'quota' in result.error
        assert len(result.run_log) == 1
        assert 'adapter_error' in result.run_log[0].parse_error

    @pytest.mark.asyncio
    async def test_malformed_json_response_returns_error(self) -> None:
        bad_result = MagicMock()
        bad_result.success = True
        bad_result.text = 'Sure! Here is the analysis: not json at all'
        bad_result.error = None
        bad_result.usage = MagicMock(cost_cents=0, input_tokens=0, output_tokens=0)

        with patch('sos.services.intake.classifier._make_adapter') as mock_make:
            adapter = MagicMock()
            adapter.execute = AsyncMock(return_value=bad_result)
            mock_make.return_value = (adapter, 'vertex-adc')

            result = await classify_transcript(_SAMPLE_TRANSCRIPT)

        assert result.error is not None
        assert 'JSON' in result.error
        assert len(result.run_log) == 1
        assert result.run_log[0].parse_error is not None

    @pytest.mark.asyncio
    async def test_escalation_failure_returns_lite_result(self) -> None:
        """If escalation call fails, keep the original flash-lite result."""
        low_conf_raw = {**_SAMPLE_RAW, 'confidence': 0.3}

        with patch('sos.services.intake.classifier._make_adapter') as mock_make:
            adapter = MagicMock()
            adapter.execute = AsyncMock(side_effect=[
                _make_exec_result(low_conf_raw),
                _make_failed_result('timeout'),
            ])
            mock_make.return_value = (adapter, 'vertex-adc')

            result = await classify_transcript(_SAMPLE_TRANSCRIPT)

        # Falls back to lite result
        assert result.model_used == FLASH_LITE_MODEL
        assert result.confidence == 0.3
        assert result.escalated is False
        # Both passes still logged
        assert len(result.run_log) == 2
        assert result.run_log[1].parse_error is not None

    @pytest.mark.asyncio
    async def test_vertex_adapter_selected_when_project_set(self) -> None:
        """GOOGLE_CLOUD_PROJECT set → billing_path='vertex-adc' in run_log."""
        exec_result = _make_exec_result(_SAMPLE_RAW)

        # Patch at the source module where VertexGeminiAdapter is defined
        with patch.dict('os.environ', {'GOOGLE_CLOUD_PROJECT': 'mumega-com'}):
            with patch('sos.adapters.vertex_gemini_adapter.VertexGeminiAdapter') as MockVertex:
                mock_adapter = MagicMock()
                mock_adapter.execute = AsyncMock(return_value=exec_result)
                MockVertex.return_value = mock_adapter

                result = await classify_transcript(_SAMPLE_TRANSCRIPT)

        assert result.run_log[0].billing_path == 'vertex-adc'

    @pytest.mark.asyncio
    async def test_gemini_adapter_selected_when_no_project(self) -> None:
        """No GOOGLE_CLOUD_PROJECT → billing_path='gemini-api' in run_log."""
        exec_result = _make_exec_result(_SAMPLE_RAW)

        # Patch at the source module where GeminiAdapter is defined
        env = {k: v for k, v in __import__('os').environ.items() if k != 'GOOGLE_CLOUD_PROJECT'}
        with patch.dict('os.environ', env, clear=True):
            with patch('sos.adapters.gemini_adapter.GeminiAdapter') as MockGemini:
                mock_adapter = MagicMock()
                mock_adapter.execute = AsyncMock(return_value=exec_result)
                MockGemini.return_value = mock_adapter

                result = await classify_transcript(_SAMPLE_TRANSCRIPT, api_key='test-key')

        assert result.run_log[0].billing_path == 'gemini-api'


# ── Constants ──────────────────────────────────────────────────────────────────


class TestConstants:
    def test_flash_lite_model_name(self) -> None:
        assert FLASH_LITE_MODEL == 'gemini-2.5-flash-lite'

    def test_flash_model_name(self) -> None:
        assert FLASH_MODEL == 'gemini-2.5-flash'

    def test_escalation_threshold_range(self) -> None:
        assert 0.0 < ESCALATION_THRESHOLD < 1.0


# ── ClassifierRunRecord ────────────────────────────────────────────────────────


class TestClassifierRunRecord:
    def test_fields_present(self) -> None:
        rec = ClassifierRunRecord(
            pass_number=1,
            model=FLASH_LITE_MODEL,
            billing_path='vertex-adc',
            confidence=0.85,
            escalated=False,
            latency_ms=312,
            input_tokens=500,
            output_tokens=200,
            cost_cents=2,
            parse_error=None,
        )
        assert rec.billing_path == 'vertex-adc'
        assert rec.latency_ms == 312
        assert rec.parse_error is None

    def test_escalated_pass_fields(self) -> None:
        rec = ClassifierRunRecord(
            pass_number=2,
            model=FLASH_MODEL,
            billing_path='gemini-api',
            confidence=0.92,
            escalated=True,
            latency_ms=800,
            input_tokens=600,
            output_tokens=250,
            cost_cents=5,
            parse_error=None,
        )
        assert rec.escalated is True
        assert rec.pass_number == 2
        assert rec.model == FLASH_MODEL
