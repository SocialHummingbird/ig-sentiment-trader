# credentials.py
import os

def load_credentials(file_path="ig_credentials.cfg"):
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Credentials file not found: {file_path}")
    creds = {}
    with open(file_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            creds[k.strip()] = v.strip()
    required = ["IG_IDENTIFIER", "IG_PASSWORD", "IG_API_KEY", "IG_ACC_TYPE"]
    missing = [r for r in required if r not in creds]
    if missing:
        raise ValueError(f"Missing keys in credentials file: {missing}")
    return creds


# --- add below your existing code, do NOT modify load_credentials above ---

def ensure_openai_env(cfg_path="openai_credentials.cfg"):
    """
    Load OPENAI_* values from a simple KEY=VALUE cfg file and put them into
    os.environ for the current process. Returns a dict of what was loaded.

    Expected keys:
      - OPENAI_API_KEY (required)
      - OPENAI_MODEL (optional, e.g. gpt-4o-mini)
      - OPENAI_TIMEOUT_S (optional, e.g. 20)
    """
    if not os.path.exists(cfg_path):
        raise FileNotFoundError(f"{cfg_path} not found")

    loaded = {}
    with open(cfg_path, "r", encoding="utf-8") as f:
        for raw in f:
            s = raw.strip()
            if not s or s.startswith("#") or "=" not in s:
                continue
            k, v = s.split("=", 1)
            k, v = k.strip(), v.strip()
            os.environ[k] = v
            loaded[k] = v

    if not os.getenv("OPENAI_API_KEY"):
        raise ValueError("OPENAI_API_KEY missing in openai_credentials.cfg")

    return loaded
