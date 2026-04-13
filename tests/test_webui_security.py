from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from nanobot_channel_webui import WebUIChannel, WebUIConfig


class _DummyBus:
    async def publish_inbound(self, msg):
        return None


def _channel() -> WebUIChannel:
    return WebUIChannel(WebUIConfig(enabled=True, host="127.0.0.1", port=18792), _DummyBus())


def _request(headers: dict[str, str]):
    return SimpleNamespace(headers=headers)


def test_webui_trusts_same_host_origin() -> None:
    channel = _channel()

    assert channel._is_trusted_origin("http://127.0.0.1:18792", "127.0.0.1:18792") is True
    assert channel._is_trusted_origin("https://127.0.0.1:18792", "127.0.0.1:18792") is True
    assert channel._is_trusted_origin("https://malicious.example", "127.0.0.1:18792") is False


def test_webui_trusts_configured_allowed_origins() -> None:
    config = WebUIConfig(
        enabled=True,
        host="0.0.0.0",
        port=18792,
        allowed_origins=["https://panel.tailnet.ts.net"],
    )
    channel = WebUIChannel(config, _DummyBus())

    assert channel._is_trusted_origin("https://panel.tailnet.ts.net", "192.168.1.42:18792") is True
    assert channel._is_trusted_origin("https://evil.example", "192.168.1.42:18792") is False


def test_webui_http_request_origin_or_referer() -> None:
    channel = _channel()

    same_origin = _request({"host": "127.0.0.1:18792", "origin": "http://127.0.0.1:18792"})
    cross_origin = _request({"host": "127.0.0.1:18792", "origin": "https://malicious.example"})
    referer_same = _request({"host": "127.0.0.1:18792", "referer": "http://127.0.0.1:18792/chat"})

    assert channel._http_request_is_trusted(same_origin) is True
    assert channel._http_request_is_trusted(cross_origin) is False
    assert channel._http_request_is_trusted(referer_same) is True


def test_webui_configures_cors_for_allowed_origins() -> None:
    config = WebUIConfig(
        enabled=True,
        host="0.0.0.0",
        port=18792,
        allowed_origins=[
            "http://192.168.66.136:18792",
            "http://100.64.100.170:18792",
            "http://192.168.66.136:18792",
        ],
    )
    channel = WebUIChannel(config, _DummyBus())
    app = FastAPI()

    channel._configure_cors(app)

    cors = [m for m in app.user_middleware if m.cls is CORSMiddleware]
    assert len(cors) == 1
    cors_cfg = getattr(cors[0], "kwargs", None) or getattr(cors[0], "options", None) or {}
    assert cors_cfg["allow_origins"] == [
        "http://192.168.66.136:18792",
        "http://100.64.100.170:18792",
    ]


def test_webui_skips_cors_when_no_allowed_origins() -> None:
    channel = _channel()
    app = FastAPI()

    channel._configure_cors(app)

    assert all(m.cls is not CORSMiddleware for m in app.user_middleware)


def test_webui_allow_from_matches_principal_alias() -> None:
    config = WebUIConfig(
        enabled=True,
        host="127.0.0.1",
        port=18792,
        allow_from=["alice"],
    )
    channel = WebUIChannel(config, _DummyBus())

    assert channel.is_allowed("webuser:alice|alice|web:192.168.1.2|192.168.1.2") is True


def test_webui_allow_from_matches_host_alias() -> None:
    config = WebUIConfig(
        enabled=True,
        host="127.0.0.1",
        port=18792,
        allow_from=["192.168.1.2"],
    )
    channel = WebUIChannel(config, _DummyBus())

    assert channel.is_allowed("web:192.168.1.2|192.168.1.2") is True
