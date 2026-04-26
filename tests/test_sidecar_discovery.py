"""Unit tests for SidecarDiscovery — pure module import, no subprocess spawn."""

import json

from bterminal import SidecarDiscovery, SidecarManifest


def test_empty_dir_returns_empty(tmp_path):
    assert SidecarDiscovery(str(tmp_path)).load_all() == {}


def test_loads_valid_manifest(tmp_path):
    (tmp_path / "foo.json").write_text(
        json.dumps({
            "name": "foo",
            "run_command": "/bin/true",
            "healthcheck_url": "http://127.0.0.1:1/health",
        })
    )
    out = SidecarDiscovery(str(tmp_path)).load_all()
    assert set(out) == {"foo"}
    assert isinstance(out["foo"], SidecarManifest)
    assert out["foo"].run_command == "/bin/true"
    # Defaults preserved for unspecified fields
    assert out["foo"].default_in_session is True
    assert out["foo"].auto_start is False


def test_skips_invalid_json(tmp_path):
    (tmp_path / "bad.json").write_text("not-json{")
    (tmp_path / "noname.json").write_text(json.dumps({"description": "no name"}))
    (tmp_path / "valid.json").write_text(json.dumps({"name": "valid"}))
    (tmp_path / "ignored.txt").write_text(json.dumps({"name": "ignored"}))
    out = SidecarDiscovery(str(tmp_path)).load_all()
    assert set(out) == {"valid"}
