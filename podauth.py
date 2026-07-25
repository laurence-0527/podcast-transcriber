"""
podauth.py — 配置加载模块（环境变量优先 + config.json 回退）

API Key 读取优先级：
  1. 进程环境变量 DASHSCOPE_API_KEY
  2. Windows 注册表环境变量（User 级 + Machine 级）
  3. config/config.json 中的 transcription.openai_api_key（向后兼容）

非敏感配置（model、base_url、output）始终从 config.json 读取。
"""

import os
import sys
import json
from pathlib import Path

CONFIG_DIR = Path(__file__).parent / "config"
CONFIG_PATH = CONFIG_DIR / "config.json"
EXAMPLE_PATH = CONFIG_DIR / "config.example.json"
ENV_KEY = "DASHSCOPE_API_KEY"


def _read_windows_envvar(name: str) -> str:
    """
    从 Windows 注册表读取持久化环境变量（User 级优先，Machine 级回退）。
    解决部分终端/沙箱环境未继承 Windows 系统环境变量的问题。
    """
    if sys.platform != "win32":
        return ""

    try:
        import winreg
        # User 级
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            if value:
                return str(value).strip()
    except (OSError, FileNotFoundError):
        pass

    try:
        import winreg
        # Machine 级
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment") as key:
            value, _ = winreg.QueryValueEx(key, name)
            if value:
                return str(value).strip()
    except (OSError, FileNotFoundError):
        pass

    return ""


def _resolve_api_key(cfg: dict) -> tuple[str, str]:
    """
    解析 API Key，返回 (api_key, source)。
    优先级：进程环境变量 > Windows 注册表 > config.json
    """
    # 1. 进程环境变量（最直接）
    env_key = os.environ.get(ENV_KEY, "").strip()
    if env_key:
        return env_key, "env"

    # 2. Windows 注册表环境变量（解决沙箱未继承的问题）
    if sys.platform == "win32":
        reg_key = _read_windows_envvar(ENV_KEY)
        if reg_key:
            return reg_key, "winreg"

    # 3. config.json 中的 transcription.openai_api_key
    file_key = cfg.get("transcription", {}).get("openai_api_key", "").strip()
    if file_key and file_key != "sk-xxxx":
        return file_key, "file"

    raise ValueError(
        f"未找到有效的 API Key！请选择以下任一方式配置：\n"
        f"  1. 设置环境变量: setx {ENV_KEY} sk-your-key-here\n"
        f"  2. 编辑 config/config.json，填入 transcription.openai_api_key"
    )


def load_config() -> dict:
    """
    加载配置文件，返回合并后的配置字典。
    若环境变量已提供 API Key，config.json 可缺省（使用默认配置）。

    返回结构：
    {
        "api_key": "sk-...",              # 已解析的 API Key
        "api_key_source": "env|winreg|file",  # Key 来源，便于调试
        "transcription": { ... },
        "summary": { ... },
        "output": { ... },
    }
    """
    DEFAULT_CFG = {
        "transcription": {
            "openai_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
        "summary": {
            "model": "qwen-plus",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        },
        "output": {
            "dir": "output",
        },
    }

    if CONFIG_PATH.exists():
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    else:
        env_key = os.environ.get(ENV_KEY, "").strip()
        winreg_key = _read_windows_envvar(ENV_KEY) if sys.platform == "win32" else ""
        if env_key or winreg_key:
            cfg = DEFAULT_CFG.copy()
        else:
            raise FileNotFoundError(
                f"配置文件不存在: {CONFIG_PATH}\n"
                f"请先复制 config/config.example.json 为 config/config.json\n"
                f"或设置环境变量 {ENV_KEY}"
            )

    api_key, api_key_source = _resolve_api_key(cfg)

    # 注入解析后的 api_key 到各子配置，让下游模块无需改动
    cfg.setdefault("transcription", {})["openai_api_key"] = api_key
    cfg.setdefault("summary", {})["api_key"] = api_key

    # 顶层也放一份，方便调试
    cfg["api_key"] = api_key
    cfg["api_key_source"] = api_key_source

    return cfg


def save_config(cfg: dict):
    """保存配置文件（保存时会移除运行时注入的 api_key，避免泄露）"""
    # 不把运行时注入的 api_key 写回文件
    save_cfg = {k: v for k, v in cfg.items() if k not in ("api_key", "api_key_source")}
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(save_cfg, f, ensure_ascii=False, indent=2)
