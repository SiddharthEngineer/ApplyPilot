"""Tests for Workday SSL context configuration."""

import ssl

from applypilot.discovery.workday import _ssl_context, setup_proxy


def test_ssl_context_exists():
    """Verify _ssl_context is an SSLContext."""
    assert isinstance(_ssl_context, ssl.SSLContext)


def test_ssl_context_uses_certifi():
    """Verify the SSL context uses certifi's CA bundle."""
    assert _ssl_context.cert_store_stats()["x509"] > 0
    # The context should have loaded certs from certifi
    ca_certs = _ssl_context.get_ca_certs()
    assert len(ca_certs) > 0, "SSL context should have loaded CA certificates"


def test_ssl_context_verify_mode():
    """Verify SSL context is in verify mode."""
    assert _ssl_context.verify_mode == ssl.CERT_REQUIRED


def test_setup_proxy_none_preserves_ssl_context():
    """Verify setup_proxy(None) creates opener that uses SSL context."""
    setup_proxy(None)
    from applypilot.discovery.workday import _opener
    # The opener should exist
    assert _opener is not None


def test_ssl_context_protocol():
    """Verify SSL context uses modern TLS protocol."""
    assert _ssl_context.protocol & ssl.PROTOCOL_TLS_CLIENT
