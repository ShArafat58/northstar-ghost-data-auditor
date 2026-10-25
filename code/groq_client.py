"""Groq SDK model-access preflight and safe provider error classification.

The official SDK uses the httpx transport instead of Python urllib; this
avoids conflating a urllib HTTP 403 with confirmed model-level restrictions.
Credentials and raw provider bodies must never be logged.
"""
from __future__ import annotations

import os
import re

API_MODELS_URL = 'https://api.groq.com/openai/v1/models'
DEFAULT_MODEL = 'openai/gpt-oss-20b'
_SAFE_TAG = re.compile(r'^[A-Za-z][A-Za-z0-9_\-]{0,79}$')


def safe_api_error(exc: Exception) -> str:
    """Return status plus safe, structured provider code, never a free-text body."""
    status = getattr(exc, 'status_code', None)
    response = getattr(exc, 'response', None)
    if status is None and response is not None:
        status = getattr(response, 'status_code', None)
    summary = f'HTTP {status}' if isinstance(status, int) else type(exc).__name__
    body = getattr(exc, 'body', None)
    if not isinstance(body, dict) and response is not None:
        try:
            body = response.json()
        except (ValueError, AttributeError, TypeError):
            body = None
    error = body.get('error', body) if isinstance(body, dict) else None
    if isinstance(error, dict):
        for field in ('type', 'code'):
            value = error.get(field)
            if isinstance(value, str) and _SAFE_TAG.fullmatch(value):
                summary += f' {field}={value}'
    return summary


def require_groq_sdk():
    try:
        from groq import Groq, APIConnectionError, APIStatusError
    except ImportError as exc:
        raise RuntimeError('Groq SDK not installed: run python -m pip install groq') from exc
    return Groq, APIConnectionError, APIStatusError


def check_groq_model(model: str = DEFAULT_MODEL) -> bool:
    """Read model availability via the *same SDK transport* as inference."""
    key = os.environ.get('GROQ_API_KEY', '').strip()
    if not key:
        raise RuntimeError('GROQ_API_KEY is not set in this terminal session')
    Groq, APIConnectionError, APIStatusError = require_groq_sdk()
    try:
        client = Groq(api_key=key, timeout=20, max_retries=1)
        available = {m.id for m in client.models.list().data}
    except APIStatusError as exc:
        raise RuntimeError('Groq model-list request failed: ' + safe_api_error(exc)) from None
    except APIConnectionError:
        raise RuntimeError('Groq model-list connection failed') from None
    if model not in available:
        raise RuntimeError(f'Requested model is not in the accessible model list: {model}')
    return True
