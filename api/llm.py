"""
llm.py — Unified LLM provider abstraction.

Routes analysis prompts to the configured provider:

  - **ollama** (default): Local Ollama via merLLM proxy at ``config.OLLAMA_URL``.
  - **ollama_cloud**: Ollama paid API at ``config.ESCALATION_API_URL``.
  - **claude**: Anthropic Claude API.

All callers use ``generate()`` which returns the raw response text.
The caller is responsible for JSON parsing.
"""
import json
import logging

import requests

import config

log = logging.getLogger(__name__)


def generate(
    prompt: str,
    *,
    format: str | None = "json",
    temperature: float = 0.1,
    num_predict: int = 768,
    num_ctx: int = 8192,
    timeout: int = 90,
) -> str:
    """
    Send a prompt to the configured LLM provider and return the response text.

    :param prompt: The full prompt string.
    :param format: Response format hint ("json" or None). Used by Ollama;
                   for Claude, the system prompt requests JSON output.
    :param temperature: Sampling temperature.
    :param num_predict: Max tokens to generate.
    :param num_ctx: Context window size (Ollama only).
    :param timeout: Request timeout in seconds.
    :return: Raw response text from the LLM.
    :raises requests.HTTPError: On non-2xx response.
    """
    provider = config.ESCALATION_PROVIDER or "ollama"

    if provider == "claude":
        return _claude(prompt, temperature=temperature,
                       max_tokens=num_predict, timeout=timeout,
                       json_mode=format == "json")
    elif provider == "ollama_cloud":
        return _ollama_cloud(prompt, format=format,
                             temperature=temperature,
                             num_predict=num_predict,
                             num_ctx=num_ctx, timeout=timeout)
    else:
        return _ollama_local(prompt, format=format,
                             temperature=temperature,
                             num_predict=num_predict,
                             num_ctx=num_ctx, timeout=timeout)


def _ollama_local(
    prompt: str, *, format: str | None, temperature: float,
    num_predict: int, num_ctx: int, timeout: int,
) -> str:
    """Call Ollama via the local merLLM proxy."""
    body: dict = {
        "model":   config.effective_model(),
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": temperature, "num_predict": num_predict,
                    "num_ctx": num_ctx},
    }
    if format:
        body["format"] = format

    r = requests.post(
        config.OLLAMA_URL,
        headers=config.ollama_headers(),
        json=body,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json().get("response", "")


def _ollama_cloud(
    prompt: str, *, format: str | None, temperature: float,
    num_predict: int, num_ctx: int, timeout: int,
) -> str:
    """Call Ollama paid cloud API."""
    url = config.ESCALATION_API_URL or config.OLLAMA_URL
    headers = {"Content-Type": "application/json"}
    if config.ESCALATION_API_KEY:
        headers["Authorization"] = f"Bearer {config.ESCALATION_API_KEY}"

    body: dict = {
        "model":   config.effective_model(),
        "prompt":  prompt,
        "stream":  False,
        "options": {"temperature": temperature, "num_predict": num_predict,
                    "num_ctx": num_ctx},
    }
    if format:
        body["format"] = format

    r = requests.post(
        url if "/api/" in url else f"{url.rstrip('/')}/api/generate",
        headers=headers,
        json=body,
        timeout=timeout,
    )
    r.raise_for_status()
    return r.json().get("response", "")


def _claude(
    prompt: str, *, temperature: float, max_tokens: int,
    timeout: int, json_mode: bool,
) -> str:
    """Call the Anthropic Claude Messages API."""
    api_key = config.ESCALATION_API_KEY
    if not api_key:
        raise ValueError("ESCALATION_API_KEY is required for Claude provider")

    url = config.ESCALATION_API_URL or "https://api.anthropic.com"
    model = config.effective_model() or "claude-sonnet-4-20250514"

    system = "You are an analysis assistant. Return structured data only."
    if json_mode:
        system += " Always respond with valid JSON and nothing else."

    r = requests.post(
        f"{url.rstrip('/')}/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "system": system,
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout=timeout,
    )
    r.raise_for_status()
    data = r.json()

    # Extract text from Claude's response format
    content = data.get("content", [])
    parts = [block["text"] for block in content if block.get("type") == "text"]
    return "\n".join(parts)
