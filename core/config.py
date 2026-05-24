import json
import os

_config = None

def load_config(path: str = None) -> dict:
    global _config
    if _config is not None:
        return _config
    if path is None:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(base, "config.json")
    with open(path, "r") as f:
        _config = json.load(f)
    return _config

def get(key: str, default=None):
    cfg = load_config()
    return cfg.get(key, default)
