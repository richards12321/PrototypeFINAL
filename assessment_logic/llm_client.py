"""Centralized Azure OpenAI client with logging, retries, and sensible defaults.

All API calls route through here. The Azure deployment configuration for our
Capgemini capstone is HARD-CODED below — region, deployment names, API version,
and endpoint URL are stable values from the project's Azure resource and don't
need to live in secrets. The only thing that has to be in secrets is the API
key, because that rotates and grants real access to the Azure resource.

Required secret (set in .streamlit/secrets.toml or .env):
    AZURE_OPENAI_API_KEY        - Azure resource key

Hard-coded deployments (jt-learning-openai-7382 in swedencentral):
    gpt-4-1-mini-qc             - text reasoning (follow-ups, scoring, feedback)
    capstone-transcribe         - speech-to-text (gpt-4o-mini-transcribe)
    capstone-realtime-voice     - realtime voice (not used in v5; reserved)

If Capgemini ever rotates the deployment names, update CAPSTONE_CONFIG below.
"""

from __future__ import annotations

import json
import logging
import os
import random
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from openai import AzureOpenAI, OpenAIError, RateLimitError

load_dotenv()

# ---------------------------------------------------------------------------
# HARD-CODED CAPSTONE AZURE CONFIG
# ---------------------------------------------------------------------------
# Source: capstone-endpoints documentation, generated 2026-05-04.
# Subscription: 750a4c02-497a-4e4a-810d-d9e26ff539d4
# Resource group: jt-learning-agent
# Account: jt-learning-openai-7382
# ---------------------------------------------------------------------------
CAPSTONE_CONFIG = {
    "endpoint": "https://swedencentral.api.cognitive.microsoft.com",
    "api_version": "2024-10-21",
    # Text reasoning: gpt-4.1-mini, 10 req/min, 10k tokens/min
    "chat_deployment": "gpt-4-1-mini-qc",
    # Speech-to-text: gpt-4o-mini-transcribe, 6000 req/min, 60k tokens/min
    "transcribe_deployment": "capstone-transcribe",
    # Realtime voice (reserved for future phase, not used here)
    "realtime_deployment": "capstone-realtime-voice",
}

# Soft rate limit for the chat deployment. Azure reports 10 req/min hard cap;
# we stay one request under that to leave headroom for parallel candidates.
CHAT_MIN_INTERVAL_SECONDS = 6.5

LOG_DIR = Path(__file__).parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)

logger = logging.getLogger("llm_calls")
if not logger.handlers:
    handler = logging.FileHandler(LOG_DIR / "llm_calls.log")
    handler.setFormatter(
        logging.Formatter("%(asctime)s | %(levelname)s | %(message)s")
    )
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _get_api_key() -> Optional[str]:
    """Read the API key from Streamlit secrets first, then environment."""
    try:
        import streamlit as st
        if hasattr(st, "secrets") and "AZURE_OPENAI_API_KEY" in st.secrets:
            return str(st.secrets["AZURE_OPENAI_API_KEY"])
    except Exception:
        pass
    return os.getenv("AZURE_OPENAI_API_KEY")


_client: Optional[AzureOpenAI] = None
_last_chat_call_at: float = 0.0


def get_client() -> AzureOpenAI:
    """Lazily build the Azure OpenAI client. Cached after first call."""
    global _client
    if _client is not None:
        return _client

    api_key = _get_api_key()
    if not api_key:
        raise RuntimeError(
            "Missing AZURE_OPENAI_API_KEY. Set it in .streamlit/secrets.toml "
            "(Streamlit Cloud) or in your .env file (local). All other Azure "
            "config (endpoint, deployments, API version) is hard-coded in "
            "CAPSTONE_CONFIG and does not need to be set."
        )

    _client = AzureOpenAI(
        api_key=api_key,
        azure_endpoint=CAPSTONE_CONFIG["endpoint"],
        api_version=CAPSTONE_CONFIG["api_version"],
    )
    return _client


def transcribe_available() -> bool:
    """True if the transcribe deployment is reachable.

    We don't make a network call here, we just confirm the API key is set.
    The transcribe deployment itself is hard-coded and known to exist.
    """
    return bool(_get_api_key())


