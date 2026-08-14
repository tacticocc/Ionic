"""Errors raised by Ionic's local subscription-runtime boundary."""

from __future__ import annotations


class RuntimeAdapterError(RuntimeError):
    """Base class for failures in an official local runtime adapter."""


class RuntimeUnavailable(RuntimeAdapterError):
    """The requested vendor runtime is missing or not authenticated."""


class RuntimePolicyError(RuntimeAdapterError):
    """The request would cross Ionic's local runtime safety policy."""


class RuntimeExecutionError(RuntimeAdapterError):
    """The official runtime process failed."""


class RuntimeTimeout(RuntimeExecutionError):
    """The runtime exceeded its configured wall-clock limit."""


class RuntimeOutputLimit(RuntimeExecutionError):
    """The runtime exceeded its configured combined output limit."""


class RuntimeOutputError(RuntimeAdapterError):
    """The runtime returned output outside the structured boundary."""
