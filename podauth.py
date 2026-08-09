"""
podauth.py — 配置加载模块

默认使用本地 ASR，无需 API Key。百炼/DashScope ASR 为可选项，仅在 config.json 中
asr.backend='bailian' 且提供 API Key 时启用。
所有非敏感配置从 config/config.json 读取；若文件不存在则使用内置默认值。
"""

import json
import os
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"
CONFIG_PATH = CONFIG_DIR / "config.json"


def load_config() -> dict:
    """
    加载配置文件，返回合并后的配置字典。
    若 config.json 不存在，使用默认本地 ASR 配置。

    返回结构：
    {
        "asr": {
            "model": "small",
            "device": "auto",
            "language": "zh",
            "condition_on_previous_text": True,
            "fp16": False
        },
        "output": {
            "dir": "output"
        }
    }
    """
    DEFAULT_CFG = {
        "asr": {
            "backend": "local",
            "model": "small",
            "device": "auto",
            "language": "zh",
            "condition_on_previous_text": True,
            "fp16": False,
            "_note": "backend: local(默认, 本地Whisper) 或 bailian(百炼/DashScope); model仅local生效"
        },
        "bailian": {
            "api_key": "",
            "model": "paraformer-v2",
            "base_url": "",
            "language_hints": ["zh", "en"],
            "disfluency_removal_enabled": False,
            "timestamp_alignment_enabled": True
        },
        "network": {
            "proxy": "",
            "_note": "HTTP 代理，如 http://127.0.0.1:7897；访问 YouTube 等受限站点时需要，留空则不使用代理"
        },
        "output": {
            "dir": "output"
        }
    }

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        cfg = {}

    # 以默认值兜底，避免缺少字段
    merged = DEFAULT_CFG.copy()
    for key, value in cfg.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value

    return merged


def save_config(cfg: dict):
    """保存配置文件。"""
    # 不保存运行时可能注入的临时字段
    save_cfg = {
        k: v
        for k, v in cfg.items()
        if k not in ("api_key", "api_key_source", "_from_cache", "error")
    }
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(save_cfg, f, ensure_ascii=False, indent=2)


def get_proxy(cfg: dict) -> str:
    """获取 HTTP 代理：配置 network.proxy 优先，其次环境变量 HTTPS_PROXY/HTTP_PROXY。"""
    proxy = ((cfg.get("network") or {}).get("proxy") or "").strip()
    if proxy:
        return proxy
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        val = (os.environ.get(var) or "").strip()
        if val:
            return val
    return ""


def proxies_dict(cfg: dict) -> dict | None:
    """返回供 requests 使用的 proxies 字典；无代理时返回 None。"""
    proxy = get_proxy(cfg)
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}
