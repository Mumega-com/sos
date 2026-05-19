"""
Tests for SOS CLI.

Tests doctor, version, and other CLI commands.
"""

import pytest
import sys
from io import StringIO
from unittest.mock import patch, MagicMock

from sos.cli import cmd_doctor, cmd_version, cmd_status, main


class TestCmdVersion:
    """Test version command."""

    def test_version_output(self, capsys):
        """Version command outputs version info."""
        args = MagicMock()
        cmd_version(args)

        captured = capsys.readouterr()
        assert "mumega" in captured.out
        assert "Sovereign Operating System" in captured.out


class TestCmdDoctor:
    """Test doctor command."""

    def test_doctor_checks_python(self, capsys):
        """Doctor checks Python version."""
        args = MagicMock()
        cmd_doctor(args)

        captured = capsys.readouterr()
        assert "Python" in captured.out

    def test_doctor_checks_packages(self, capsys):
        """Doctor checks required packages."""
        args = MagicMock()
        cmd_doctor(args)

        captured = capsys.readouterr()
        # Should check FastAPI and HTTPX
        assert "FastAPI" in captured.out or "fastapi" in captured.out.lower()

    def test_doctor_checks_env_vars(self, capsys):
        """Doctor checks environment variables."""
        args = MagicMock()
        cmd_doctor(args)

        captured = capsys.readouterr()
        assert "GEMINI_API_KEY" in captured.out

    @patch.dict('os.environ', {'GEMINI_API_KEY': 'test-key-12345'})
    def test_doctor_with_key_set(self, capsys):
        """Doctor passes when GEMINI_API_KEY is set."""
        args = MagicMock()
        result = cmd_doctor(args)

        captured = capsys.readouterr()
        assert "[OK]" in captured.out
        # Should show partial key
        assert "test-key" in captured.out


class TestCmdStatus:
    """Test status command."""

    @patch('httpx.get')
    def test_status_checks_services(self, mock_get, capsys):
        """Status checks service health endpoints."""
        # Mock unhealthy response
        mock_get.side_effect = Exception("Connection refused")

        args = MagicMock()
        cmd_status(args)

        captured = capsys.readouterr()
        assert "Engine" in captured.out
        assert "Memory" in captured.out
        # Services should show as not running
        assert "not running" in captured.out or "[--]" in captured.out


class TestMain:
    """Test main CLI entry point."""

    def test_main_no_args_shows_help(self, capsys):
        """Main with no args shows help."""
        with patch.object(sys, 'argv', ['mumega']):
            main()

        captured = capsys.readouterr()
        # Should show usage or help
        assert "mumega" in captured.out.lower() or "usage" in captured.out.lower()

    def test_main_version_flag(self, capsys):
        """Main with --version shows version."""
        with patch.object(sys, 'argv', ['mumega', '--version']):
            main()

        captured = capsys.readouterr()
        assert "mumega" in captured.out

    def test_main_doctor_command(self, capsys):
        """Main with doctor command runs doctor."""
        with patch.object(sys, 'argv', ['mumega', 'doctor']):
            main()

        captured = capsys.readouterr()
        assert "Doctor" in captured.out or "Python" in captured.out
