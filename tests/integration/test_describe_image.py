"""Integration test for app.adapters.inference.describe_image — ONE real
call to the 9router vision combo (T16's gate). Builds a synthetic
screenshot-like PNG in the test itself (never a real user screenshot),
per PRD §23.
Run directly: pytest tests/integration/test_describe_image.py -q
"""
from __future__ import annotations

import pytest
from PIL import Image, ImageDraw

from app.adapters import inference
from app.adapters.inference import InferenceError, describe_image


def _synthetic_error_screenshot(path: str) -> None:
    img = Image.new("RGB", (400, 200), color="white")
    draw = ImageDraw.Draw(img)
    draw.rectangle([0, 0, 399, 40], fill="red")
    draw.text((10, 10), "ERROR: Checkout failed (500)", fill="white")
    draw.text((10, 60), "Payment Gateway - Retry", fill="black")
    draw.rectangle([10, 100, 150, 140], outline="black")
    draw.text((20, 112), "Retry button", fill="black")
    img.save(path)


def test_describe_image_real_call_returns_nonempty_description(tmp_path):
    image_path = tmp_path / "synthetic_error.png"
    _synthetic_error_screenshot(str(image_path))

    description = describe_image(str(image_path))

    assert isinstance(description, str)
    assert len(description.strip()) > 0


def test_describe_image_raises_inference_error_when_unreachable(monkeypatch, tmp_path):
    image_path = tmp_path / "synthetic_error.png"
    _synthetic_error_screenshot(str(image_path))
    # _NINE_ROUTER_URL is read once at import time, so point the module
    # attribute itself at an unreachable address (env var alone is too late).
    monkeypatch.setattr(inference, "_NINE_ROUTER_URL", "http://127.0.0.1:1")

    with pytest.raises(InferenceError):
        describe_image(str(image_path), timeout_seconds=3)
