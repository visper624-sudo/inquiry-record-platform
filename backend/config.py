# -*- coding: utf-8 -*-
"""全局配置：路径、常量、角色与阶段口径。

所有路径均相对项目根目录解析，便于整体拷贝部署。
"""
from __future__ import annotations

import os
from pathlib import Path

# ---------------------------------------------------------------- 路径
BASE_DIR = Path(__file__).resolve().parent.parent
BACKEND_DIR = BASE_DIR / "backend"
FRONTEND_DIR = BASE_DIR / "frontend"
DATA_DIR = BASE_DIR / "data"
TEMPLATE_DIR = DATA_DIR / "templates"
DEMO_DIR = DATA_DIR / "demo"
STORAGE_DIR = BASE_DIR / "storage"
DB_PATH = Path(os.environ.get("PRACTICE_DB") or (STORAGE_DIR / "app.db"))
MATERIAL_DIR = STORAGE_DIR / "materials"
EXPORT_DIR = STORAGE_DIR / "exports"
LOG_DIR = STORAGE_DIR / "logs"

DB_PATH.parent.mkdir(parents=True, exist_ok=True)
for _p in (STORAGE_DIR, MATERIAL_DIR, EXPORT_DIR, LOG_DIR):
    _p.mkdir(parents=True, exist_ok=True)

# ---------------------------------------------------------------- 运行参数
HOST = os.environ.get("PRACTICE_HOST", "127.0.0.1")
PORT = int(os.environ.get("PRACTICE_PORT", "8765"))
SESSION_TTL_HOURS = int(os.environ.get("PRACTICE_SESSION_TTL", "12"))
MAX_UPLOAD_BYTES = 20 * 1024 * 1024
ALLOWED_MATERIAL_EXT = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".pdf", ".txt", ".docx", ".xlsx", ".csv", ".mp4", ".mp3",
}
# 截图/演示专用登录入口（仅 --demo 启动时开放；且只允许绑定回环地址）
DEMO_MODE = os.environ.get("PRACTICE_DEMO", "0") == "1"

# ---------------------------------------------------------------- 安全参数
# Cookie 是否加 Secure（生产环境经 HTTPS 反代时置 1）
COOKIE_SECURE = os.environ.get("PRACTICE_COOKIE_SECURE", "0") == "1"
# 可信反向代理 IP 列表（只有来自这些直连地址的 X-Forwarded-For 才被采信）
TRUSTED_PROXIES = {
    ip.strip() for ip in os.environ.get("PRACTICE_TRUSTED_PROXIES", "").split(",") if ip.strip()
}
# 并发连接上限与请求读取超时（防慢连接耗尽线程）
MAX_CONNECTIONS = int(os.environ.get("PRACTICE_MAX_CONNECTIONS", "64"))
REQUEST_TIMEOUT_SEC = int(os.environ.get("PRACTICE_REQUEST_TIMEOUT", "30"))
# 登录失败锁定策略
LOGIN_MAX_FAILURES = int(os.environ.get("PRACTICE_LOGIN_MAX_FAILURES", "5"))
LOGIN_LOCK_SECONDS = int(os.environ.get("PRACTICE_LOGIN_LOCK_SECONDS", "900"))
# 大模型：默认禁止访问本机/内网地址；自定义 OpenAI 兼容端点需显式声明域名
LLM_ALLOW_PRIVATE = os.environ.get("PRACTICE_LLM_ALLOW_PRIVATE", "0") == "1"
LLM_ALLOWED_HOSTS = {
    h.strip().lower() for h in os.environ.get("PRACTICE_LLM_ALLOWED_HOSTS", "").split(",") if h.strip()
}

# ---------------------------------------------------------------- 业务常量
ROLES = {
    "police": "办案民警",
    "analyst": "反诈研判员",
    "admin": "系统管理员",
}

