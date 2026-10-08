#!/usr/bin/env python3
"""Build and verify the canonical podcast engine from an isolated wheel install."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import zipfile


APP = Path(__file__).resolve().parents[1]


def run(argv: list[str], *, cwd: Path | str | None = None, env: dict[str, str] | None = None) -> None:
    subprocess.run(argv, cwd=cwd, env=env, check=True)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        raise SystemExit("ffmpeg and ffprobe are required for the portable render check")

    proof = Path(tempfile.mkdtemp(prefix="podsift-engine-wheel-proof-"))
    source = proof / "source"
    engine_source = source / "companion" / "engine"
    engine_source.mkdir(parents=True)
    shutil.copy2(APP / "pyproject.toml", source / "pyproject.toml")
    shutil.copy2(APP / "LICENSE", source / "LICENSE")
    shutil.copy2(APP / "NOTICE", source / "NOTICE")
    for module in (APP / "companion" / "engine").glob("*.py"):
        shutil.copy2(module, engine_source / module.name)
    wheels = proof / "wheels"
    wheels.mkdir()
    run([
        sys.executable, "-m", "pip", "wheel", "--no-deps", "--no-build-isolation",
        "--wheel-dir", str(wheels), str(source),
    ], cwd="/")
    wheel = next(wheels.glob("podsift_engine-*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        names = archive.namelist()
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        package_metadata = archive.read(metadata_name).decode("utf-8")
    assert any(name.endswith("/licenses/LICENSE") for name in names), names
    assert any(name.endswith("/licenses/NOTICE") for name in names), names
    assert "Provides-Extra: network" in package_metadata
    assert "Requires-Dist: httpx" in package_metadata and 'extra == "network"' in package_metadata

    venv = proof / "venv"
    run([sys.executable, "-m", "venv", str(venv)])
    python = venv / "bin" / "python"
    run([str(python), "-m", "pip", "install", "--no-deps", str(wheel)], cwd="/")

    runner = proof / "isolated_render.py"
    result_file = proof / "result.json"
    runner.write_text(
        """from __future__ import annotations
import hashlib
import json
from importlib import metadata
from pathlib import Path
import shutil
import socket
import subprocess
import sys

from companion import engine

proof = Path(sys.argv[1]).resolve()
checkout = Path(sys.argv[2]).resolve()
module = Path(engine.__file__).resolve()
distribution = Path(metadata.distribution("podsift-engine").locate_file("")).resolve()
assert module.is_relative_to(distribution), (module, distribution)
assert not module.is_relative_to(checkout), module

def no_network(*_args, **_kwargs):
    raise AssertionError("portable verification attempted network access")

socket.getaddrinfo = no_network
socket.socket.connect = no_network
audio = proof / "owned-synthetic.mp3"
subprocess.run([
    "ffmpeg", "-v", "error", "-nostdin", "-y", "-f", "lavfi", "-i",
    "sine=frequency=440:duration=6:sample_rate=44100", "-c:a", "libmp3lame", "-q:a", "3",
    str(audio),
], check=True)
duration = engine.probe_duration(audio)
identity = engine.identify(audio, str(audio))
episode = {
    "episode_id": "owned-fixture", "audio": str(audio), "duration": duration,
    "title": "Owned synthetic fixture", "media": identity, "exclude": [(1.0, 2.0)],
    "segments": [
        {"start": 0.0, "end": 1.0, "text": "routing lesson start"},
        {"start": 1.0, "end": 2.0, "text": "routing sponsor exclusion"},
        {"start": 2.0, "end": 4.0, "text": "routing lesson middle"},
        {"start": 4.0, "end": 9.0, "text": "routing lesson bounded ending"},
    ],
}
plan = engine.plan("routing", [episode], pad=0.0)
assert plan.status == "ready" and plan.spans
assert max(span["end"] for span in plan.spans) <= duration
assert all(span["end"] <= 1.0 or span["start"] >= 2.0 for span in plan.spans)

