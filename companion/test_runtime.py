"""Exercise the public app with the managed-service implementation absent."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import textwrap

import pytest

from companion import runtime


def test_self_hosted_app_runs_without_managed_service_files(tmp_path):
    source = Path(__file__).resolve().parent
    target = tmp_path / "companion"
    shutil.copytree(source, target, ignore=shutil.ignore_patterns(
        "__pycache__", "test_*", "tests", "gateway.py", "hosted*.py"))
    # This route belongs to the managed-service distribution as well.
    (target / "routes" / "hosted.py").unlink(missing_ok=True)
    code = textwrap.dedent('''
        import os
        from pathlib import Path
        import httpx
        from fastapi.testclient import TestClient
        from companion.server import create_app

        app = create_app("http://podfetch.test", Path("notes.db"),
                         transport=httpx.MockTransport(lambda r: httpx.Response(404)))
        with TestClient(app) as client:
            assert client.get("/companion/health").status_code == 200
            assert client.get("/companion/notes").json() == []
            assert client.get("/companion/settings/ai").status_code == 200
            assert client.get("/companion/hosted/usage").status_code == 404
        os.environ["PODSIFT_HOSTED"] = "true"
        try:
            create_app("http://podfetch.test", Path("unexpected.db"))
        except RuntimeError as exc:
            assert "separately installed runtime" in str(exc)
        else:
            raise AssertionError("Missing managed-service runtime accepted")
        assert not Path("unexpected.db").exists()
    ''')
    result = subprocess.run([sys.executable, "-c", code], cwd=tmp_path,
                            env={**os.environ, "PYTHONPATH": str(tmp_path),
                                 "PODSIFT_HOSTED": "false", "PYTHONDONTWRITEBYTECODE": "1"},
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("value", ["", "1", "yes", "tru"])
def test_invalid_runtime_mode_is_refused(monkeypatch, value):
    monkeypatch.setenv("PODSIFT_HOSTED", value)
    with pytest.raises(RuntimeError, match="true or false"):
        runtime.hosted_enabled()


def test_missing_dependency_in_installed_managed_runtime_is_not_hidden(monkeypatch):
    monkeypatch.setenv("PODSIFT_HOSTED", "true")
    def broken_import(*args):
        raise ModuleNotFoundError("missing dependency", name="missing_dependency")
    monkeypatch.setattr(runtime.importlib, "import_module", broken_import)
    with pytest.raises(ModuleNotFoundError, match="missing dependency"):
        runtime.hosted_policy()
