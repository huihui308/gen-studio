"""Tests for the API Keys management page and its credential store.

The image service is never contacted: these tests cover login protection,
the five write routes, the on-disk contract (api_keys.json under web/data,
mode 0600, atomic writes), the read-only usage file, the three access modes
and the Bearer header added to ImageClient.
"""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.apikeys import (
    ApiKeyError,
    ApiKeyStore,
    MODE_KEY,
    MODE_LOCKED,
    MODE_OPEN,
)
from app.imagegen import ImageClient

from conftest import csrf_token, login


def key_file(settings) -> Path:
    return settings.data_dir / "api_keys.json"


def usage_file(settings) -> Path:
    return settings.data_dir / "api_keys_usage.json"


def read_payload(settings) -> dict:
    return json.loads(key_file(settings).read_text(encoding="utf-8"))


def post(client, path: str, token: str, **data):
    data["csrf_token"] = token
    return client.post(path, data=data, follow_redirects=False)


# ------------------------------------------------------------ page protection


def test_apikeys_page_requires_login(make_app):
    client = TestClient(make_app())
    response = client.get("/apikeys", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")

    for path in (
        "/apikeys",
        "/apikeys/1/edit",
        "/apikeys/1/toggle",
        "/apikeys/1/reset",
        "/apikeys/1/delete",
    ):
        response = client.post(path, data={}, follow_redirects=False)
        assert response.status_code == 303, path
        assert response.headers["location"].startswith("/login"), path


def test_page_renders_title_base_urls_nav_and_csp_safety(client, monkeypatch):
    monkeypatch.setattr("app.main._lan_ip", lambda: "192.168.171.50")
    login(client)
    page = client.get("/apikeys")
    assert page.status_code == 200
    assert "<h1>API Keys</h1>" in page.text
    assert "集中管理生图服务的访问凭证，用于模型调用身份鉴权" in page.text
    # Nav entry sits after 图像生成 and is marked active.
    assert '<a href="/apikeys" class="nav-link is-active">API Keys</a>' in page.text
    # Render one row so the row-level controls are on the page.
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="首个密钥")
    page = client.get("/apikeys")
    # Two Base URL rows with copy buttons.
    assert "OpenAI 兼容 Base URL" in page.text
    assert "原生 Base URL" in page.text
    assert "http://192.168.171.50:30021/v1" in page.text
    assert 'id="base-native">http://192.168.171.50:30021<' in page.text
    assert page.text.count('data-copy-target=') == 2
    # Callers must send the key as a header; loopback stays key-less.
    assert "Authorization: Bearer" in page.text
    assert "X-API-Key" in page.text
    assert "0.0.0.0" in page.text
    # CSP: no inline styles or inline event handlers anywhere.
    assert 'style="' not in page.text
    assert " onclick=" not in page.text
    # Page stays usable without JavaScript.
    assert '<details class="key-edit">' in page.text
    assert "<summary>编辑</summary>" in page.text
    assert 'type="search" id="key-search"' in page.text


def test_page_falls_back_to_loopback_without_lan_ip(client, monkeypatch):
    monkeypatch.setattr("app.main._lan_ip", lambda: None)
    login(client)
    page = client.get("/apikeys")
    assert "http://127.0.0.1:30021/v1" in page.text
    assert "未能检测到局域网地址" in page.text


def test_page_falls_back_to_default_port(make_app, monkeypatch):
    monkeypatch.setattr("app.main._lan_ip", lambda: "192.168.171.50")
    app = make_app()
    app.state.image_client.base_url = "http://image-service.invalid"
    client = TestClient(app)
    login(client)
    page = client.get("/apikeys")
    assert "http://192.168.171.50:30021/v1" in page.text


# ------------------------------------------------------------- create / read


def test_create_key_writes_0600_and_shows_masked_value_with_data_key(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")

    response = post(client, "/apikeys", token, description="批处理任务")
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]

    path = key_file(settings)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    payload = read_payload(settings)
    assert payload["version"] == 1
    assert payload["next_id"] == 2
    entry = payload["keys"][0]
    assert entry["id"] == 1
    assert entry["enabled"] is True
    assert entry["description"] == "批处理任务"
    assert entry["key"].startswith("sk-")
    assert len(entry["key"]) == 3 + 48  # sk- + 48 hex characters
    datetime.fromisoformat(entry["created_at"])  # ISO with timezone

    page = client.get("/apikeys")
    masked = entry["key"][:6] + "****" + entry["key"][-4:]
    assert masked in page.text
    assert f'data-key="{entry["key"]}"' in page.text
    # The full secret is present exactly once (the copy attribute), never as text.
    assert page.text.count(entry["key"]) == 1
    assert "需 Key（1 个可用）" in page.text


