"""
SOS Configuration Validation

Validates environment and configuration for SOS services.
"""

import os
from dataclasses import dataclass, field
from typing import List, Optional
from enum import Enum


class ValidationSeverity(str, Enum):
    """Severity levels for validation issues."""
    ERROR = "error"      # Blocks startup
    WARNING = "warning"  # Should fix but can run
    INFO = "info"        # Informational


@dataclass
class ValidationIssue:
    """A single validation issue."""
    severity: ValidationSeverity
    message: str
    key: str
    suggestion: Optional[str] = None


@dataclass
class ValidationResult:
    """Result of configuration validation."""
    valid: bool
    issues: List[ValidationIssue] = field(default_factory=list)

    def add_error(self, key: str, message: str, suggestion: str = None):
        self.issues.append(ValidationIssue(
            severity=ValidationSeverity.ERROR,
            key=key,
            message=message,
            suggestion=suggestion
        ))
        self.valid = False

    def add_warning(self, key: str, message: str, suggestion: str = None):
        self.issues.append(ValidationIssue(
            severity=ValidationSeverity.WARNING,
            key=key,
            message=message,
            suggestion=suggestion
        ))

    def add_info(self, key: str, message: str):
        self.issues.append(ValidationIssue(
            severity=ValidationSeverity.INFO,
            key=key,
            message=message
        ))

    @property
    def errors(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.ERROR]

    @property
    def warnings(self) -> List[ValidationIssue]:
        return [i for i in self.issues if i.severity == ValidationSeverity.WARNING]


def validate_config() -> ValidationResult:
    """
    Validate SOS configuration and environment.

    Checks:
    - Required API keys
    - Service URLs
    - Feature flags
    - File permissions

    Returns:
        ValidationResult with any issues found
    """
    result = ValidationResult(valid=True)

    # Check for at least one model provider
    model_keys = [
        ("GEMINI_API_KEY", "Google Gemini"),
        ("ANTHROPIC_API_KEY", "Anthropic Claude"),
        ("OPENAI_API_KEY", "OpenAI"),
        ("XAI_API_KEY", "xAI Grok"),
    ]

    has_model_key = False
    for key, name in model_keys:
        if os.environ.get(key):
            has_model_key = True
            result.add_info(key, f"{name} API key configured")
            break

    if not has_model_key:
        result.add_warning(
            "MODEL_API_KEY",
            "No model provider API key found",
            "Set GEMINI_API_KEY or another provider key in .env"
        )

    # Check gateway URL
    gateway_url = os.environ.get("GATEWAY_URL", "")
    if gateway_url:
        result.add_info("GATEWAY_URL", f"Gateway: {gateway_url}")
    else:
        result.add_info("GATEWAY_URL", "Using default gateway")

    # Check service ports for conflicts
    ports = {
        "SOS_ENGINE_PORT": os.environ.get("SOS_ENGINE_PORT", "6060"),
        "SOS_MEMORY_PORT": os.environ.get("SOS_MEMORY_PORT", "6061"),
        "SOS_ECONOMY_PORT": os.environ.get("SOS_ECONOMY_PORT", "6062"),
        "SOS_TOOLS_PORT": os.environ.get("SOS_TOOLS_PORT", "6063"),
    }

    seen_ports = {}
    for key, port in ports.items():
        if port in seen_ports:
            result.add_error(
                key,
                f"Port {port} conflicts with {seen_ports[port]}",
                f"Change {key} to a different port"
            )
        seen_ports[port] = key

    # Check autonomy settings
    pulse = os.environ.get("SOS_PULSE_INTERVAL")
    if pulse:
        try:
            pulse_val = float(pulse)
            if pulse_val < 60:
                result.add_warning(
                    "SOS_PULSE_INTERVAL",
                    f"Pulse interval {pulse_val}s is very short",
                    "Consider 300s (5 min) or longer"
                )
        except ValueError:
            result.add_error(
                "SOS_PULSE_INTERVAL",
                f"Invalid pulse interval: {pulse}",
                "Must be a number (seconds)"
            )

    # Check log level
    log_level = os.environ.get("SOS_LOG_LEVEL", "INFO").upper()
    valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
    if log_level not in valid_levels:
        result.add_warning(
            "SOS_LOG_LEVEL",
            f"Unknown log level: {log_level}",
            f"Use one of: {', '.join(valid_levels)}"
        )

    # Check for .env file
    from pathlib import Path
    env_file = Path.cwd() / ".env"
    if not env_file.exists():
        env_example = Path.cwd() / ".env.example"
        if env_example.exists():
            result.add_warning(
                ".env",
                "No .env file found",
                "Copy .env.example to .env and configure"
            )

    return result


def validate_for_startup() -> bool:
    """
    Validate configuration and return True if OK to start.

    Prints validation results to console.
    """
    result = validate_config()

    if result.errors:
        print("Configuration errors (must fix):")
        for issue in result.errors:
            print(f"  [ERROR] {issue.key}: {issue.message}")
            if issue.suggestion:
                print(f"          -> {issue.suggestion}")
        return False

    if result.warnings:
        print("Configuration warnings:")
        for issue in result.warnings:
            print(f"  [WARN] {issue.key}: {issue.message}")
            if issue.suggestion:
                print(f"         -> {issue.suggestion}")

    return True
