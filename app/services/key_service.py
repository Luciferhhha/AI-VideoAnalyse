"""mimo API Key 的本地加密存取（控制面板「API Key 管理」）。

设计要点：
- **存储**：`data/secrets/mimo_api_key.bin`（.gitignore 已忽略），内容是 Windows DPAPI
  （`CryptProtectData`）按**当前 Windows 用户**加密后的密文。磁盘上没有明文，
  文件拷到别的账号/机器上解不开。诚实定位：本地静态加密/混淆，不是绝对安全。
- **显示**：端点只回掩码（形如 `sk-abcd****wxyz`）与来源，**永不回传明文**（也不提供查看明文的接口，
  要换就重新粘贴）。
- **生效**：`save()` / `clear()` 立即回写 `config.MIMO_API_KEY`（服务层都是运行时读该属性），
  改完无需重启；启动时 `load_into_config()` 把文件里的 Key 载入 config。
- **优先级**：文件（面板管理）> 环境变量 `MIMO_API_KEY` > 空。
- **测试隔离**：`KEY_FILE` 是模块属性，`tests/conftest.py` 的 `client` fixture 会把它指向
  tmp_path，测试永远读不到真实 Key 文件。
"""

from __future__ import annotations

import ctypes
import logging
import os
from ctypes import wintypes
from datetime import datetime
from pathlib import Path
from typing import Any

from app import config

logger = logging.getLogger(__name__)

# 模块属性（而非函数内引用），便于测试 monkeypatch 指向临时路径
KEY_FILE: Path = config.DATA_DIR / "secrets" / "mimo_api_key.bin"
STORAGE = "dpapi"
_CRYPTPROTECT_UI_FORBIDDEN = 0x1

_last_error: str | None = None


class KeyServiceError(Exception):
    """Key 存取失败（DPAPI 不可用、写盘失败、解密失败）。"""


class KeyValidationError(KeyServiceError):
    """Key 内容非法（空值、掩码值），路由层翻译为 400。"""


# ---------------------------------------------------------------- DPAPI


class _DataBlob(ctypes.Structure):
    _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_byte))]


def _require_windows() -> None:
    if os.name != "nt":
        raise KeyServiceError("DPAPI 仅在 Windows 上可用，无法加密存取 API Key")


def protect(plain: str) -> bytes:
    """明文 → 当前 Windows 用户可解的密文（DPAPI）。"""
    _require_windows()
    data = plain.encode("utf-8")
    buf = ctypes.create_string_buffer(data, len(data))  # 保持缓冲存活到调用结束
    src = _DataBlob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    dst = _DataBlob()
    ok = ctypes.windll.crypt32.CryptProtectData(
        ctypes.byref(src),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(dst),
    )
    if not ok:
        raise KeyServiceError(
            f"CryptProtectData 失败，错误码 {ctypes.windll.kernel32.GetLastError()}"
        )
    try:
        return ctypes.string_at(dst.pbData, dst.cbData)
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


def unprotect(blob: bytes) -> str:
    """DPAPI 密文 → 明文（换账号/机器时会失败）。"""
    _require_windows()
    buf = ctypes.create_string_buffer(blob, len(blob))
    src = _DataBlob(len(blob), ctypes.cast(buf, ctypes.POINTER(ctypes.c_byte)))
    dst = _DataBlob()
    ok = ctypes.windll.crypt32.CryptUnprotectData(
        ctypes.byref(src),
        None,
        None,
        None,
        None,
        _CRYPTPROTECT_UI_FORBIDDEN,
        ctypes.byref(dst),
    )
    if not ok:
        raise KeyServiceError(
            f"CryptUnprotectData 失败，错误码 {ctypes.windll.kernel32.GetLastError()}"
            "（密文可能来自其他 Windows 账号或已损坏）"
        )
    try:
        return ctypes.string_at(dst.pbData, dst.cbData).decode("utf-8")
    finally:
        ctypes.windll.kernel32.LocalFree(dst.pbData)


