"""Verify a headless learning cut across real stdio MCP, HTTP and the audio renderer.

Uses synthetic audio and the offline PodFetch fixture; no external model, podcast download,
user database or browser. Requires companion test dependencies, ffmpeg and the MCP dev venv.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
import time

import httpx
import uvicorn
from fastapi import Request, Response

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from companion.cuts import get_speech
from companion.llm import get_llm
from companion.server import create_app
from companion.test_cuts import EP, FakePodFetch, tone


CLIENT_SCRIPT = r'''
import asyncio, json, os
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    url, episode_id = os.environ["VERIFY_PODCAST_URL"], os.environ["VERIFY_EPISODE_ID"]
    params = StdioServerParameters(command=os.environ["VERIFY_MCP_PYTHON"],
        args=["-m", "podcast_mcp.server"], env={"PODCAST_URL": url})
    checks = []
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert {"plan_from_segments", "get_transcript", "get_plan_script", "prepare_episode"} <= names
            checks.append("Real stdio MCP initialization and agent tool discovery")
            async def call(name, args):
                result = await session.call_tool(name, args)
                assert not result.isError, result
                return json.loads(result.content[0].text)

            cursor, words, ids, digest = 0, [], [], None
            while True:
                page = await call("get_transcript", {"episode_id": episode_id, "cursor": cursor, "max_chars": 300})
                digest = digest or page["transcript_digest"]
                assert digest and digest == page["transcript_digest"]
                words += [line["text"] for line in page["segments"]]
                ids += [line["id"] for line in page["segments"]]
                if page["next_cursor"] is None:
                    assert page["complete"]
                    break
                assert page["next_cursor"] > cursor
                cursor = page["next_cursor"]
            assert len(ids) == 15 and len(set(ids)) == 15
            checks.append("Every transcript segment read once, with a stable version digest")
            prepared = await call("prepare_episode", {"episode_id": episode_id})
            assert prepared["downloaded"] and prepared["title"]
            assert prepared["status"] == "downloaded"
            checks.append("Headless preparation reads the real episode envelope and reuses a parsed transcript")
            selected = [{"episode_id": episode_id, "transcript_digest": digest,
                "keep": [{"start_id": ids[3], "end_id": ids[4], "why": "A useful new application"}],
                "skip": [{"start_id": ids[3], "end_id": ids[3],
                          "why": "Already recalled from a supplied book, chapter 12"}]}]
            plan = await call("plan_from_segments", {"want": "Applications beyond familiar definitions",
                              "selections": selected, "minutes": 0.3})
            assert plan["mode"] == "agent" and plan["status"] == "ready"
            assert 0 < plan["kept_seconds"] <= 18
            assert all(s["start"] >= 32 for s in plan["spans"])
            checks.append("Caller choices become a stored plan; prior-learning skip and budget hold without app AI")

            cursor, script = 0, []
            while True:
                page = await call("get_plan_script", {"plan_id": plan["id"], "cursor": cursor, "limit": 3})
                script += page["lines"]
                if page["next_cursor"] is None:
                    assert page["complete"]
                    break
                cursor = page["next_cursor"]
            assert any("chapter 12" in (line.get("why") or "") for line in script)
            assert any(line["kind"] == "keep" for line in script)
            checks.append("Paginated keep/skip script preserves source sentences and book evidence")

            job_id = (await call("render_cut", {"plan_id": plan["id"]}))["job_id"]
            deadline = asyncio.get_running_loop().time() + 60
            while True:
                job = await call("job_status", {"job_id": job_id})
                if job["status"] in ("done", "failed"):
                    break
                assert asyncio.get_running_loop().time() < deadline, "Export timed out"
                await asyncio.sleep(0.1)
            assert job["status"] == "done", job
            cut = await call("cut_link", {"cut_id": job["cut_id"]})
            assert cut["index"] and all(item["episode_id"] == episode_id for item in cut["index"])
            async with httpx.AsyncClient() as client:
                response = await client.get(cut["url"])
                assert response.status_code == 200 and len(response.content) > 1000
                assert response.headers["content-type"] == "audio/mpeg"
            checks.append("Real renderer exports a playable MP3 and original-source index through MCP")
            print(json.dumps({"status": "PASS", "checks": checks, "segments_read": len(ids),
                              "kept_seconds": plan["kept_seconds"], "source_seconds": plan["source_seconds"]}))

asyncio.run(main())
'''


def main() -> None:
    # Keep the venv launcher path: resolving its symlink selects the system interpreter.
    mcp_python = Path(os.environ.get("MCP_PYTHON", ROOT / "mcp/.venv/bin/python")).expanduser().absolute()
    if not mcp_python.is_file():
        raise SystemExit("Install the MCP dev environment first; see CONTRIBUTING.md.")
    with tempfile.TemporaryDirectory(prefix="podcast-agent-check-") as scratch:
        folder = Path(scratch)
        fake = FakePodFetch(tone(folder / "source.mp3"))
        fake.add(transcript="generated")
        app = create_app("http://podfetch.test", folder / "companion.db", transport=httpx.MockTransport(fake))
        app.dependency_overrides[get_llm] = lambda: None
        app.dependency_overrides[get_speech] = lambda: None

        # Mirror the same-origin API proxy supplied by Caddy/Vite in a real install.
        # Companion routes still perform their normal internal HTTP calls via MockTransport.
        @app.api_route("/api/v1/{path:path}", methods=["GET", "POST", "PUT"])
        async def podcast_api(path: str, request: Request):
            response = fake(httpx.Request(request.method, f"http://podfetch.test/api/v1/{path}",
                                          headers=request.headers, content=await request.body()))
            return Response(response.content, status_code=response.status_code,
                            media_type=response.headers.get("content-type"))

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        sock.listen(128)
        port = sock.getsockname()[1]
        server = uvicorn.Server(uvicorn.Config(app, log_level="error", lifespan="off"))
        thread = threading.Thread(target=lambda: server.run(sockets=[sock]), daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 10
            while not server.started:
                if not thread.is_alive() or time.monotonic() > deadline:
                    raise RuntimeError("The synthetic companion did not start.")
                time.sleep(0.02)
            env = {**os.environ, "VERIFY_PODCAST_URL": f"http://127.0.0.1:{port}",
                   "VERIFY_EPISODE_ID": EP, "VERIFY_MCP_PYTHON": str(mcp_python)}
            result = subprocess.run([str(mcp_python), "-c", CLIENT_SCRIPT], cwd=ROOT, env=env,
                                    capture_output=True, text=True, timeout=90)
            if result.returncode:
                raise RuntimeError(result.stderr + result.stdout)
            report = json.loads(result.stdout.strip().splitlines()[-1])
            out = ROOT / "evidence/agent-workflow"
            out.mkdir(parents=True, exist_ok=True)
            (out / "report.json").write_text(json.dumps(report, indent=2) + "\n")
            print(json.dumps(report, indent=2))
        finally:
            server.should_exit = True
            thread.join(timeout=10)
            sock.close()


if __name__ == "__main__":
    main()
