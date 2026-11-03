"""Bounded, credential-redacted observations for a single client's content reads."""
from src.log_redact import redact_text


def record(client, dynamic_id, endpoint, message):
    observations = getattr(client, '__dict__', {}).get('_content_observations')
    if not isinstance(observations, dict):
        observations = {}
        client._content_observations = observations
    if dynamic_id not in observations and len(observations) >= 64:
        observations.pop(next(iter(observations)))
    observations.setdefault(dynamic_id, {})[endpoint] = redact_text(str(message))[:350]


def describe(client, dynamic_id):
    observations = getattr(client, '__dict__', {}).get('_content_observations', {})
    return '；'.join(f'{key}：{value}' for key, value in observations.get(dynamic_id, {}).items())
