"""Allowlisted provider diagnostics: never save raw exceptions, bodies or prompts."""
import json
import os
import re
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import httpx
from groq import APIConnectionError, APITimeoutError

CODES = {'invalid_request_error', 'json_validate_failed', 'context_length_exceeded', 'model_not_found',
         'rate_limit_exceeded', 'rate_limit_error', 'insufficient_quota', 'blocked_api_access',
         'invalid_api_key', 'permission_denied', 'billing_required', 'credit_balance_exhausted'}
REQUEST_FIELDS = {'operation', 'method', 'endpoint', 'model', 'response_format', 'stream', 'max_completion_tokens',
                  'temperature', 'service_tier', 'reasoning_effort', 'timeout_seconds', 'prompt_utf8_bytes',
                  'context_utf8_bytes', 'input_size_bound'}
PARAMS = {'model', 'messages', 'response_format', 'max_completion_tokens', 'temperature', 'stream',
          'stream_options', 'reasoning_effort', 'service_tier'}


class ProviderFailure(ValueError):
    def __init__(self, message, diagnostic):
        super().__init__(message)
        self.diagnostic = diagnostic


def groq_failure(exc, metadata=None):
    if isinstance(exc, ProviderFailure): return exc
    status = getattr(exc, 'status_code', None)
    if not isinstance(status, int): status = None
    messages = {
        400: ('invalid_request', 'Groq rejected the request (HTTP 400).'),
        401: ('authentication', 'Groq API key was rejected (HTTP 401).'),
        402: ('billing_required', 'Groq returned payment-required (HTTP 402); no payment or paid fallback is authorised.'),
        403: ('access_denied', 'Groq denied access (HTTP 403).'),
        404: ('not_found', 'Groq model or endpoint was not found (HTTP 404).'),
        429: ('rate_limit', 'Groq rate limit or quota rejection (HTTP 429). Available free balance is unknown.'),
    }
    if status in messages:
        category, message = messages[status]
    elif status is not None and status >= 500:
        category, message = 'provider_error', f'Groq provider error (HTTP {status}).'
    elif status is not None:
        category, message = 'http_error', f'Groq request rejected (HTTP {status}).'
    elif isinstance(exc, (APITimeoutError, httpx.TimeoutException, TimeoutError)):
        category, message = 'timeout', 'Groq request timed out.'
    elif isinstance(exc, (APIConnectionError, httpx.NetworkError, ConnectionError)):
        category, message = 'network', 'Groq network connection failed.'
    elif isinstance(exc, ValueError):
        category, message = 'invalid_response', 'Groq response or local request validation failed.'
    else:
        category, message = 'application_error', 'Groq request failed inside the application before a usable response.'
    error_class = type(exc).__name__
    diagnostic = {'provider': 'groq', 'category': category, 'http_status': status,
                  'exception_type': error_class if re.fullmatch(r'[A-Za-z_]{1,60}', error_class) else 'Exception',
                  'request': {key: value for key, value in (metadata or {}).items() if key in REQUEST_FIELDS
                              and (value is None or isinstance(value, (str, bool, int, float)))}, 'occurred_at': datetime.now(timezone.utc).isoformat()}
    # Recognise only a known keyword-binding failure, never echo arbitrary exception text.
    if isinstance(exc, TypeError):
        match = re.search(r"unexpected keyword argument '([a-z_]+)'", str(exc))
        if match and match[1] in PARAMS:
            diagnostic['invalid_parameter'] = match[1]
            message = f'Groq SDK rejected unsupported parameter {match[1]} (application TypeError; no HTTP status).'
    body = getattr(exc, 'body', None)
    if isinstance(body, dict):
        error = body.get('error', body)
        if isinstance(error, dict):
            for key in ('code', 'type'):
                value = error.get(key)
                if isinstance(value, str) and value in CODES: diagnostic['provider_error_' + key] = value
            if isinstance(error.get('param'), str) and error['param'] in PARAMS: diagnostic['invalid_parameter'] = error['param']
    request_id = getattr(exc, 'request_id', None)
    if request_id is None:
        request_id = getattr(getattr(exc, 'response', None), 'headers', {}).get('x-request-id')
    if isinstance(request_id, str) and re.fullmatch(r'req_[a-zA-Z0-9_-]{1,90}', request_id):
        diagnostic['request_id'] = request_id
    diagnostic['message'] = message
    return ProviderFailure(message, diagnostic)


def save_diagnostic(path, diagnostic):
    """Save only one sanitised failure, owner-only; this is not an interview archive."""
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = None
    try:
        with tempfile.NamedTemporaryFile('w', encoding='utf-8', dir=path.parent, delete=False) as handle:
            temp = handle.name
            os.chmod(temp, 0o600)
            json.dump(diagnostic, handle, ensure_ascii=True, indent=2)
        os.replace(temp, path)
    finally:
        if temp and os.path.exists(temp): os.unlink(temp)
