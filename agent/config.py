# -*- coding: utf-8 -*-
"""Agent 配置 — 从 YAML 加载，环境变量覆盖

凭据/连接参数的唯一业务来源是 config/config.yaml（或 DB_* / DEEPSEEK_* 环境变量）。
本文件模块级默认值仅用于 YAML 缺失/漏 key 时的兜底，并在缺失时打印可诊断日志；
业务代码（llm.py、sql/risk/report 工具）一律经 get_llm_config() / get_database_config()
读取，禁止再内联真实凭据字面量。
"""

import os


# ============================================================
# 默认值（YAML 缺失时的兜底；真实值以 config/config.yaml 为准）
# ============================================================
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com/v1"
DEEPSEEK_MODEL = "deepseek-chat"
MAX_STEPS = 8

DB_HOST = "localhost"
DB_PORT = 3306
DB_NAME = "finance_data"
DB_USER = "root"
DB_PASSWORD = os.environ.get("DB_PASSWORD", "")


# ============================================================
# YAML 配置加载器
# ============================================================

class ConfigLoader:
    """
    RQagentPro 风格配置加载器
    支持 YAML + dot-notation 访问 + 环境变量覆盖
    """

    def __init__(self, config_dir: str = None):
        if config_dir is None:
            # 默认路径: codex/config/
            config_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config"
            )
        self.config_dir = config_dir
        self._config: dict = {}
        self._loaded = False
        self.load_error: str = ""  # 最近一次加载失败原因（空串=成功），供诊断

    def load(self) -> dict:
        """加载配置并返回；缺失/解析失败打印可诊断错误并回退默认值。"""
        if self._loaded:
            return self._config

        import yaml
        config_path = os.path.join(self.config_dir, "config.yaml")

        try:
            with open(config_path, "r", encoding="utf-8") as f:
                parsed = yaml.safe_load(f)
            self._config = parsed or {}
            self._loaded = True
            self.load_error = ""
            print(f"[Config] 已加载配置: {config_path}")
        except FileNotFoundError:
            self._config = {}
            self._loaded = True
            self.load_error = f"配置文件不存在: {config_path}"
            print(f"[Config] 错误: {self.load_error}")
            print("[Config] 已回退到内置默认值。若连接不上真实服务，请先补全该文件，"
                  "或通过环境变量 DB_HOST/DB_PORT/DB_NAME/DB_USER/DB_PASSWORD/"
                  "DEEPSEEK_API_KEY/DEEPSEEK_MODEL 覆盖后重启。")
        except Exception as e:
            self._config = {}
            self._loaded = True
            self.load_error = f"配置文件加载失败: {config_path}: {e}"
            print(f"[Config] 错误: {self.load_error}")
            print("[Config] 已回退到内置默认值，请修正配置后重启。")

        # 环境变量覆盖
        self._apply_env_overrides()

        return self._config

    def _apply_env_overrides(self):
        """环境变量覆盖 YAML 配置"""
        overrides = {
            "DEEPSEEK_API_KEY": "llm.api_key",
            "DEEPSEEK_MODEL": "llm.model",
            "DB_HOST": "database.host",
            "DB_PORT": "database.port",
            "DB_NAME": "database.database",
            "DB_USER": "database.user",
            "DB_PASSWORD": "database.password",
            "PORT": "server.port",
            "DEBUG": "server.debug",
        }
        for env_var, config_path in overrides.items():
            val = os.environ.get(env_var)
            if val:
                self.set(config_path, val)

    def get(self, key: str, default=None):
        """
        dot-notation 访问配置
        例: cfg.get("llm.model") → "deepseek-chat"
        """
        if not self._loaded:
            self.load()

        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        return value

    def set(self, key: str, value):
        """设置配置值"""
        if not self._loaded:
            self.load()

        keys = key.split(".")
        target = self._config
        for k in keys[:-1]:
            if k not in target:
                target[k] = {}
            target = target[k]
        target[keys[-1]] = value


# ============================================================
# 全局单例 + 业务读取入口
# ============================================================
_config_loader: ConfigLoader = None
_warned_keys: set = set()


def get_config() -> ConfigLoader:
    """获取全局配置加载器"""
    global _config_loader
    if _config_loader is None:
        _config_loader = ConfigLoader()
    return _config_loader


def _warn_missing(keys) -> None:
    """打印缺失 key 诊断（同 key 进程内只提醒一次，避免刷屏）。"""
    for key in keys:
        if key in _warned_keys:
            continue
        _warned_keys.add(key)
        print(f"[Config] 警告: 配置缺少 {key}，已回退到内置默认值。"
              "请检查 config/config.yaml 对应字段或用环境变量覆盖；改配置无需改代码。")


def get_llm_config() -> dict:
    """LLM 连接配置：YAML -> 环境变量 -> 内置默认值（key 缺失时打印诊断日志）。"""
    cfg = get_config()
    cfg.load()
    mapping = (
        ("api_key", "llm.api_key", DEEPSEEK_API_KEY),
        ("base_url", "llm.api_url", DEEPSEEK_BASE_URL),
        ("model", "llm.model", DEEPSEEK_MODEL),
    )
    result, missing = {}, []
    for field, key, default in mapping:
        value = cfg.get(key)
        if value is None:
            missing.append(key)
            value = default
        result[field] = value
    if missing:
        _warn_missing(missing)
    return result


def get_database_config() -> dict:
    """数据库连接配置：YAML -> 环境变量 -> 内置默认值（key 缺失时打印诊断日志）。"""
    cfg = get_config()
    cfg.load()
    mapping = (
        ("host", "database.host", DB_HOST),
        ("port", "database.port", DB_PORT),
        ("database", "database.database", DB_NAME),
        ("user", "database.user", DB_USER),
        ("password", "database.password", DB_PASSWORD),
    )
    result, missing = {}, []
    for field, key, default in mapping:
        value = cfg.get(key)
        if value is None:
            missing.append(key)
            value = default
        if field == "port":
            value = int(value)
        result[field] = value
    if missing:
        _warn_missing(missing)
    return result


# ============================================================
# 模块初始化：用 YAML/环境变量覆盖模块级默认常量
# ============================================================
def _init_from_yaml():
    global DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL, MAX_STEPS
    global DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD
    cfg = get_config()
    cfg.load()

    llm = get_llm_config()
    DEEPSEEK_API_KEY = llm["api_key"]
    DEEPSEEK_BASE_URL = llm["base_url"]
    DEEPSEEK_MODEL = llm["model"]

    db = get_database_config()
    DB_HOST = db["host"]
    DB_PORT = db["port"]
    DB_NAME = db["database"]
    DB_USER = db["user"]
    DB_PASSWORD = db["password"]

    max_iter = cfg.get("agent.max_iterations")
    if max_iter is None:
        _warn_missing(["agent.max_iterations"])
    else:
        MAX_STEPS = int(max_iter)


# 模块导入即初始化：让 get_config() 真正从 YAML 读到配置
_init_from_yaml()