# ---------------------------------------------------------------- 查询


def mask(key: str) -> str:
    """`sk-abcdef****wxyz` 形态的展示掩码；过短只回 `****`。"""
    if not key:
        return ""
    if len(key) <= 12:
        return "****"
    return f"{key[:6]}****{key[-4:]}"


def _display_path(path: Path) -> str:
    try:
        return str(path.relative_to(config.BASE_DIR))
    except ValueError:
        return str(path)


def status() -> dict[str, Any]:
    """当前 Key 状态（只读，绝不含明文）。"""
    global _last_error
    key = config.MIMO_API_KEY or ""
    file_exists = KEY_FILE.exists()
    env_value = os.getenv("MIMO_API_KEY", "")
    if not key:
        source = "none"
    elif file_exists:
        source = "file"  # 文件优先于环境变量
    elif env_value:
        source = "env"
    else:
        source = "config"  # 运行中被改写（测试 monkeypatch 等）

    updated_at = None
    if file_exists:
        try:
            updated_at = datetime.fromtimestamp(
                KEY_FILE.stat().st_mtime
            ).strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            updated_at = None

    return {
        "configured": bool(key),
        "masked": mask(key) if key else None,
        "source": source,
        "storage": STORAGE,
        "storage_path": _display_path(KEY_FILE),
        "updated_at": updated_at,
        "error": _last_error,
    }


# ---------------------------------------------------------------- 写入


def save(api_key: str) -> dict[str, Any]:
    """加密落盘并立即写回 `config.MIMO_API_KEY`（保存后即可用，无需重启）。"""
    global _last_error
    key = (api_key or "").strip()
    if len(key) >= 2 and key[0] == key[-1] and key[0] in "\"'":
        key = key[1:-1].strip()  # 兼容带引号粘贴
    if not key:
        raise KeyValidationError("API Key 不能为空")
    if "****" in key:
        raise KeyValidationError("看起来是掩码值（含 ****），请粘贴完整 API Key")

    blob = protect(key)  # 加密失败 → KeyServiceError
    try:
        KEY_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = KEY_FILE.with_suffix(".tmp")
        tmp.write_bytes(blob)
        tmp.replace(KEY_FILE)  # 原子替换，避免写一半
    except OSError as exc:
        raise KeyServiceError(f"密钥文件写入失败：{exc}") from exc

    config.MIMO_API_KEY = key
    _last_error = None
    logger.info("API Key 已保存 source=file masked=%s", mask(key))
    return status()


def clear() -> dict[str, Any]:
    """删除密钥文件并清空面板管理的 Key（回落到环境变量 `MIMO_API_KEY`）。"""
    global _last_error
    try:
        KEY_FILE.unlink(missing_ok=True)
        KEY_FILE.with_suffix(".tmp").unlink(missing_ok=True)
    except OSError as exc:
        raise KeyServiceError(f"删除密钥文件失败：{exc}") from exc

    config.MIMO_API_KEY = os.getenv("MIMO_API_KEY", "")
    _last_error = None
    result = status()
    logger.warning("API Key 已清空 source=%s", result["source"])
    return result


def load_into_config() -> bool:
    """启动时把密钥文件里的 Key 载入 `config.MIMO_API_KEY`；失败则保留环境变量的值。"""
    global _last_error
    if not KEY_FILE.exists():
        return False
    try:
        plain = unprotect(KEY_FILE.read_bytes())
    except Exception as exc:  # noqa: BLE001 —— 解密失败不应让服务起不来
        _last_error = str(exc)
        logger.warning("API Key 文件读取失败：%s（继续使用环境变量中的值）", exc)
        return False
    if not plain:
        return False
    config.MIMO_API_KEY = plain
    _last_error = None
    logger.info("API Key 已从密钥文件载入 source=file masked=%s", mask(plain))
    return True
