"""Read shared browser plans through a real stdio MCP client, with no model calls."""
import asyncio
import json
import os
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

ROOT = Path(__file__).resolve().parents[1]


async def main():
    browser = json.loads((ROOT / "evidence/source-workspace/report.json").read_text())
    assert browser["status"] == "PASS", "Complete the browser journey first."
    params = StdioServerParameters(
        command=str(ROOT / "mcp/.venv/bin/python"),
        args=["-m", "podcast_mcp.server"],
        env={"PODCAST_URL": os.environ.get("APP_URL", "http://127.0.0.1:5219")},
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert "list_plans" in names and "plan_from_segments" in names
            result = await session.call_tool("list_plans", {"episode_id": browser["episode_id"], "limit": 5})
            assert not result.isError, result
            data = json.loads(result.content[0].text)
            plan = next(plan for plan in data["plans"] if plan["id"] == browser["plan_id"])
            assert plan["mode"] == "agent" and plan["kept_seconds"] > 0
            assert plan["kept_seconds_clock"]
            assert any(episode["episode_id"] == browser["episode_id"] for episode in plan["episodes"])
    report = {"status": "PASS", "checks": ["Real stdio MCP exposes saved-plan discovery",
              "The connected agent recovers the browser's persisted source selection with human-readable clocks"],
              "tool_count": len(names), "plan_id": browser["plan_id"]}
    (ROOT / "evidence/source-workspace/mcp-report.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report))


if __name__ == "__main__":
    asyncio.run(main())
