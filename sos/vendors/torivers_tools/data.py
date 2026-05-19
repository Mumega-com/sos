"""
Data processing utilities for automations.

This module provides utilities for common data processing tasks
that are performed deterministically without LLM involvement.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass
class DataStats:
    """Statistics for a dataset."""

    count: int
    min_value: float | None
    max_value: float | None
    mean: float | None
    median: float | None
    std_dev: float | None


class DataProcessor:
    """
    Utility class for data processing operations.

    These are deterministic operations that don't require LLM calls.
    Use these for data transformation, aggregation, and analysis.

    Example:
        processor = DataProcessor()

        # Parse and clean data
        items = processor.parse_csv(csv_content)
        cleaned = processor.clean_nulls(items)

        # Aggregate
        stats = processor.calculate_stats(cleaned, "amount")
    """

    @staticmethod
    def parse_csv(
        content: str,
        delimiter: str = ",",
        has_header: bool = True,
    ) -> list[dict[str, Any]]:
        """
        Parse CSV content into list of dictionaries.

        Args:
            content: CSV string content
            delimiter: Column delimiter
            has_header: Whether first row is header

        Returns:
            List of row dictionaries
        """
        import csv
        from io import StringIO

        reader = csv.reader(StringIO(content), delimiter=delimiter)
        rows = list(reader)

        if not rows:
            return []

        if has_header:
            headers = rows[0]
            return [dict(zip(headers, row)) for row in rows[1:]]
        else:
            return [{f"col_{i}": val for i, val in enumerate(row)} for row in rows]

    @staticmethod
    def parse_json(content: str) -> Any:
        """
        Parse JSON content.

        Args:
            content: JSON string

        Returns:
            Parsed JSON data
        """
        import json

        return json.loads(content)

    @staticmethod
    def to_json(data: Any, indent: int | None = 2) -> str:
        """
        Serialize data to JSON string.

        Args:
            data: Data to serialize
            indent: Indentation level (None for compact)

        Returns:
            JSON string
        """
        import json

        return json.dumps(data, indent=indent, default=str)

    @staticmethod
    def clean_nulls(
        items: list[dict[str, Any]],
        null_values: list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Remove null/empty values from dictionaries.

        Args:
            items: List of dictionaries
            null_values: Values to treat as null

        Returns:
            Cleaned items
        """
        null_set = set(null_values or [None, "", "null", "NULL", "None"])

        return [{k: v for k, v in item.items() if v not in null_set} for item in items]

    @staticmethod
    def filter_items(
        items: list[dict[str, Any]],
        field: str,
        condition: str,
        value: Any,
    ) -> list[dict[str, Any]]:
        """
        Filter items by field condition.

        Args:
            items: List of dictionaries
            field: Field to filter on
            condition: Condition (eq, ne, gt, gte, lt, lte, contains)
            value: Value to compare against

        Returns:
            Filtered items
        """
        ops = {
            "eq": lambda a, b: a == b,
            "ne": lambda a, b: a != b,
            "gt": lambda a, b: float(a) > float(b),
            "gte": lambda a, b: float(a) >= float(b),
            "lt": lambda a, b: float(a) < float(b),
            "lte": lambda a, b: float(a) <= float(b),
            "contains": lambda a, b: str(b) in str(a),
        }

        if condition not in ops:
            raise ValueError(f"Unknown condition: {condition}")

        op = ops[condition]
        return [item for item in items if field in item and op(item[field], value)]

    @staticmethod
    def group_by(
        items: list[dict[str, Any]],
        field: str,
    ) -> dict[Any, list[dict[str, Any]]]:
        """
        Group items by a field value.

        Args:
            items: List of dictionaries
            field: Field to group by

        Returns:
            Dictionary mapping field values to item lists
        """
        groups: dict[Any, list[dict[str, Any]]] = {}
        for item in items:
            key = item.get(field)
            if key not in groups:
                groups[key] = []
            groups[key].append(item)
        return groups

    @staticmethod
    def calculate_stats(
        items: list[dict[str, Any]],
        field: str,
    ) -> DataStats:
        """
        Calculate statistics for a numeric field.

        Args:
            items: List of dictionaries
            field: Numeric field to analyze

        Returns:
            Statistics for the field
        """
        import statistics

        values = []
        for item in items:
            if field in item:
                try:
                    values.append(float(item[field]))
                except (ValueError, TypeError):
                    pass

        if not values:
            return DataStats(
                count=0,
                min_value=None,
                max_value=None,
                mean=None,
                median=None,
                std_dev=None,
            )

        return DataStats(
            count=len(values),
            min_value=min(values),
            max_value=max(values),
            mean=statistics.mean(values),
            median=statistics.median(values),
            std_dev=statistics.stdev(values) if len(values) > 1 else 0.0,
        )

    @staticmethod
    def format_date(
        date: datetime | str,
        format_string: str = "%Y-%m-%d",
    ) -> str:
        """
        Format a date to string.

        Args:
            date: Date to format
            format_string: strftime format string

        Returns:
            Formatted date string
        """
        if isinstance(date, str):
            date = datetime.fromisoformat(date.replace("Z", "+00:00"))
        return date.strftime(format_string)

    @staticmethod
    def truncate_text(text: str, max_length: int, suffix: str = "...") -> str:
        """
        Truncate text to maximum length.

        Args:
            text: Text to truncate
            max_length: Maximum length including suffix
            suffix: Suffix to add when truncated

        Returns:
            Truncated text
        """
        if len(text) <= max_length:
            return text
        return text[: max_length - len(suffix)] + suffix