def test_copy_button_finds_key_on_its_row(client, settings):
    """Regression: data-key must sit on an *ancestor* of the copy button.

    apikeys.js copies with button.closest("[data-key]"), which only walks
    ancestors. The attribute used to live on the sibling <span>, so closest
    returned null and the button silently did nothing (reported 2026-11-12).
    Keep it on the <tr>.
    """

    login(client)
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="复制回归")
    html = client.get("/apikeys").text
    entry = read_payload(settings)["keys"][0]

    rows = re.findall(r"<tr\b[^>]*>.*?</tr>", html, re.S)
    copy_rows = [row for row in rows if "data-copy-key" in row]
    assert copy_rows, "找不到含复制按钮的表格行"
    for row in copy_rows:
        opening_tag = row.split(">", 1)[0]
        assert f'data-key="{entry["key"]}"' in opening_tag, (
            "data-key 不在复制按钮的祖先元素上，closest 会返回 null，按钮点击将无反应"
        )


def test_create_key_allows_empty_description(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")
    response = post(client, "/apikeys", token, description="")
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]
    assert read_payload(settings)["keys"][0]["description"] == ""
    page = client.get("/apikeys")
    assert "未填写" in page.text


def test_create_rejects_overlong_description(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")
    response = post(client, "/apikeys", token, description="x" * 201)
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    assert not key_file(settings).exists()
    page = client.get(response.headers["location"])
    assert "描述最多 200 个字符" in page.text


def test_empty_state_is_explained(client):
    login(client)
    page = client.get("/apikeys")
    assert "尚未创建任何 API Key" in page.text


# ---------------------------------------------------------------------- edit


def test_edit_description_updates_file_and_page(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="旧描述")

    response = post(client, "/apikeys/1/edit", token, description="新描述")
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]
    assert read_payload(settings)["keys"][0]["description"] == "新描述"

    page = client.get("/apikeys")
    assert "新描述" in page.text
    assert "旧描述" not in page.text


def test_edit_can_clear_description(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="旧描述")
    response = post(client, "/apikeys/1/edit", token, description="")
    assert "notice=" in response.headers["location"]
    assert read_payload(settings)["keys"][0]["description"] == ""
    assert "未填写" in client.get("/apikeys").text


# -------------------------------------------------------------------- toggle


def test_toggle_flips_enabled_and_page_state(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="唯一密钥")

    page = client.get("/apikeys")
    assert 'key-toggle key-toggle-on' in page.text
    assert "需 Key（1 个可用）" in page.text

    response = post(client, "/apikeys/1/toggle", token)
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]
    assert read_payload(settings)["keys"][0]["enabled"] is False

    page = client.get("/apikeys")
    assert 'key-toggle key-toggle-off' in page.text
    assert "已停用" in page.text
    assert "已锁定（无可用 Key，仅本机回环可调用）" in page.text

    post(client, "/apikeys/1/toggle", token)
    assert read_payload(settings)["keys"][0]["enabled"] is True
    assert "需 Key（1 个可用）" in client.get("/apikeys").text


# --------------------------------------------------------------------- reset


def test_reset_replaces_secret_and_invalidates_old_value(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="会被重置")
    old_key = read_payload(settings)["keys"][0]["key"]

    response = post(client, "/apikeys/1/reset", token)
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]

    payload = read_payload(settings)
    entry = payload["keys"][0]
    assert entry["key"] != old_key
    assert entry["key"].startswith("sk-")
    assert len(entry["key"]) == 3 + 48
    assert entry["id"] == 1
    assert entry["description"] == "会被重置"

    page = client.get("/apikeys")
    assert old_key not in page.text
    assert f'data-key="{entry["key"]}"' in page.text


# -------------------------------------------------------------------- delete


def test_delete_syncs_list_file_and_keeps_ids_monotonic(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="第一个")
    post(client, "/apikeys", token, description="第二个")
    first_key = read_payload(settings)["keys"][0]["key"]
    second_key = read_payload(settings)["keys"][1]["key"]

    response = post(client, "/apikeys/1/delete", token)
    assert response.status_code == 303
    assert "notice=" in response.headers["location"]

    payload = read_payload(settings)
    assert [item["id"] for item in payload["keys"]] == [2]
    assert payload["keys"][0]["key"] == second_key

    page = client.get("/apikeys")
    assert first_key not in page.text
    assert f'data-key="{second_key}"' in page.text

    # Deleted ids are never reused.
    post(client, "/apikeys", token, description="第三个")
    assert [item["id"] for item in read_payload(settings)["keys"]] == [2, 3]


# ------------------------------------------------------- GET fallbacks / CSRF


