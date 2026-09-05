from __future__ import annotations

from app.core.utils import extract_model_name, sanitize_netbox_tenant_name, slugify


class TestSlugify:
    def test_basic(self):
        assert slugify("Hello World") == "hello-world"

    def test_special_chars(self):
        assert slugify("Acme & Co!") == "acme-co"

    def test_collapses_dashes(self):
        assert slugify("a---b") == "a-b"

    def test_unicode(self):
        result = slugify("Örebro Municipality")
        assert "rebro" in result

    def test_max_length(self):
        result = slugify("a" * 100, max_length=10)
        assert len(result) <= 10

    def test_strips_trailing_dash(self):
        result = slugify("Hello World!", max_length=5)
        assert not result.endswith("-")

    def test_empty_string(self):
        assert slugify("") == ""

    def test_only_special_chars(self):
        assert slugify("!@#$%^&*()") == ""

    def test_whitespace_only(self):
        assert slugify("   ") == ""

    def test_already_slug(self):
        assert slugify("hello-world") == "hello-world"

    def test_numbers(self):
        assert slugify("Server 123") == "server-123"

    def test_multiple_spaces(self):
        assert slugify("a  b   c") == "a-b-c"

    def test_default_max_length(self):
        result = slugify("a" * 100)
        assert len(result) <= 64


class TestSanitizeNetboxTenantName:
    def test_strips_control_chars(self):
        assert sanitize_netbox_tenant_name("Hello\x00World") == "HelloWorld"

    def test_strips_tab(self):
        assert sanitize_netbox_tenant_name("Hello\tWorld") == "HelloWorld"

    def test_strips_newline(self):
        assert sanitize_netbox_tenant_name("Hello\nWorld") == "HelloWorld"

    def test_trims_whitespace(self):
        assert sanitize_netbox_tenant_name("  Hello  ") == "Hello"

    def test_preserves_normal_text(self):
        assert sanitize_netbox_tenant_name("Acme Corp") == "Acme Corp"

    def test_empty_string(self):
        assert sanitize_netbox_tenant_name("") == ""


class TestExtractModelName:
    def test_valid_target(self):
        assert extract_model_name("tenancy.tenant") == "tenant"

    def test_ipam_vrf(self):
        assert extract_model_name("ipam.vrf") == "vrf"

    def test_ipam_prefix(self):
        assert extract_model_name("ipam.prefix") == "prefix"

    def test_no_dot(self):
        assert extract_model_name("tenant") is None

    def test_too_many_dots(self):
        assert extract_model_name("a.b.c") is None

    def test_empty_string(self):
        assert extract_model_name("") is None