# Backwards-compat alias: old code called this whisper_available().
whisper_available = transcribe_available


def _throttle_chat() -> None:
    """Soft local throttle so we stay under the 10 req/min Azure cap.

    The chat deployment (gpt-4-1-mini-qc) is rate-limited to 10 requests per
    minute. Layer 3 alone makes ~10 calls per candidate (5 follow-ups + 5
    scorings), so without throttling a single candidate going fast can hit
    the wall and fail. We sleep just long enough to stay under the cap.
    """
    global _last_chat_call_at
    now = time.time()
    elapsed = now - _last_chat_call_at
    if elapsed < CHAT_MIN_INTERVAL_SECONDS and _last_chat_call_at > 0:
        time.sleep(CHAT_MIN_INTERVAL_SECONDS - elapsed)
    _last_chat_call_at = time.time()


def chat_complete(
    prompt: str,
    *,
    temperature: float = 0.0,
    max_tokens: int = 500,
    model: Optional[str] = None,  # kept for backwards compat, ignored on Azure
    retries: int = 3,
) -> str:
    """Call the chat completions endpoint with retries and backoff.

    Uses the hard-coded chat deployment (gpt-4-1-mini-qc). The model
    parameter is accepted but ignored so existing callers don't break.

    Retries with exponential backoff on rate-limit errors. The local
    throttle in _throttle_chat() should prevent most rate hits, but Azure
    can still return 429 if other concurrent candidates push us over.
    """
    client = get_client()
    deployment = CAPSTONE_CONFIG["chat_deployment"]
    last_err: Optional[Exception] = None

    for attempt in range(retries + 1):
        try:
            _throttle_chat()
            t0 = time.time()
            resp = client.chat.completions.create(
                model=deployment,
                messages=[{"role": "user", "content": prompt}],
                temperature=temperature,
                max_tokens=max_tokens,
            )
            elapsed = time.time() - t0
            content = resp.choices[0].message.content or ""
            usage = resp.usage
            logger.info(
                json.dumps({
                    "deployment": deployment,
                    "temperature": temperature,
                    "max_tokens": max_tokens,
                    "elapsed_s": round(elapsed, 3),
                    "prompt_tokens": usage.prompt_tokens if usage else None,
                    "completion_tokens": usage.completion_tokens if usage else None,
                    "prompt_preview": prompt[:120].replace("\n", " "),
                })
            )
            return content.strip()
        except RateLimitError as e:
            last_err = e
            # Exponential backoff with jitter: 4s, 8s, 16s
            wait = (2 ** (attempt + 1)) * 2 + random.uniform(0, 1)
            logger.warning(f"Rate limited (attempt {attempt}), sleeping {wait:.1f}s")
            if attempt < retries:
                time.sleep(wait)
                continue
            raise
        except OpenAIError as e:
            last_err = e
            logger.warning(f"Azure OpenAI error attempt {attempt}: {e}")
            if attempt < retries:
                time.sleep(0.5 * (attempt + 1))
                continue
            raise
    if last_err:
        raise last_err
    return ""


def transcribe_audio(audio_bytes: bytes, filename: str = "recording.wav") -> str:
    """Send audio bytes to the transcribe deployment. Returns transcript text.

    Uses the hard-coded capstone-transcribe deployment (gpt-4o-mini-transcribe).
    This replaces the older Whisper path; the function name is kept the same
    so existing callers don't have to change.

    Rate limits on this deployment are generous (6000 req/min) so we don't
    throttle locally.
    """
    client = get_client()
    deployment = CAPSTONE_CONFIG["transcribe_deployment"]

    import io
    bio = io.BytesIO(audio_bytes)
    bio.name = filename

    t0 = time.time()
    resp = client.audio.transcriptions.create(
        model=deployment,
        file=bio,
    )
    elapsed = time.time() - t0
    logger.info(
        json.dumps({
            "deployment": deployment,
            "elapsed_s": round(elapsed, 3),
            "audio_bytes": len(audio_bytes),
            "transcript_len": len(resp.text or ""),
        })
    )
    return (resp.text or "").strip()