STAGES = ["intake", "templates", "inquiry", "completed"]
STAGE_LABELS = {
    "intake": "接报录入",
    "templates": "模板选择",
    "inquiry": "问询进行中",
    "completed": "已完成问询",
}
STAGE_NEXT = {"intake": "templates", "templates": "inquiry", "inquiry": "completed", "completed": "completed"}

STATUS_LABELS = {
    "intake": "未问询",
    "templates": "未问询",
    "inquiry": "问询中",
    "completed": "已问询",
}

SECTIONS = [
    {"key": "opening", "title": "固定开头"},
    {"key": "contact", "title": "接触引流"},
    {"key": "process", "title": "被骗经过"},
    {"key": "loss", "title": "资金损失"},
    {"key": "evidence", "title": "证据补充"},
]
SECTION_TITLE = {s["key"]: s["title"] for s in SECTIONS}

CASE_CATEGORIES = [
    "冒充电商物流客服", "网络婚恋交友", "虚假网络投资理财", "虚假征信", "刷单返利",
    "冒充公检法", "冒充领导熟人", "网络游戏虚假交易", "虚假购物服务", "虚假网络贷款",
    "冒充军警采购", "虚假中奖", "机票退改签", "虚拟货币投资", "冒充客服解除扣费",
    "裸聊敲诈勒索", "养老诈骗", "校园贷培训贷", "网络招嫖涉黄", "其他",
]

FLOW_KEYS = ["fund", "communication", "network", "personnel", "delivery"]
FLOW_LABELS = {
    "fund": "资金流",
    "communication": "通信流",
    "network": "网络流",
    "personnel": "人员流",
    "delivery": "寄递流",
}
FLOW_STATUS_LABELS = {
    "collected": "已收集",
    "needs_supplement": "需补充",
    "key_gap": "重点缺口",
    "not_involved": "不涉及",
}

# 文书排版参数（公安红头问询笔录口径）
DOC_LAYOUT = {
    "page": {"top": 3.7, "bottom": 3.5, "left": 2.8, "right": 2.6},  # cm
    "header_font": "方正小标宋简体",
    "header_fallback": "宋体",
    "header_size": 22,      # 二号
    "body_font": "仿宋_GB2312",
    "body_fallback": "仿宋",
    "body_size": 16,        # 三号
    "line_spacing": 30,     # 磅
    "title_spacing": 24,
}

# LLM 适配层默认参数（可被设置页覆盖）
# DeepSeek 为默认提供方：真实调用大模型；未配置密钥或调用失败时自动回退规则引擎。
# 密钥只保存在用户目录（不随项目分发），也可通过环境变量 PRACTICE_LLM_API_KEY 注入。
LLM_KEY_FILE = Path(
    os.environ.get("PRACTICE_LLM_KEY_FILE") or (Path.home() / ".practice-2.11" / "llm_key.txt")
)
try:
    LLM_KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
DEFAULT_LLM_CONFIG = {
    "provider": "deepseek",          # deepseek / openai_compatible / ollama / rule_engine
    "model": "deepseek-chat",
    "base_url": "https://api.deepseek.com",
    "temperature": 0.2,
    "max_tokens": 900,
    "timeout_sec": 40,
    "enabled": True,                 # 有可用密钥时走大模型，否则规则引擎兜底
}

ACTION_LABELS = {
    "login": "登录",
    "logout": "登出",
    "record.create": "创建笔录",
    "record.sync_external": "同步外部案件",
    "record.stage": "阶段流转",
    "record.save": "暂存笔录",
    "record.complete": "完成问询",
    "record.template_select": "选定模板",
    "qa.create": "新增问答",
    "qa.update": "修改问答",
    "qa.delete": "删除问答",
    "advice.adopt": "采纳AI建议",
    "ai.analyze": "AI深度研判",
    "material.upload": "上传材料",
    "material.delete": "删除材料",
    "document.export": "导出文书",
    "import.parse": "解析历史笔录",
    "import.commit": "导入转存",
    "setting.update": "修改配置",
    "setting.llm_test": "模型连通性测试",
}