changed = proof / "changed-synthetic.mp3"
shutil.copyfile(audio, changed)
data = bytearray(changed.read_bytes())
data[-1] ^= 1
changed.write_bytes(data)
try:
    engine.verify_media(identity, str(audio), changed)
except engine.MediaIdentityError:
    pass
else:
    raise AssertionError("changed media was accepted")

rendered = proof / "bounded-cut.mp3"
cut = engine.cut(plan.spans, rendered, level=False)
assert 4.7 <= cut.duration <= 5.3, cut.duration
payload = {
    "distribution": str(distribution), "module": str(module),
    "wheel_only_import": True, "network_used": False,
    "planned_spans": [[span["start"], span["end"]] for span in plan.spans],
    "source_duration": duration, "render_duration": cut.duration,
    "render": str(rendered),
    "render_sha256": hashlib.sha256(rendered.read_bytes()).hexdigest(),
    "changed_media_rejected": True,
}
(proof / "result.json").write_text(json.dumps(payload, indent=2) + "\\n")
""",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env.pop("PYTHONPATH", None)
    env["PYTHONNOUSERSITE"] = "1"
    run([str(python), "-I", str(runner), str(proof), str(APP)], cwd="/", env=env)

    result = json.loads(result_file.read_text(encoding="utf-8"))

    network_venv = proof / "network-venv"
    run([sys.executable, "-m", "venv", str(network_venv)])
    network_python = network_venv / "bin" / "python"
    run([str(network_python), "-m", "pip", "install", f"{wheel}[network]"], cwd="/")
    network_runner = proof / "isolated_network.py"
    network_result = proof / "network-result.json"
    network_runner.write_text(
        """from __future__ import annotations
import json
from importlib import metadata
from pathlib import Path
import socket
import sys

from companion import engine

import httpx

proof = Path(sys.argv[1]).resolve()
checkout = Path(sys.argv[2]).resolve()
module = Path(engine.__file__).resolve()
distribution = Path(metadata.distribution("podsift-engine").locate_file("")).resolve()
assert module.is_relative_to(distribution), (module, distribution)
assert not module.is_relative_to(checkout), module
httpx_module = Path(httpx.__file__).resolve()
assert httpx_module.is_relative_to(Path(sys.prefix).resolve()), httpx_module
requirements = metadata.requires("podsift-engine") or []
assert any(req.startswith("httpx") and "network" in req for req in requirements), requirements
version = tuple(int(part) for part in httpx.__version__.split(".")[:2])
assert (0, 27) <= version < (1, 0), httpx.__version__

def no_network(*_args, **_kwargs):
    raise AssertionError("network-extra verification attempted a real connection")

socket.getaddrinfo = no_network
socket.socket.connect = no_network
seen = []
def answer(request):
    seen.append({"host": request.headers["host"], "path": request.url.path})
    return httpx.Response(200, content=b"<rss>owned fixture</rss>",
                          headers={"Content-Type": "application/rss+xml"})

body = engine.safe_read(
    "https://podcast.example/feed.xml",
    resolver=lambda _host, _port: ["93.184.216.34"],
    transport=httpx.MockTransport(answer),
)
assert body == b"<rss>owned fixture</rss>"
assert seen == [{"host": "podcast.example", "path": "/feed.xml"}]
(proof / "network-result.json").write_text(json.dumps({
    "module": str(module), "httpx_version": httpx.__version__, "httpx_module": str(httpx_module),
    "network_extra_declared": True, "mock_http_read": True, "real_network_used": False,
}, indent=2) + "\\n")
""",
        encoding="utf-8",
    )
    run([str(network_python), "-I", str(network_runner), str(proof), str(APP)], cwd="/", env=env)
    network = json.loads(network_result.read_text(encoding="utf-8"))
    result.update({
        "proof": str(proof),
        "wheel": str(wheel),
        "wheel_sha256": sha256(wheel),
        "license_files": ["LICENSE", "NOTICE"],
        "network": network,
    })
    result_file.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
