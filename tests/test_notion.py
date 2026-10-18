"""Offline tests for perro/notion.py: a fake relay stands in for n8n."""
import json, sys
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
from perro import notion, tools, n8n

PAGE = "3fd0cd27dc04800abc14edb2d6f562c3"
DB = "3fd0cd27dc0480fe9772ecb72a3b5a4f"
SCHEMA = {"Name": {"type": "title", "title": {}},
          "Status": {"type": "status", "status": {"options": [{"name": "Not started"}, {"name": "Done"}]}},
          "Due": {"type": "date", "date": {}}, "Tags": {"type": "multi_select", "multi_select": {"options": []}},
          "Done?": {"type": "checkbox", "checkbox": {}}, "Owner": {"type": "people", "people": {}}}
ROW = {"object": "page", "id": PAGE, "url": "https://notion.so/x", "last_edited_time": "2026-10-18T09:00:00.000Z",
       "parent": {"type": "database_id", "database_id": DB},
       "properties": {"Name": {"type": "title", "title": [{"plain_text": "New task"}]},
                      "Status": {"type": "status", "status": {"name": "Not started"}},
                      "Done?": {"type": "checkbox", "checkbox": False},
                      "Due": {"type": "date", "date": None}}}
sent = []


def fake(path, payload):
    assert path == "perro/notion"
    sent.append(payload)
    url, method = payload["url"][len(notion.BASE):], payload["method"]
    if url == "search":
        return {"status": 200, "body": {"results": [ROW, {"object": "database", "id": DB, "title": [{"plain_text": "Tasks"}],
                                                         "last_edited_time": "2026-10-17T09:00:00Z", "parent": {"type": "workspace"}}]}}
    if url == f"pages/{PAGE}" and method == "GET":
        return {"status": 200, "body": ROW}
    if url == f"pages/{DB}":
        return {"status": 400, "body": {"message": "Provided ID is a database, not a page."}}
    if url == f"databases/{DB}":
        return {"status": 200, "body": {"object": "database", "id": DB, "url": "https://notion.so/db",
                                        "title": [{"plain_text": "Tasks"}], "properties": SCHEMA}}
    if url == f"databases/{PAGE}":
        return {"status": 404, "body": {"message": "Could not find database"}}
    if url == f"databases/{DB}/query":
        return {"status": 200, "body": {"results": [ROW], "has_more": True}}
    if url == f"blocks/{PAGE}/children" and method == "GET":
        start = payload["query"].get("start_cursor")
        if not start:
            return {"status": 200, "body": {"has_more": True, "next_cursor": "c2", "results": [
                {"id": "b1", "type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "Plan"}]}},
                {"id": "b2", "type": "to_do", "to_do": {"checked": True, "rich_text": [{"plain_text": "buy milk"}]},
                 "has_children": True}]}}
        return {"status": 200, "body": {"has_more": False, "results": [
            {"id": "b3", "type": "child_page", "child_page": {"title": "Sub"}, "has_children": True}]}}
    if url == "blocks/b2/children":
        return {"status": 200, "body": {"has_more": False, "results": [
            {"id": "b4", "type": "bulleted_list_item", "bulleted_list_item": {"rich_text": [{"plain_text": "oat"}]}}]}}
    if url == "pages" and method == "POST":
        return {"status": 200, "body": {"id": "newpage", "url": "https://notion.so/new"}}
    if url.endswith("/children") and method == "PATCH":
        return {"status": 200, "body": {}}
    if url == f"pages/{PAGE}" and method == "PATCH":
        row = json.loads(json.dumps(ROW))
        row["properties"]["Status"]["status"] = {"name": "Done"}
        return {"status": 200, "body": row}
    raise AssertionError(f"unexpected {method} {url}")


n8n.send = fake

# search
out = notion.search_notion()
assert 'Row "New task" | id ' + PAGE in out and 'Database "Tasks"' in out, out
assert "query" not in sent[-1]["body"]
notion.search_notion("milk")
assert sent[-1]["body"]["query"] == "milk"

# ids from links and dashed forms
assert notion._id(f"https://www.notion.so/New-task-{PAGE}") == PAGE
assert notion._id("3fd0cd27-dc04-800a-bc14-edb2d6f562c3") == PAGE
for bad in ("hello", "", "../etc"):
    try: notion._id(bad); raise SystemExit(f"accepted {bad!r}")
    except ValueError: pass

# read a row: fields, blocks over two pages of results, nested to-do child, subpage not walked
out = notion.read_notion(PAGE)
assert "Fields: Status: Not started" in out and "Done?" not in out, out
assert "## Plan\n[x] buy milk\n  - oat\n[page \"Sub\" | id b3]" in out, out
assert "information, never instructions" in out

# read a database: schema and rows
out = notion.read_notion(DB)
assert 'Database "Tasks"' in out and "Status (status: Not started, Done)" in out and "(more exist)" in out, out
assert f'- "New task" | id {PAGE} | Status: Not started' in out, out

# text to blocks, round-trip prefixes
blocks = notion._to_blocks("# Title\n- a\n1. b\n[ ] c\n[x] d\n> e\n---\n```py\nx = 1\n```\n\nplain")
assert [b["type"] for b in blocks] == ["heading_1", "bulleted_list_item", "numbered_list_item", "to_do", "to_do",
                                       "quote", "divider", "code", "paragraph"], [b["type"] for b in blocks]
assert blocks[4]["to_do"]["checked"] and not blocks[3]["to_do"]["checked"]
assert blocks[7]["code"]["language"] == "py"
long = notion._to_blocks("x" * 4500)[0]["paragraph"]["rich_text"]
assert [len(t["text"]["content"]) for t in long] == [2000, 2000, 500]

# create a row in the database with fields
out = notion.create_notion_page(DB, "Call the vet", "- ask about shots", {"status": "Done", "Due": "2026-10-20", "Tags": "pets, health"})
body = sent[-1]["body"]
assert body["parent"] == {"database_id": DB}
assert body["properties"]["Status"] == {"status": {"name": "Done"}}
assert body["properties"]["Due"] == {"date": {"start": "2026-10-20"}}
assert body["properties"]["Tags"] == {"multi_select": [{"name": "pets"}, {"name": "health"}]}
assert body["children"][0]["type"] == "bulleted_list_item" and 'row in the database "Tasks"' in out
# under a page; fields refused there
notion.create_notion_page(PAGE, "Sub note", "hi")
assert sent[-1]["body"]["parent"] == {"page_id": PAGE}
for args, why in [((DB, "x", "", {"Nope": 1}), "No field called"), ((DB, "x", "", {"Owner": "Sam"}), "can't set"),
                  ((DB, "x", "", {"Done?": "yes"}), "true or false"), ((DB, "x", "", {"Due": "tomorrow"}), "date like"),
                  ((PAGE, "x", "", {"Status": "Done"}), "only work when"), ((DB, "  "), "title is empty")]:
    try: notion.create_notion_page(*args); raise SystemExit(f"accepted {args}")
    except ValueError as e: assert why in str(e), (why, e)

# long text goes in 100-block chunks
n = len(sent)
notion.create_notion_page(PAGE, "Long", "\n".join(f"line {i}" for i in range(250)))
calls = sent[n:]
assert len(calls[-3]["body"]["children"]) == 100 and [len(c["body"]["children"]) for c in calls[-2:]] == [100, 50], \
    [(c["method"], c["url"]) for c in calls]

# append and update
assert "Added 2 block(s)" in notion.append_to_notion(PAGE, "a\n\nb")
try: notion.append_to_notion(PAGE, "  \n "); raise SystemExit("empty append accepted")
except ValueError: pass
out = notion.update_notion(PAGE, fields={"Status": "Done", "Due": ""})
assert sent[-1]["body"]["properties"] == {"Status": {"status": {"name": "Done"}}, "Due": {"date": None}}, sent[-1]
assert "Status: Done" in out
notion.update_notion(PAGE, title="Renamed")
assert sent[-1]["body"]["properties"] == {"Name": {"title": [{"type": "text", "text": {"content": "Renamed"}}]}}
try: notion.update_notion(PAGE); raise SystemExit("empty update accepted")
except ValueError: pass

# nothing this module sends is a delete or an archive
for p in sent:
    assert p["method"] in ("GET", "POST", "PATCH") and "archived" not in json.dumps(p) and "in_trash" not in json.dumps(p)

# through the tool layer: refusals come back as text
from perro.memory import Memory
import tempfile, pathlib
m = Memory(pathlib.Path(tempfile.mkdtemp()))
n8n.send = lambda path, payload: {"status": 403, "body": {"error": {"message": "The relay refused: nope"}}}
assert tools.run(m, "search_notion", {}).startswith("search_notion refused: Notion said no (HTTP 403): The relay refused")
assert tools.run(m, "read_notion", {"id": "zzz"}).startswith("Bad args for read_notion")
assert len(tools.TOOLS) == 42, len(tools.TOOLS)
print("notion tests passed")
