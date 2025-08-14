# Tests/test_sentiment_gate.py
import os, sys, json
from pathlib import Path
import pytest

# --- Add the project path(s) BEFORE any other imports ---
HERE = Path(__file__).resolve()
SEARCH_ROOTS = [HERE.parents[1], HERE.parents[2]]

def add_path_for(filename: str) -> Path:
    for root in SEARCH_ROOTS:
        if not root or not root.exists():
            continue
        hits = list(root.rglob(filename))
        if hits:
            sys.path.insert(0, str(hits[0].parent))
            return hits[0]
    pytest.skip(f"{filename} not found in project", allow_module_level=True)

cred_py = add_path_for("credentials.py")
sent_py = add_path_for("sentiment_client.py")

from credentials import ensure_openai_env                          # noqa: E402
from sentiment_client import get_sentiment_for_price_action         # noqa: E402

# Find and load OpenAI creds
hits = list(cred_py.parent.rglob("openai_credentials.cfg"))
if not hits:
    pytest.skip("openai_credentials.cfg not found", allow_module_level=True)
ensure_openai_env(str(hits[0]))

# Find config
hits_cfg = list(sent_py.parent.rglob("bot_config.json"))
if not hits_cfg:
    pytest.skip("bot_config.json not found", allow_module_level=True)
BOT_CFG = hits_cfg[0]

@pytest.mark.skipif(not os.getenv("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_sentiment_gate_threshold():
    cfg = json.loads(BOT_CFG.read_text(encoding="utf-8"))
    s_cfg = cfg.get("sentiment", {}) or {}
    thr = float(s_cfg.get("min_score", 0.15))
    model = s_cfg.get("model", "gpt-4o-mini")
    timeout = int(s_cfg.get("timeout_s", 20))

    sent = get_sentiment_for_price_action(
        model=model,
        instrument_name="US 500 £1",
        close=5500.0, sma20=5490.0, rsi14=58.0,
        timeout_s=timeout,
    )
    assert sent is not None, "Sentiment call failed/None"
    score = float(sent.get("score", 0.0))
    assert 0.0 <= score <= 1.0
    assert score >= thr, f"score {score:.2f} < thr {thr:.2f}"
