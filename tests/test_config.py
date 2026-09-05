from __future__ import annotations

from app.config import Settings


class TestSettings:
    def test_defaults(self):
        s = Settings(
            netbox_url="http://nb:8000",
            netbox_token="tok",
            twenty_url="http://tw:3000",
            twenty_api_key="key",
            valkey_host="vk",
            valkey_port=6380,
            public_base_url="http://public:8000",
            netbox_webhook_secret="nb-sec",
            twenty_webhook_token="tw-sec",
        )
        assert s.netbox_url == "http://nb:8000"
        assert s.valkey_port == 6380
        assert s.netbox_webhook_secret == "nb-sec"
        assert s.twenty_webhook_token == "tw-sec"
        assert s.sync_source_header == "X-Sync-Source"
        assert s.sync_source_value == "NetboxTwentyMiddleware"

    def test_auto_generates_secrets_when_not_set(self):
        s = Settings(
            netbox_url="http://nb:8000",
            netbox_token="tok",
            twenty_url="http://tw:3000",
            twenty_api_key="key",
        )
        assert len(s.netbox_webhook_secret) == 64
        assert len(s.twenty_webhook_token) == 64

    def test_explicit_secrets_not_overridden(self):
        s = Settings(
            netbox_url="http://nb:8000",
            netbox_token="tok",
            twenty_url="http://tw:3000",
            twenty_api_key="key",
            netbox_webhook_secret="my-secret",
            twenty_webhook_token="my-token",
        )
        assert s.netbox_webhook_secret == "my-secret"
        assert s.twenty_webhook_token == "my-token"

    def test_env_file_loading(self, tmp_path, monkeypatch):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "NETBOX_URL=http://env-netbox:8000\n"
            "NETBOX_TOKEN=env-token\n"
            "TWENTY_URL=http://env-twenty:3000\n"
            "TWENTY_API_KEY=env-key\n"
        )
        monkeypatch.chdir(tmp_path)
        s = Settings()
        assert s.netbox_url == "http://env-netbox:8000"
        assert s.netbox_token == "env-token"

    def test_env_vars_override(self, monkeypatch):
        monkeypatch.setenv("NETBOX_URL", "http://env:9000")
        monkeypatch.setenv("NETBOX_TOKEN", "env-tok")
        monkeypatch.setenv("TWENTY_URL", "http://env:4000")
        monkeypatch.setenv("TWENTY_API_KEY", "env-key")
        s = Settings()
        assert s.netbox_url == "http://env:9000"
        assert s.netbox_token == "env-tok"

    def test_get_settings_returns_instance(self):
        from app.config import get_settings

        s = get_settings()
        assert isinstance(s, Settings)