@pytest.mark.parametrize("action", ["edit", "toggle", "reset", "delete"])
def test_direct_get_to_write_route_is_a_friendly_redirect(client, settings, action):
    login(client)
    response = client.get(f"/apikeys/1/{action}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/apikeys?error=")
    page = client.get(response.headers["location"])
    assert "直接访问不会" in page.text
    assert "POST" in page.text
    # Nothing was created or changed by the GET.
    assert not key_file(settings).exists()


def test_write_routes_reject_forged_csrf(client, settings):
    login(client)
    for path, data in (
        ("/apikeys", {"description": "x"}),
        ("/apikeys/1/edit", {"description": "x"}),
        ("/apikeys/1/toggle", {}),
        ("/apikeys/1/reset", {}),
        ("/apikeys/1/delete", {}),
    ):
        data = {**data, "csrf_token": "forged"}
        response = client.post(path, data=data, follow_redirects=False)
        assert response.status_code == 303, path
        assert "error=" in response.headers["location"], path
        page = client.get(response.headers["location"])
        assert "会话校验失败" in page.text, path
    assert not key_file(settings).exists()


def test_write_route_for_unknown_id_reports_error(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="唯一")
    response = post(client, "/apikeys/999/edit", token, description="nope")
    assert response.status_code == 303
    assert "error=" in response.headers["location"]
    page = client.get(response.headers["location"])
    assert "API Key #999 不存在" in page.text
    assert read_payload(settings)["keys"][0]["description"] == "唯一"


# -------------------------------------------------------- usage (read-only)


