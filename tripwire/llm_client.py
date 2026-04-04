import json
import os
import re

from . import config

# --- Optional OpenAI import ---
try:
    from openai import OpenAI
except ImportError:
    OpenAI = None

# --- OpenAI client (lazy init; avoids missing bearer header in CI) ---
_client = None


def get_openai_client():
    """Lazily create and cache an OpenAI client.

    Ensures OPENAI_API_KEY is read at the moment the client is needed.
    """
    global _client

    if OpenAI is None:
        raise RuntimeError("OpenAI SDK not available. Ensure 'openai' is installed.")

    if _client is None:
        api_key = (os.getenv("OPENAI_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("OPENAI_API_KEY environment variable is not set.")
        # Explicit is fine and avoids any ambiguity.
        _client = OpenAI(api_key=api_key)

    return _client


def _call_llm_json(prompt: str, model: str = None, fallback: dict = None) -> dict:
    """Call the LLM and parse a JSON response.

    Fail-closed behaviour:
    - If the client is unavailable or response is not parseable JSON, return the fallback dict.
    - Default fallback includes BOTH keys ('decision' and 'overall_decision') for compatibility
      with Pass 1/2 and legacy callers.

    Args:
        prompt: The prompt to send to the LLM.
        model: Model name override. Defaults to config.LLM_MODEL.
        fallback: Custom fallback dict for parse/call failures. Defaults to Stage 4 uncertain shape.
    """
    if model is None:
        model = config.LLM_MODEL

    if fallback is None:
        fallback = {
            "decision": "uncertain",
            "overall_decision": "uncertain",
            "confidence": "low",
            "reason": "LLM call failed or output was not valid JSON."
        }
    else:
        fallback = dict(fallback)

    try:
        client = get_openai_client()
    except Exception as e:
        fallback["reason"] = f"LLM client unavailable: {e}"
        return fallback

    try:
        resp = client.responses.create(
            model=model,
            input=prompt
        )
        txt = getattr(resp, "output_text", None)
        if not txt:
            return fallback

        # Try strict JSON first
        try:
            return json.loads(txt)
        except Exception:
            # Try to extract the first JSON object in the response
            m = re.search(r"\{.*\}", txt, flags=re.S)
            if m:
                try:
                    return json.loads(m.group(0))
                except Exception:
                    return fallback
            return fallback
    except Exception as e:
        fallback["reason"] = f"LLM exception: {e}"
        return fallback
