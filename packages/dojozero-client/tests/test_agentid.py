"""AgentID credential storage + client building (offline unit tests)."""

from __future__ import annotations

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


@pytest.fixture
def creds(tmp_path, monkeypatch):
    """``_credentials`` fully isolated into a tmp dir (HOME + both modules).

    Belt-and-suspenders so no ambient ``~/.dojozero/credentials.json`` or a
    prior test's module state can leak in regardless of suite ordering.
    """
    from dojozero_client import _config, _credentials

    cfg = tmp_path / ".dojozero"
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(_config, "CONFIG_DIR", cfg)
    monkeypatch.setattr(_credentials, "CONFIG_DIR", cfg)
    monkeypatch.setattr(_credentials, "CREDENTIALS_FILE", cfg / "credentials.json")
    return _credentials


_IDENT = {
    "agent_id": "agent_id:modelscope:agent_x",
    "kid": "sdk-abc",
    "key_path": "/somewhere/agent.pem",
    "idp_url": "https://pre.modelscope.cn/openapi/v1",
    "audience": "hub_abc123",
}


def test_save_load_has_agentid(creds):
    assert creds.has_agentid() is False
    creds.save_agentid(dict(_IDENT))
    assert creds.has_agentid() is True
    assert creds.load_agentid() == _IDENT


def test_agentid_coexists_with_api_key(creds):
    """save_api_key merges — it must not wipe a stored agentid (and vice versa)."""
    creds.save_api_key("sk-123")
    creds.save_agentid(dict(_IDENT))
    assert creds.load_api_key() == "sk-123"
    assert creds.load_agentid() == _IDENT
    # And saving api_key again keeps agentid.
    creds.save_api_key("sk-456")
    assert creds.load_agentid() == _IDENT


def _write_pem(path):
    key = Ed25519PrivateKey.generate()
    path.write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def test_build_agentid_client(tmp_path):
    pytest.importorskip("agent_id_client_sdk")
    from dojozero_client._agentid import build_agentid_client

    pem = tmp_path / "agent.pem"
    _write_pem(pem)
    client, audience = build_agentid_client({**_IDENT, "key_path": str(pem)})
    assert audience == "hub_abc123"
    assert client._default_audience == "hub_abc123"  # built with the right audience


def test_build_agentid_client_missing_field():
    from dojozero_client._agentid import build_agentid_client

    with pytest.raises(ValueError):
        build_agentid_client({"agent_id": "a", "kid": "k"})  # missing fields
