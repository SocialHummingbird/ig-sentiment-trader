# sentiment_client.py
import os, json, requests, traceback

OPENAI_URL = "https://api.openai.com/v1/chat/completions"

def _env_key() -> str | None:
    return os.environ.get("OPENAI_API_KEY")

def get_sentiment_for_price_action(
    model: str,
    instrument_name: str,
    close: float | None,
    sma20: float | None,
    rsi14: float | None,
    timeout_s: int = 20,
) -> dict | None:
    """
    Returns a dict like:
      {"label":"bullish|bearish|neutral","score":0.0-1.0,"explanation":"..."}
    or None if key is missing or the API call fails.
    """
    api_key = _env_key()
    if not api_key:
        print("[DEBUG] No OPENAI_API_KEY in environment.")
        return None

    facts = {
        "instrument": instrument_name,
        "close": close,
        "sma20": sma20,
        "rsi14": rsi14
    }
    sys_prompt = (
        "You are a trading assistant. Given the indicators, rate short-term bias.\n"
        "Output STRICT JSON with fields: label (bullish|bearish|neutral), "
        "score (0..1 where 1 is strong confidence in the label), explanation (<=2 sentences)."
    )
    user_msg = (
        "Indicators:\n"
        f"{json.dumps(facts)}\n"
        "Consider typical meanings: price vs SMA20 and RSI(14) thresholds around 30/70. "
        "Return ONLY JSON."
    )

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"}
    }

    try:
        print(f"[DEBUG] Calling OpenAI model={model}, timeout={timeout_s}")
        resp = requests.post(
            OPENAI_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json=payload,
            timeout=timeout_s,
        )
        print(f"[DEBUG] HTTP status: {resp.status_code}")
        print(f"[DEBUG] Raw response text: {resp.text[:500]}")

        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        print(f"[DEBUG] Model content: {content}")

        data = json.loads(content)
        label = str(data.get("label", "neutral")).lower()
        if label not in ("bullish", "bearish", "neutral"):
            label = "neutral"
        score = float(data.get("score", 0.0))
        score = max(0.0, min(1.0, score))
        expl = str(data.get("explanation", ""))[:400]
        return {"label": label, "score": score, "explanation": expl}

    except Exception as e:
        print("[ERROR] Exception in get_sentiment_for_price_action:", e)
        traceback.print_exc()
        return None
