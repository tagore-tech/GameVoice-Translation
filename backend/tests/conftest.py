"""
backend/tests/conftest.py

Adds the backend root to sys.path so test files can import
audio.processor, ws.handler, etc. directly.
"""

import sys
import os

# backend/ directory (one level up from tests/)
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_configure(config):
    """Register custom markers to suppress PytestUnknownMarkWarning."""
    config.addinivalue_line(
        "markers",
        "integration: marks tests that require a live backend and real API credentials",
    )
