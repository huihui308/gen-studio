"""Tests for the image-generation page and its proxy endpoint.

The upstream Qwen-Image service is never called: ``app.state.image_client`` is
replaced with a deterministic double, so these tests cover validation, CSRF,
multipart upload and error mapping only.
"""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient

from app.imagegen import (
    ImageServiceError,
    ImageValidationError,
    preview_size,
    resolve_size,
    snap_resolution,
    sniff_image,
    validate_request,
    validate_upload,
)

from conftest import csrf_token, login

PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)

RESULT = {
    "image": {
        "filename": "i2i-42_00001_.png",
        "subfolder": "qwen-image-2.1-turbo",
        "width": 1024,
        "height": 1024,
        "b64": base64.b64encode(PNG_1PX).decode(),
    },
    "elapsed_s": 41.2,
    "seed": 42,
    "resolution": 1024,
}


class FakeImageClient:
    """Records calls and returns a canned result or raises."""

    base_url = "http://127.0.0.1:30021"

    def __init__(self, result: dict | None = None, error: Exception | None = None) -> None:
        self.result = result or RESULT
        self.error = error
        self.calls: list[dict] = []
        self.healthy_state = True

    def healthy(self) -> bool:
        return self.healthy_state

    def generate(self, prompt, resolution, *, seed=None, upload=None,
                 width=None, height=None):
        self.calls.append(
            {
                "prompt": prompt,
                "resolution": resolution,
                "seed": seed,
                "upload": upload,
                "width": width,
                "height": height,
            }
        )
        if self.error is not None:
            raise self.error
        return self.result


@pytest.fixture()
def image_client(make_app):
    app = make_app()
    fake = FakeImageClient()
    app.state.image_client = fake
    return app, fake


def _client_with(app) -> TestClient:
    return TestClient(app)


# ------------------------------------------------------------------ page


def test_page_allows_data_and_blob_images_in_csp(image_client):
    """The preview uses a data: URL; blob: is kept for the thumbnail/download path.

    Regression: img-src previously only allowed 'self' data:, so a blob: preview
    was blocked by the browser and the result showed a broken image.
    """

    app, fake = image_client
    client = _client_with(app)
    login(client)
    page = client.get("/image")
    csp = page.headers["content-security-policy"]
    assert "img-src 'self' data: blob:" in csp


