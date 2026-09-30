import asyncio, os, sys
os.environ["FW_CONTEXT_INDEX_DIR"] = r"D:\github\fw-context-mcp\implementation\fw_index_home"
os.chdir(r"C:\Users\h00969597\AppData\Local\Temp\dsh-XBvQPf\fw_smoke_g8")
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

async def main():
    params = StdioServerParameters(command=sys.executable, args=["-m", "fw_context_mcp.mcp.server"], cwd=os.getcwd())
    async with stdio_client(params) as (r, w):
        async with ClientSession(r, w) as s:
            await s.initialize()
            tools = await s.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("TOOLS:", len(names))
            need = ["read_file", "lookup_symbol", "list_conditional_regions", "explain_condition", "get_active_build", "find_callers", "get_file_map"]
            print("present:", {n: (n in names) for n in need})
            res = await s.call_tool("get_active_build", {"fast": True})
            txt = res.content[0].text if res.content else ""
            import json as j
            d = j.loads(txt) if txt.strip().startswith("{") else {}
            print("status:", d.get("status"), "| symbols:", d.get("symbol_count"), "| manifest:", d.get("manifest_verification"))
            res2 = await s.call_tool("read_file", {"file_path": "fixture/poller.c"})
            d2 = j.loads(res2.content[0].text)
            lines = d2.get("content", "").split("\n")
            print("read_file lines:", d2.get("lines"), "| line12:", repr(lines[11]), "| line14:", repr(lines[13]))
            res3 = await s.call_tool("lookup_symbol", {"name": "poller_init", "exact": True})
            d3 = j.loads(res3.content[0].text)
            print("lookup:", [(x.get("name"), x.get("line"), x.get("file", "")[-20:]) for x in d3] if isinstance(d3, list) else d3)
asyncio.run(main())