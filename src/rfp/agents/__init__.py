"""Typed multi-agent extraction. Search stays in rfp.search; this package coordinates it."""

from rfp.agents.orchestrator import run_extraction

__all__ = ["run_extraction"]
