# sentiment_llm.py
# Minimal LLM sentiment scorer with JSON-structured output + simple aggregation.
# Usage examples:
#   python sentiment_llm.py --topic "US 500" --text "CPI beats, futures jump"
#   python sentiment_llm.py --topic "Germany 40" --file news.txt
# As a library:
#   from sentiment_llm import score_texts_aggregate

from __future__ import annotations
import os, json, argparse, sys
from dataclasses import dataclass, asdict
from typing import List, Dict, Any
from datetime import datetime, timezone

# ---- LLM client (OpenAI python package >=1.0)
try:
    from openai import OpenAI
except Exception as e:
    OpenAI = None

# ---- Schema
@dataclass
class SentimentItem:
    topic: str
    score: float        # -1..1 (negative..positive)
    confidence: float   # 0..1
    stance: str         # bullish | bearish | neutral | mixed
    reasons: str
    tags: List[str]

def _client():
    if OpenAI is None:
        raise RuntimeError("OpenAI client not available. Install: pip install openai")
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise RuntimeError("Please set OPENAI_API_KEY")
    return OpenAI(api_key=api_key)

_SYSTEM = (
    "You are a cautious financial news analyst. "
    "Rate sentiment toward the given topic for short-term trading (intra-day to 1-2 days). "
    "Return STRICT JSON with fields: topic, score (-1..1), confidence (0..1), "
    "stance (bullish|bearish|neutral|mixed), reasons (<=2 sentences), tags (array of 1-5 short tokens). "
    "If the text is irrelevant, output score=0, confidence<=0.3, stance='neutral'."
)

def score_text_llm(topic: str, text: str, model: str = "gpt-4o-mini") -> SentimentItem:
    client = _client()
    prompt = (
        f"Topic: {topic}\n"
        "Text to analyze (may include headlines, snippets):\n"
        f"-----\n{text}\n-----\n"
        "Return ONLY JSON."
    )
    try:
        resp = client.chat.completions.create(
            model=model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {"role":"system","content":_SYSTEM},
                {"role":"user","content":prompt},
            ],
        )
        content = resp.choices[0].message.content
        data = json.loads(content)
        # Basic validation/defaults
        item = SentimentItem(
            topic=data.get("topic", topic)[:64],
            score=float(max(-1.0, min(1.0, data.get("score", 0.0)))),
            confidence=float(max(0.0, min(1.0, data.get("confidence", 0.0)))),
            stance=(data.get("stance") or "neutral").lower(),
            reasons=(data.get("reasons") or "")[:400],
            tags=[str(t)[:20] for t in (data.get("tags") or [])][:5],
        )
        return item
    except Exception as e:
        # Fallback neutral
        return SentimentItem(topic=topic, score=0.0, confidence=0.0, stance="neutral",
                             reasons=f"error: {type(e).__name__}", tags=["error"])

def score_texts_aggregate(topic: str, texts: List[str], model: str = "gpt-4o-mini") -> Dict[str, Any]:
    """Score many texts and return an aggregate dict."""
    items = [score_text_llm(topic, t, model=model) for t in texts if (t or "").strip()]
    if not items:
        return {
            "topic": topic, "n_docs": 0, "score": 0.0, "confidence": 0.0, "stance": "neutral",
            "reasons": "no documents", "tags": [], "items":[]
        }
    # simple average weighted by confidence
    wsum = sum(max(0.05, it.confidence) for it in items)
    s_avg = sum(it.score * max(0.05, it.confidence) for it in items) / wsum
    c_avg = sum(it.confidence for it in items) / len(items)
    # stance by score threshold
    stance = "neutral"
    if s_avg >= 0.15: stance = "bullish"
    if s_avg <= -0.15: stance = "bearish"
    agg = {
        "topic": topic,
        "n_docs": len(items),
        "score": round(s_avg, 4),
        "confidence": round(c_avg, 4),
        "stance": stance,
        "reasons": "; ".join(sorted(set(it.stance for it in items)))[:200],
        "tags": sorted(set(tag for it in items for tag in it.tags))[:8],
        "items": [asdict(it) for it in items],
        "ts_utc": datetime.now(timezone.utc).isoformat(timespec="seconds")+"Z",
    }
    return agg

def _load_text_from_file(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True, help='e.g. "US 500" or "Germany 40"')
    ap.add_argument("--text", help="single text to score")
    ap.add_argument("--file", help="file path with text to score")
    ap.add_argument("--model", default="gpt-4o-mini")
    ap.add_argument("--json-out", help="write JSON result to this path")
    args = ap.parse_args()

    text = args.text or ( _load_text_from_file(args.file) if args.file else None )
    if not text:
        print("Provide --text or --file", file=sys.stderr); sys.exit(2)

    res = score_texts_aggregate(args.topic, [text], model=args.model)
    out = json.dumps(res, ensure_ascii=False, separators=(",",":"))
    print(out)
    if args.json_out:
        with open(args.json_out, "w", encoding="utf-8") as f: f.write(out)

if __name__ == "__main__":
    main()
