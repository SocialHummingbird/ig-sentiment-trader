# IG Sentiment Trader (Demo)

Single-run trader using IG REST + SMA/RSI signal + LLM sentiment gate + risk guards.
See `login_only/trade_once.py`. Copy `bot_config.example.json` to `bot_config.json`
and add `login_only/ig_credentials.cfg` / `openai_credentials.cfg` (not in Git).

## Run
python .\login_only\trade_once.py --config bot_config.json
