# Tests/smoke_test_sentiment_gate.py
import os, sys, json
from pathlib import Path

# --- Add the project path(s) BEFORE any other imports ---
HERE = Path(__file__).resolve()
SEARCH_ROOTS = [HERE.parents[1], HERE.parents[2]]  # repo root guesses

def add_path_for(filename: str) -> Path:
    """Find a file anywhere under SEARCH_ROOTS and add its parent to sys.path."""
    for root in SEARCH_ROOTS:
        if not root or not root.exists():
            continue
        hits = list(root.rglob(filename))
        if hits:
            sys.path.insert(0, str(hits[0].parent))
            return hits[0]
    raise ImportError(f"Could not find {filename} under: " + ", ".join(str(r) for r in SEARCH_ROOTS if r))

# Ensure modules are importable
cred_py   = add_path_for("credentials.py")
sent_py   = add_path_for("sentiment_client.py")

# Load OpenAI creds (auto-find the cfg)
from credentials import ensure_openai_env   # noqa: E402
cfg_hits = list(cred_py.parent.rglob("openai_credentials.cfg"))
if not cfg_hits:
    raise FileNotFoundError("openai_credentials.cfg not found anywhere under project tree.")
ensure_openai_env(str(cfg_hits[0]))

# Import the scorer
from sentiment_client import get_sentiment_for_price_action  # noqa: E402

# Load bot config (auto-find)
cfg_hits = list(sent_py.parent.rglob("bot_config.json"))
if not cfg_hits:
    raise FileNotFoundError("bot_config.json not found anywhere under project tree.")
cfg = json.loads(cfg_hits[0].read_text(encoding="utf-8"))

thr = float(cfg.get("sentiment", {}).get("min_score", 0.15))
model = cfg.get("sentiment", {}).get("model", "gpt-4o-mini")
timeout = int(cfg.get("sentiment", {}).get("timeout_s", 20))

cases = [
    ("US 500 £1 (bullish-ish)", 5500.0, 5490.0, 58.0),
    ("Germany 40 £1 (neutral-ish)", 18500.0, 18510.0, 49.0),
]

rc = 0
for name, close, sma20, rsi14 in cases:
    print(f"\n{name}")
    sent = get_sentiment_for_price_action(
        model=model, instrument_name=name,
        close=close, sma20=sma20, rsi14=rsi14, timeout_s=timeout
    )
    print("sentiment:", sent)
    if not sent:
        print("RESULT: UNAVAILABLE (treat as BLOCK)"); rc = max(rc, 1); continue
    score = float(sent.get("score", 0.0))
    if score >= thr:
        print(f"RESULT: PASS (score {score:.2f} ≥ thr {thr:.2f})")
    else:
        print(f"RESULT: BLOCK (score {score:.2f} < thr {thr:.2f})")
sys.exit(rc)