def test_image_page_requires_login(make_app):
    client = TestClient(make_app())
    response = client.get("/image", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_image_page_renders_controls(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    page = client.get("/image")
    assert page.status_code == 200
    assert 'id="image-form"' in page.text
    assert 'id="reference"' in page.text
    assert 'id="resolution"' in page.text
    assert 'id="aspect"' in page.text
    assert '<option value="1080" >' in page.text or '<option value="1080">' in page.text
    assert 'id="size-preview"' in page.text
    assert '<option value="16:9">16:9</option>' in page.text
    assert "/image" in page.text  # nav entry is active


def test_result_image_supports_click_to_zoom(image_client):
    """The result image is a button that opens a full-screen preview."""

    app, fake = image_client
    client = _client_with(app)
    login(client)
    page = client.get("/image").text
    assert 'id="result-image"' in page
    assert 'id="lightbox"' in page
    assert 'id="lightbox-image"' in page
    assert 'id="lightbox-close"' in page
    assert 'aria-label="生成结果，点击放大预览"' in page


# ------------------------------------------------------------ validation


def test_missing_prompt_rejected(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    response = client.post(
        "/api/image/generate", data={"csrf_token": token, "resolution": "1024"}
    )
    assert response.status_code == 400
    assert "提示词" in response.json()["error"]
    assert fake.calls == []


def test_bad_resolution_rejected(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    response = client.post(
        "/api/image/generate",
        data={"csrf_token": token, "prompt": "x", "resolution": "999"},
    )
    assert response.status_code == 400
    assert "分辨率" in response.json()["error"]
    assert fake.calls == []


def test_missing_csrf_rejected(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    response = client.post(
        "/api/image/generate",
        data={"csrf_token": "wrong", "prompt": "x", "resolution": "1024"},
    )
    assert response.status_code == 403
    assert fake.calls == []


def test_non_image_upload_rejected(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    response = client.post(
        "/api/image/generate",
        data={"csrf_token": token, "prompt": "x", "resolution": "1024"},
        files={"file": ("notes.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 400
    assert "图片格式" in response.json()["error"]
    assert fake.calls == []


def test_empty_upload_rejected(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    response = client.post(
        "/api/image/generate",
        data={"csrf_token": token, "prompt": "x", "resolution": "1024"},
        files={"file": ("empty.png", b"", "image/png")},
    )
    assert response.status_code == 400
    assert fake.calls == []


# -------------------------------------------------------------- happy path


def test_text_to_image_returns_preview_payload(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    response = client.post(
        "/api/image/generate",
        data={
            "csrf_token": token,
            "prompt": "一只橘猫",
            "resolution": "1024",
            "seed": "42",
        },
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert payload["image"]["filename"] == "i2i-42_00001_.png"
    assert payload["image"]["b64"]
    assert payload["elapsed_s"] == 41.2
    call = fake.calls[0]
    assert call["prompt"] == "一只橘猫"
    assert call["resolution"] == 1024
    assert call["seed"] == 42
    assert call["upload"] is None


def test_aspect_ratio_is_resolved_into_width_height(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    response = client.post(
        "/api/image/generate",
        data={
            "csrf_token": token,
            "prompt": "一只橘猫",
            "resolution": "1024",
            "aspect": "16:9",
        },
    )
    assert response.status_code == 200
    call = fake.calls[0]
    assert (call["width"], call["height"]) == (1376, 768)


def test_auto_aspect_sends_no_explicit_size(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    client.post(
        "/api/image/generate",
        data={"csrf_token": token, "prompt": "x", "resolution": "1024", "aspect": ""},
    )
    call = fake.calls[0]
    assert call["width"] is None and call["height"] is None


def test_bad_aspect_rejected(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    response = client.post(
        "/api/image/generate",
        data={"csrf_token": token, "prompt": "x", "resolution": "1024", "aspect": "7:5"},
    )
    assert response.status_code == 400
    assert "宽高比" in response.json()["error"]
    assert fake.calls == []


def test_reference_upload_reaches_client_as_multipart(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    response = client.post(
        "/api/image/generate",
        data={
            "csrf_token": token,
            "prompt": "把 <image1> 变成水彩风",
            "resolution": "1328",
        },
        files={"file": ("ref.png", PNG_1PX, "image/png")},
    )
    assert response.status_code == 200
    call = fake.calls[0]
    # 1328 is not a multiple of 32: the console snaps it to the service grid.
    assert call["resolution"] == 1344
    assert call["upload"] is not None
    filename, data = call["upload"]
    assert filename == "reference.png"
    assert data == PNG_1PX


# ---------------------------------------------------------- error mapping


def test_upstream_error_maps_to_502(image_client):
    app, fake = image_client
    fake.error = ImageServiceError("生图失败：参考图过多")
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    response = client.post(
        "/api/image/generate",
        data={"csrf_token": token, "prompt": "x", "resolution": "1024"},
    )
    assert response.status_code == 502
    assert "参考图过多" in response.json()["error"]


def test_upstream_timeout_maps_to_504(image_client):
    app, fake = image_client
    fake.error = ImageServiceError("等待生图超时（600 秒）", status_code=504)
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    response = client.post(
        "/api/image/generate",
        data={"csrf_token": token, "prompt": "x", "resolution": "1024"},
    )
    assert response.status_code == 504
    assert "超时" in response.json()["error"]


# ------------------------------------------------------- helper behaviour


@pytest.mark.parametrize(
    ("prompt", "resolution", "seed", "expected_seed"),
    [
        ("ok", "1024", "", None),
        ("ok", "768", "7", 7),
    ],
)
def test_validate_request_defaults(prompt, resolution, seed, expected_seed):
    _, clean_resolution, clean_seed = validate_request(prompt, resolution, seed)
    assert clean_resolution == int(resolution)
    assert clean_seed == expected_seed


@pytest.mark.parametrize(
    ("aspect", "expected"),
    [
        ("", None),
        ("auto", None),
        ("1:1", (1024, 1024)),
        ("4:3", (1184, 896)),
        ("16:9", (1376, 768)),
        ("9:16", (768, 1376)),
        ("21:9", (1568, 672)),
    ],
)
def test_resolve_size_maps_ratio_to_grid(aspect, expected):
    assert resolve_size(aspect, 1024) == expected


@pytest.mark.parametrize("aspect", ["1:1", "4:3", "16:9", "21:9", "9:16"])
@pytest.mark.parametrize("resolution", [768, 1024, 1328, 1536, 2048])
def test_resolved_sizes_stay_on_grid_and_under_the_cap(aspect, resolution):
    width, height = resolve_size(aspect, resolution)
    assert width % 32 == 0 and height % 32 == 0
    assert 64 <= width <= 2048 and 64 <= height <= 2048


def test_unknown_aspect_rejected():
    with pytest.raises(ImageValidationError):
        resolve_size("7:5", 1024)


def test_snap_resolution_aligns_to_service_grid():
    assert snap_resolution(1080) == 1088
    assert snap_resolution(720) == 704
    assert snap_resolution(1024) == 1024
    assert snap_resolution(2048) == 2048


def test_preview_size_reports_effective_canvas():
    assert preview_size(1080, "") == {
        "resolution": 1088, "aspect": "auto", "auto": True,
        "width": 1088, "height": 1088,
    }
    square = preview_size(1080, "16:9")
    assert square["auto"] is False
    assert (square["width"], square["height"]) == (1440, 800)
    assert square["resolution"] == 1088


def test_size_endpoint_returns_effective_canvas(image_client):
    app, _fake = image_client
    client = _client_with(app)
    login(client)
    response = client.get("/api/image/size?resolution=1080&aspect=16:9")
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    assert (payload["width"], payload["height"]) == (1440, 800)
    assert payload["resolution"] == 1088


def test_size_endpoint_rejects_unknown_resolution(image_client):
    app, _fake = image_client
    client = _client_with(app)
    login(client)
    response = client.get("/api/image/size?resolution=999&aspect=16:9")
    assert response.status_code == 400


def test_size_endpoint_requires_login(image_client):
    app, _fake = image_client
    client = _client_with(app)
    response = client.get("/api/image/size?resolution=1024", follow_redirects=False)
    assert response.status_code in (401, 303)


def test_generate_with_1080_sends_snapped_resolution(image_client):
    app, fake = image_client
    client = _client_with(app)
    login(client)
    token = csrf_token(client, "/image")
    client.post(
        "/api/image/generate",
        data={"csrf_token": token, "prompt": "x", "resolution": "1080",
              "aspect": "16:9"},
    )
    call = fake.calls[0]
    assert call["resolution"] == 1088
    assert (call["width"], call["height"]) == (1440, 800)


def test_validate_request_rejects_empty_prompt():
    with pytest.raises(ImageValidationError):
        validate_request("   ", "1024", "")


def test_sniff_image_detects_formats():
    assert sniff_image(PNG_1PX) == ".png"
    assert sniff_image(b"\xff\xd8\xff\xe0" + b"0" * 20) == ".jpg"
    assert sniff_image(b"GIF89a" + b"0" * 20) == ".gif"
    assert sniff_image(b"RIFF1234WEBP" + b"0" * 20) == ".webp"
    assert sniff_image(b"plain text") is None


def test_validate_upload_rejects_wrong_declared_type():
    with pytest.raises(ImageValidationError):
        validate_upload("ref.png", "text/plain", PNG_1PX)
