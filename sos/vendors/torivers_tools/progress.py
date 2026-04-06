"""
Progress reporting for automations.

This module provides the ProgressReporter interface and implementations
for sending progress updates to users during automation execution.

In production: forwarded through the platform action contract via runtime
In local sandbox: prints to console or collects for testing
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Callable


class LogLevel(str, Enum):
    """Log levels for progress messages."""

    INFO = "info"
    ACTION = "action"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"


@dataclass
class ProgressEntry:
    """A single progress log entry."""

    level: LogLevel
    title: str
    description: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary representation."""
        return {
            "level": self.level.value,
            "title": self.title,
            "description": self.description,
            "timestamp": self.timestamp.isoformat(),
            "metadata": self.metadata,
        }


class ProgressReporterBase(ABC):
    """
    Abstract base class for progress reporters.

    This interface defines the contract for progress reporting.
    Different implementations handle production vs testing scenarios.
    """

    @abstractmethod
    def log_info(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an informational message."""
        pass

    @abstractmethod
    def log_action(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an action being performed."""
        pass

    @abstractmethod
    def log_success(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log a successful step completion."""
        pass

    @abstractmethod
    def log_warning(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log a warning (non-fatal issue)."""
        pass

    @abstractmethod
    def log_error(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an error."""
        pass


class ProgressReporter(ProgressReporterBase):
    """
    Reports progress updates during automation execution.

    This class handles buffering and sending progress updates
    to the ToRivers platform. It supports different log levels
    and can be configured with custom handlers for testing.

    Example (for testing):
        entries = []
        reporter = ProgressReporter(callback=entries.append)

        reporter.log_action("Starting", "Beginning process")
        reporter.log_success("Done", "Process completed")

        assert len(entries) == 2
    """

    def __init__(
        self,
        execution_id: str = "",
        callback: Callable[[ProgressEntry], None] | None = None,
    ) -> None:
        """
        Initialize progress reporter.

        Args:
            execution_id: The execution ID for this session
            callback: Optional callback for progress entries (for testing)
        """
        self._execution_id = execution_id
        self._callback = callback
        self._entries: list[ProgressEntry] = []

    @property
    def execution_id(self) -> str:
        """Get the execution ID."""
        return self._execution_id

    @property
    def entries(self) -> list[ProgressEntry]:
        """Get all logged entries."""
        return list(self._entries)

    def log_info(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Log an informational message.

        Use this for general status updates.

        Args:
            title: Short title for the info
            description: Optional detailed description
            metadata: Optional metadata for debugging
        """
        self._log(LogLevel.INFO, title, description, metadata)

    def log_action(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Log a progress action.

        Use this for general progress updates (shows as 'in progress').

        Args:
            title: Short title for the action
            description: Optional detailed description
            metadata: Optional metadata for debugging
        """
        self._log(LogLevel.ACTION, title, description, metadata)

    def log_success(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Log a success message.

        Use this when a step completes successfully.

        Args:
            title: Short success message
            description: Optional detailed description
            metadata: Optional metadata for debugging
        """
        self._log(LogLevel.SUCCESS, title, description, metadata)

    def log_warning(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Log a warning message.

        Use this for non-fatal issues.

        Args:
            title: Short warning message
            description: Optional detailed description
            metadata: Optional metadata for debugging
        """
        self._log(LogLevel.WARNING, title, description, metadata)

    def log_error(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Log an error message.

        Use this for errors that affect execution.

        Args:
            title: Short error message
            description: Optional detailed description
            metadata: Optional metadata for debugging
        """
        self._log(LogLevel.ERROR, title, description, metadata)

    def _log(
        self,
        level: LogLevel,
        title: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """
        Internal logging method.

        Args:
            level: Log level
            title: Log title
            description: Log description
            metadata: Optional metadata
        """
        entry = ProgressEntry(
            level=level,
            title=title,
            description=description,
            metadata=metadata or {},
        )
        self._entries.append(entry)

        if self._callback:
            self._callback(entry)

    def clear(self) -> None:
        """Clear all logged entries."""
        self._entries.clear()

    def get_entries_by_level(self, level: LogLevel) -> list[ProgressEntry]:
        """
        Get all entries of a specific log level.

        Args:
            level: The log level to filter by

        Returns:
            List of entries matching the level
        """
        return [e for e in self._entries if e.level == level]

    def has_errors(self) -> bool:
        """Check if any errors have been logged."""
        return any(e.level == LogLevel.ERROR for e in self._entries)

    def has_warnings(self) -> bool:
        """Check if any warnings have been logged."""
        return any(e.level == LogLevel.WARNING for e in self._entries)


class MockProgressReporter(ProgressReporterBase):
    """
    Mock progress reporter for testing automations.

    This implementation stores all log entries in memory for
    later inspection during tests.

    Example:
        reporter = MockProgressReporter()

        # Run automation with reporter
        context = ExecutionContext("test-123", reporter)
        automation.run(context)

        # Assert on logged entries
        assert reporter.action_count == 3
        assert reporter.has_entry_with_title("Processing data")
        assert not reporter.has_errors()
    """

    def __init__(self) -> None:
        """Initialize mock progress reporter."""
        self._entries: list[ProgressEntry] = []

    @property
    def entries(self) -> list[ProgressEntry]:
        """Get all logged entries."""
        return list(self._entries)

    @property
    def info_count(self) -> int:
        """Get count of info entries."""
        return sum(1 for e in self._entries if e.level == LogLevel.INFO)

    @property
    def action_count(self) -> int:
        """Get count of action entries."""
        return sum(1 for e in self._entries if e.level == LogLevel.ACTION)

    @property
    def success_count(self) -> int:
        """Get count of success entries."""
        return sum(1 for e in self._entries if e.level == LogLevel.SUCCESS)

    @property
    def warning_count(self) -> int:
        """Get count of warning entries."""
        return sum(1 for e in self._entries if e.level == LogLevel.WARNING)

    @property
    def error_count(self) -> int:
        """Get count of error entries."""
        return sum(1 for e in self._entries if e.level == LogLevel.ERROR)

    def log_info(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an informational message."""
        self._log(LogLevel.INFO, title, description, metadata)

    def log_action(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an action being performed."""
        self._log(LogLevel.ACTION, title, description, metadata)

    def log_success(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log a successful step completion."""
        self._log(LogLevel.SUCCESS, title, description, metadata)

    def log_warning(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log a warning (non-fatal issue)."""
        self._log(LogLevel.WARNING, title, description, metadata)

    def log_error(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an error."""
        self._log(LogLevel.ERROR, title, description, metadata)

    def _log(
        self,
        level: LogLevel,
        title: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Internal logging method."""
        entry = ProgressEntry(
            level=level,
            title=title,
            description=description,
            metadata=metadata or {},
        )
        self._entries.append(entry)

    def clear(self) -> None:
        """Clear all logged entries."""
        self._entries.clear()

    def has_errors(self) -> bool:
        """Check if any errors have been logged."""
        return self.error_count > 0

    def has_warnings(self) -> bool:
        """Check if any warnings have been logged."""
        return self.warning_count > 0

    def has_entry_with_title(self, title: str) -> bool:
        """
        Check if an entry with the given title exists.

        Args:
            title: The title to search for

        Returns:
            True if an entry with the title exists
        """
        return any(e.title == title for e in self._entries)

    def get_entries_by_level(self, level: LogLevel) -> list[ProgressEntry]:
        """
        Get all entries of a specific log level.

        Args:
            level: The log level to filter by

        Returns:
            List of entries matching the level
        """
        return [e for e in self._entries if e.level == level]

    def get_last_entry(self) -> ProgressEntry | None:
        """
        Get the most recent log entry.

        Returns:
            The last entry or None if no entries
        """
        return self._entries[-1] if self._entries else None

    def assert_no_errors(self) -> None:
        """
        Assert that no errors have been logged.

        Raises:
            AssertionError: If errors have been logged
        """
        if self.has_errors():
            errors = self.get_entries_by_level(LogLevel.ERROR)
            error_titles = [e.title for e in errors]
            raise AssertionError(f"Expected no errors, but found: {error_titles}")

    def assert_has_entry(
        self,
        level: LogLevel,
        title: str,
        description: str | None = None,
    ) -> None:
        """
        Assert that a specific entry exists.

        Args:
            level: Expected log level
            title: Expected title
            description: Optional expected description

        Raises:
            AssertionError: If the entry doesn't exist
        """
        for entry in self._entries:
            if entry.level == level and entry.title == title:
                if description is None or entry.description == description:
                    return
        raise AssertionError(
            f"Expected entry with level={level.value}, title='{title}' not found"
        )


class ConsoleProgressReporter(ProgressReporterBase):
    """
    Progress reporter that prints to console.

    Useful for local development and debugging.
    """

    # ANSI color codes for terminal output
    _COLORS = {
        LogLevel.INFO: "\033[94m",  # Blue
        LogLevel.ACTION: "\033[96m",  # Cyan
        LogLevel.SUCCESS: "\033[92m",  # Green
        LogLevel.WARNING: "\033[93m",  # Yellow
        LogLevel.ERROR: "\033[91m",  # Red
    }
    _RESET = "\033[0m"
    _ICONS = {
        LogLevel.INFO: "ℹ",
        LogLevel.ACTION: "⚙",
        LogLevel.SUCCESS: "✓",
        LogLevel.WARNING: "⚠",
        LogLevel.ERROR: "✗",
    }

    def __init__(self, use_colors: bool = True) -> None:
        """
        Initialize console progress reporter.

        Args:
            use_colors: Whether to use ANSI colors in output
        """
        self._use_colors = use_colors
        self._entries: list[ProgressEntry] = []

    @property
    def entries(self) -> list[ProgressEntry]:
        """Get all logged entries."""
        return list(self._entries)

    def log_info(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an informational message."""
        self._log(LogLevel.INFO, title, description, metadata)

    def log_action(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an action being performed."""
        self._log(LogLevel.ACTION, title, description, metadata)

    def log_success(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log a successful step completion."""
        self._log(LogLevel.SUCCESS, title, description, metadata)

    def log_warning(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log a warning (non-fatal issue)."""
        self._log(LogLevel.WARNING, title, description, metadata)

    def log_error(
        self,
        title: str,
        description: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Log an error."""
        self._log(LogLevel.ERROR, title, description, metadata)

    def _log(
        self,
        level: LogLevel,
        title: str,
        description: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Internal logging method."""
        entry = ProgressEntry(
            level=level,
            title=title,
            description=description,
            metadata=metadata or {},
        )
        self._entries.append(entry)

        # Print to console
        icon = self._ICONS[level]
        if self._use_colors:
            color = self._COLORS[level]
            prefix = f"{color}{icon}{self._RESET}"
        else:
            prefix = icon

        print(f"{prefix} {title}")
        if description:
            print(f"  {description}")

    def clear(self) -> None:
        """Clear all logged entries."""
        self._entries.clear()
