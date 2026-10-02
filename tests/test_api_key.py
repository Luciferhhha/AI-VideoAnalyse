"""阶段十六增量测试：控制面板 API Key 管理（DPAPI 加密存取）。

覆盖：
- DPAPI 往返：密文不含明文，`unprotect` 能还原；
- `PUT /api-keys/mimo`：加密落盘 + 立即写回 `config.MIMO_API_KEY`（无需重启）、掩码与来源；
- 入参校验：空值 / 掩码值 → 400，缺字段 → 422，且失败不污染 config；
- `DELETE /api-keys/mimo`：删除密钥文件并回落到环境变量；
- **任何端点都不得回传明文**（含 `GET /settings`）；
- `load_into_config()` 优先级与坏文件容错。

隔离：`tests/conftest.py` 的 `client` fixture 已把 `key_service.KEY_FILE` 指向 tmp_path，
测试读不到 `data/secrets` 里的真实密钥文件；本文件内的用例再自行 monkeypatch
`config.MIMO_API_KEY`，保证起始状态确定。
"""

import pytest
from fastapi.testclient import TestClient

from app import config
from app.services import key_service

PLAIN = "sk-panel-test-abcdefghijklmnop"  # 测试用假 Key（非真实 Key）


@pytest.fixture(autouse=True)
def _reset_last_error():
    """避免坏文件用例留下的解密错误泄漏到其他测试的状态里。"""
    yield
    key_service._last_error = None


# ---------------------------------------------------------------- 底层加解密


def test_protect_unprotect_roundtrip() -> None:
    blob = key_service.protect(PLAIN)

    assert isinstance(blob, bytes) and blob
    assert PLAIN.encode() not in blob, "DPAPI 密文中不得出现明文"
    assert key_service.unprotect(blob) == PLAIN


def test_mask_hides_middle() -> None:
    assert key_service.mask("") == ""
    assert key_service.mask("sk-short") == "****"
    masked = key_service.mask(PLAIN)
    assert masked.startswith("sk-pan") and masked.endswith("mnop")
    assert masked.count("*") == 4
    assert PLAIN not in masked


# ---------------------------------------------------------------- 保存 / 查询


def test_save_writes_ciphertext_and_takes_effect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MIMO_API_KEY", "")

    body = client.put("/api-keys/mimo", json={"api_key": PLAIN}).json()

    assert body["configured"] is True
    assert body["source"] == "file"
    assert body["storage"] == "dpapi"
    assert body["masked"] and PLAIN not in str(body)
    assert body["updated_at"] is not None
    assert body["error"] is None

    raw = key_service.KEY_FILE.read_bytes()
    assert PLAIN.encode() not in raw, "磁盘上不得出现明文"
    assert key_service.unprotect(raw) == PLAIN  # 密文可还原
    assert config.MIMO_API_KEY == PLAIN  # 立即生效，无需重启
    assert client.get("/health").status_code == 200


def test_save_strips_quotes_and_whitespace(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MIMO_API_KEY", "")

    body = client.put(
        "/api-keys/mimo", json={"api_key": f'  "{PLAIN}"  '}
    ).json()

    assert body["configured"] is True
    assert config.MIMO_API_KEY == PLAIN
    assert key_service.unprotect(key_service.KEY_FILE.read_bytes()) == PLAIN


def test_put_rejects_empty_and_masked_without_side_effect(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MIMO_API_KEY", "")

    empty = client.put("/api-keys/mimo", json={"api_key": "   "})
    assert empty.status_code == 400
    assert "不能为空" in empty.json()["detail"]

    masked = client.put("/api-keys/mimo", json={"api_key": "sk-abc****1234"})
    assert masked.status_code == 400
    assert "掩码" in masked.json()["detail"]

    assert client.put("/api-keys/mimo", json={}).status_code == 422  # 缺字段
    too_long = client.put("/api-keys/mimo", json={"api_key": "x" * 513})
    assert too_long.status_code == 422  # 超长

    assert not key_service.KEY_FILE.exists(), "失败的保存不得留下文件"
    assert config.MIMO_API_KEY == ""  # config 未被污染


def test_get_status_and_settings_never_expose_plaintext(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MIMO_API_KEY", "")
    assert client.put("/api-keys/mimo", json={"api_key": PLAIN}).status_code == 200

    status_resp = client.get("/api-keys/mimo")
    assert status_resp.status_code == 200
    assert PLAIN not in status_resp.text, "状态端点不得回传明文"
    assert status_resp.json()["masked"] == key_service.mask(PLAIN)

    settings_resp = client.get("/settings")
    assert PLAIN not in settings_resp.text, "配置快照不得回传明文"
    mimo = settings_resp.json()["mimo"]
    assert mimo["api_key_configured"] is True
    assert mimo["api_key_masked"] == key_service.mask(PLAIN)
    assert mimo["api_key_source"] == "file"


# ---------------------------------------------------------------- 删除 / 回落


def test_delete_removes_file_and_clears_config(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MIMO_API_KEY", "")
    monkeypatch.delenv("MIMO_API_KEY", raising=False)  # 删除后回落值必须为空
    client.put("/api-keys/mimo", json={"api_key": PLAIN})
    assert key_service.KEY_FILE.exists()

    body = client.delete("/api-keys/mimo").json()

    assert body["configured"] is False
    assert body["source"] == "none"
    assert body["masked"] is None
    assert not key_service.KEY_FILE.exists()
    assert config.MIMO_API_KEY == ""


def test_delete_falls_back_to_environment_variable(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_key = "sk-env-fallback-value-123456"
    monkeypatch.setattr(config, "MIMO_API_KEY", "")
    monkeypatch.setenv("MIMO_API_KEY", env_key)
    client.put("/api-keys/mimo", json={"api_key": PLAIN})

    body = client.delete("/api-keys/mimo").json()

    assert body["configured"] is True
    assert body["source"] == "env", "删除面板密钥后回落到环境变量"
    assert config.MIMO_API_KEY == env_key
    assert env_key not in str(body)


def test_status_source_env_before_any_save(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "MIMO_API_KEY", "sk-env-only-value-abcdef")
    monkeypatch.setenv("MIMO_API_KEY", "sk-env-only-value-abcdef")

    body = client.get("/api-keys/mimo").json()

    assert body["configured"] is True
    assert body["source"] == "env"
    assert body["updated_at"] is None  # 还没在面板里保存过


# ---------------------------------------------------------------- 启动载入


def test_load_into_config_priority_and_bad_file(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "secrets" / "mimo_api_key.bin"
    monkeypatch.setattr(key_service, "KEY_FILE", target)

    # 1) 文件不存在 → 不覆盖环境变量里的值
    monkeypatch.setattr(config, "MIMO_API_KEY", "sk-from-env-abcdef")
    assert key_service.load_into_config() is False
    assert config.MIMO_API_KEY == "sk-from-env-abcdef"

    # 2) 文件存在 → 覆盖（文件优先级高于环境变量）
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(key_service.protect("sk-from-file-1234567890"))
    assert key_service.load_into_config() is True
    assert config.MIMO_API_KEY == "sk-from-file-1234567890"

    # 3) 坏文件 → 失败但保留原值，并把原因记进 status().error
    monkeypatch.setattr(config, "MIMO_API_KEY", "sk-keep-me-0000000000")
    target.write_bytes(b"garbage-not-dpapi")
    assert key_service.load_into_config() is False
    assert config.MIMO_API_KEY == "sk-keep-me-0000000000"
    assert key_service.status()["error"]