def test_usage_file_renders_calls_and_last_used(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="统计")
    used_at = "2026-11-11T10:30:00+08:00"
    usage_file(settings).write_text(
        json.dumps(
            {"version": 1, "usage": {"1": {"calls": 7, "last_used_at": used_at}}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    page = client.get("/apikeys")
    assert "7 次" in page.text
    expected = datetime.fromisoformat(used_at).astimezone().strftime("%Y-%m-%d %H:%M:%S")
    assert expected in page.text
    # The console never writes the usage file itself.
    assert usage_file(settings).read_text(encoding="utf-8").count("calls") == 1


def test_missing_usage_file_shows_zero_and_dash(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="无统计")
    page = client.get("/apikeys")
    assert "0 次" in page.text


def test_broken_usage_file_is_ignored_silently(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="坏统计")
    usage_file(settings).write_text("{not json", encoding="utf-8")

    page = client.get("/apikeys")
    assert page.status_code == 200
    assert "0 次" in page.text
    assert "配置文件异常" not in page.text  # the warning bar is for the key file


# --------------------------------------------------------------- access modes


def test_access_mode_labels_follow_the_contract(client, settings):
    login(client)
    token = csrf_token(client, "/apikeys")

    # (1) file missing -> open
    page = client.get("/apikeys")
    assert "开放（未配置 API Key，局域网免凭证可调用）" in page.text
    assert "规则：" in page.text

    # (2) >=1 enabled -> needs a key
    post(client, "/apikeys", token, description="模式")
    page = client.get("/apikeys")
    assert "需 Key（1 个可用）" in page.text

    # (3) 0 enabled -> locked
    post(client, "/apikeys/1/toggle", token)
    page = client.get("/apikeys")
    assert "已锁定（无可用 Key，仅本机回环可调用）" in page.text


def test_corrupt_key_file_shows_warning_and_create_repairs_it(client, settings):
    login(client)
    key_file(settings).write_text("{not json", encoding="utf-8")

    page = client.get("/apikeys")
    assert page.status_code == 200
    assert "API Key 配置文件异常" in page.text
    assert "已锁定" in page.text  # exists but nothing usable

    token = csrf_token(client, "/apikeys")
    response = post(client, "/apikeys", token, description="修复")
    assert "notice=" in response.headers["location"]
    page = client.get("/apikeys")
    assert "API Key 配置文件异常" not in page.text
    assert read_payload(settings)["keys"][0]["description"] == "修复"


def test_static_assets_are_served(client):
    login(client)
    script = client.get("/static/apikeys.js")
    assert script.status_code == 200
    assert "execCommand" in script.text
    assert "data-copy-key" in script.text
    css = client.get("/static/app.css")
    assert css.status_code == 200
    assert ".key-toggle" in css.text
    assert ".copy-helper" in css.text


# ------------------------------------------------------------- store unit tests


def test_store_next_id_increments_across_reloads(tmp_path):
    path = tmp_path / "api_keys.json"
    store = ApiKeyStore(path)
    first = store.create("a")
    second = store.create("b")
    assert (first.id, second.id) == (1, 2)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["next_id"] == 3
    assert oct(path.stat().st_mode & 0o777) == "0o600"

    reloaded = ApiKeyStore(path)
    third = reloaded.create("c")
    assert third.id == 3
    assert [item.id for item in reloaded.list()] == [1, 2, 3]


def test_store_missing_file_means_open_mode(tmp_path):
    store = ApiKeyStore(tmp_path / "api_keys.json")
    assert store.list() == []
    assert store.error is None
    assert store.access_mode() == (MODE_OPEN, 0)
    assert store.first_enabled_key() is None


def test_store_corrupt_file_does_not_explode(tmp_path):
    path = tmp_path / "api_keys.json"
    path.write_text("{not json", encoding="utf-8")
    store = ApiKeyStore(path)

    assert store.list() == []
    assert store.error is not None
    assert store.access_mode() == (MODE_LOCKED, 0)
    assert store.first_enabled_key() is None
    # A successful write rewrites valid content and clears the error.
    created = store.create("修复")
    assert created.id == 1
    assert store.error is None
    assert len(store.list()) == 1


def test_store_first_enabled_key_picks_lowest_enabled_id(tmp_path):
    path = tmp_path / "api_keys.json"
    store = ApiKeyStore(path)
    first = store.create("a")
    second = store.create("b")
    assert store.first_enabled_key() == first.key

    store.toggle(first.id)
    assert store.first_enabled_key() == second.key

    store.toggle(second.id)
    assert store.first_enabled_key() is None


def test_store_rejects_overlong_description(tmp_path):
    store = ApiKeyStore(tmp_path / "api_keys.json")
    with pytest.raises(ApiKeyError):
        store.create("x" * 201)
    assert not (tmp_path / "api_keys.json").exists()


def test_store_usage_is_silent_when_unreadable(tmp_path):
    store = ApiKeyStore(tmp_path / "api_keys.json", tmp_path / "usage.json")
    assert store.usage() == {}

    (tmp_path / "usage.json").write_text("{broken", encoding="utf-8")
    assert store.usage() == {}

    (tmp_path / "usage.json").write_text(
        json.dumps(
            {
                "version": 1,
                "usage": {
                    "1": {"calls": 3, "last_used_at": "2026-11-11T10:30:00+08:00"},
                    "bad": {"calls": 1},
                    "2": "not-a-dict",
                },
            }
        ),
        encoding="utf-8",
    )
    usage = store.usage()
    assert usage[1]["calls"] == 3
    assert usage[1]["last_used_at"] == "2026-11-11T10:30:00+08:00"
    # Malformed entries (non-numeric id, non-object body) are skipped.
    assert 2 not in usage
    assert all(isinstance(key, int) for key in usage)


# ------------------------------------------------------------- ImageClient auth


class _FakeResponse:
    """Minimal context manager standing in for urlopen's response."""

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self) -> bytes:
        return json.dumps(
            {
                "ok": True,
                "images": [{"filename": "out.png", "subfolder": "", "width": 1024, "height": 1024}],
                "elapsed_s": 1.0,
            }
        ).encode("utf-8")


def test_image_client_sends_bearer_header_from_provider(monkeypatch):
    captured: dict = {}

    def fake_urlopen(request, timeout=None):
        captured["request"] = request
        return _FakeResponse()

    monkeypatch.setattr("app.imagegen.urlopen", fake_urlopen)
    secret = "sk-" + "ab" * 24
    client = ImageClient("http://127.0.0.1:30021", api_key_provider=lambda: secret)

    result = client.generate("一只猫", 1024)
    assert result["image"]["filename"] == "out.png"
    request = captured["request"]
    assert request.get_header("Authorization") == f"Bearer {secret}"


def test_image_client_omits_header_without_or_with_empty_key(monkeypatch):
    captured: list = []

    def fake_urlopen(request, timeout=None):
        captured.append(request)
        return _FakeResponse()

    monkeypatch.setattr("app.imagegen.urlopen", fake_urlopen)

    # Default constructor: behaviour identical to the pre-existing client.
    ImageClient("http://127.0.0.1:30021").generate("x", 1024)
    assert captured[-1].get_header("Authorization") is None

    # Provider returning an empty value means "no credential".
    ImageClient("http://127.0.0.1:30021", api_key_provider=lambda: "").generate("x", 1024)
    assert captured[-1].get_header("Authorization") is None


def test_console_client_provider_uses_lowest_enabled_key(make_app, settings):
    app = make_app()
    client = TestClient(app)
    login(client)
    token = csrf_token(client, "/apikeys")
    post(client, "/apikeys", token, description="第一个")
    post(client, "/apikeys", token, description="第二个")

    provider = app.state.image_client.api_key_provider
    assert provider is not None
    keys = app.state.api_key_store.list()
    assert provider() == keys[0].key

    post(client, "/apikeys/1/toggle", token)
    keys = app.state.api_key_store.list()
    assert provider() == keys[1].key
