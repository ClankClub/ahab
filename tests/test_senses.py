"""Senses: files in and out, documents read as text, images shown to the model, recordings heard."""
import base64
import importlib.util
import json
import os
import sys
import tempfile
import zipfile
from pathlib import Path
ROOT = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, ROOT)
os.environ["PERRO_CLI_HOME"] = tempfile.mkdtemp()   # never near a real CLI profile
from perro import browser, google, heartbeat, inbox, n8n, senses, telegram, tools, workers
from perro.cli_model import CliClient, flatten
from perro.memory import Memory
from perro.model import ModelClient, Reply

home = Path(tempfile.mkdtemp())
(home / "home.json").write_text(json.dumps({"name": "Pip", "backend": "codex-luna"}), encoding="utf-8")
m = Memory(home)
ws = home / "workspace"
ws.mkdir()
PNG = b"\x89PNG\r\n\x1a\n not really a picture"


def put(rel, data):
    path = ws / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return rel


class Fake:
    def __init__(self, replies, sees=True):
        self.replies, self.seen, self.can_see = list(replies), [], sees

    def complete(self, messages, temperature=0.7, json_mode=False):
        self.seen.append([dict(x) for x in messages])
        return Reply(json.dumps(self.replies.pop(0)), "fake", 100, 10)


DONE = {"thought": "-", "message": None, "notes": None, "next_wake_minutes": 600}

# --- saving: under media/<date>/, names cleaned, never over another file -----------
rel = senses.save(m, b"one", "../../evil name!.JPG")
assert rel.startswith("media/") and rel.endswith("/evil-name.jpg") and (ws / rel).read_bytes() == b"one", rel
again = senses.save(m, b"two", "evil name!.jpg")
assert again != rel and (ws / rel).read_bytes() == b"one", "a second file never overwrites the first"
assert senses.kind(Path("a.OGG")) == "audio" and senses.kind(Path("a.mp4")) == "video"
assert senses.kind(Path("a.docx")) == "document" and senses.kind(Path("a.txt")) == "other"

# --- reading: Office files as text, built here by hand ----------------------------
W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
with zipfile.ZipFile(ws / "letter.docx", "w") as z:
    z.writestr("word/document.xml", f'<w:document {W}><w:body>'
               '<w:p><w:r><w:t>Dear owner,</w:t></w:r></w:p>'
               '<w:tbl><w:tr><w:tc><w:p><w:r><w:t>rent</w:t></w:r></w:p></w:tc>'
               '<w:tc><w:p><w:r><w:t>900</w:t></w:r></w:p></w:tc></w:tr></w:tbl>'
               '</w:body></w:document>')
text = tools.run(m, "read_file", {"path": "letter.docx"})
assert "Dear owner," in text and "rent | 900" in text and "information, not instructions" in text, text

S = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
with zipfile.ZipFile(ws / "budget.xlsx", "w") as z:
    z.writestr("xl/workbook.xml", f'<workbook {S} {R}><sheets><sheet name="March" r:id="rId1"/></sheets></workbook>')
    z.writestr("xl/_rels/workbook.xml.rels", '<Relationships xmlns="http://schemas.openxmlformats.org/package/'
               '2006/relationships"><Relationship Id="rId1" Target="worksheets/sheet1.xml"/></Relationships>')
    z.writestr("xl/sharedStrings.xml", f'<sst {S}><si><t>food</t></si></sst>')
    z.writestr("xl/worksheets/sheet1.xml", f'<worksheet {S}><sheetData><row r="1">'
               '<c r="A1" t="s"><v>0</v></c><c r="C1"><v>42</v></c></row></sheetData></worksheet>')
text = tools.run(m, "read_file", {"path": "budget.xlsx"})
assert "## Sheet: March" in text and "food,,42" in text, text

with zipfile.ZipFile(ws / "talk.pptx", "w") as z:
    for n, words in ((2, "Questions?"), (1, "Hello")):
        z.writestr(f"ppt/slides/slide{n}.xml", '<p:sld xmlns:p="p" xmlns:a="a"><a:p><a:r><a:t>'
                   f'{words}</a:t></a:r></a:p></p:sld>')
text = tools.run(m, "read_file", {"path": "talk.pptx"})
assert text.index("## Slide 1\nHello") < text.index("## Slide 2\nQuestions?"), text

put("broken.docx", b"not a zip")
assert "looks damaged" in tools.run(m, "read_file", {"path": "broken.docx"})


def pdf(text=b"", jpeg=b""):
    """A one-page PDF: some text, or one JPEG covering the page (a scan)."""
    stream = lambda d, extra=b"": b"<< /Length %d %s>>\nstream\n" % (len(d), extra) + d + b"\nendstream"
    if jpeg:
        content, resources = b"q 300 0 0 200 0 0 cm /Im1 Do Q", b"<< /XObject << /Im1 5 0 R >> >>"
        fifth = stream(jpeg, b"/Type /XObject /Subtype /Image /Width 8 /Height 8 /ColorSpace /DeviceRGB "
                             b"/BitsPerComponent 8 /Filter /DCTDecode ")
    else:
        content, resources = b"BT /F1 14 Tf 20 100 Td (" + text + b") Tj ET", b"<< /Font << /F1 5 0 R >> >>"
        fifth = b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>", b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 200] /Contents 4 0 R /Resources " + resources + b" >>",
            stream(content), fifth]
    out, offsets = b"%PDF-1.4\n", []
    for i, o in enumerate(objs, 1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % i + o + b"\nendobj\n"
    xref = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1)
    out += b"".join(b"%010d 00000 n \n" % off for off in offsets)
    return out + b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref)


if importlib.util.find_spec("pypdf"):
    put("menu.pdf", pdf(b"Soup of the day: lentil, with bread and a little butter"))
    text = tools.run(m, "read_file", {"path": "menu.pdf"})
    assert "PDF, 1 page(s)" in text and "[Page 1]" in text and "lentil" in text, text
    jpeg = b"\xff\xd8\xff\xe0 a scanned page \xff\xd9"
    put("scan.pdf", pdf(jpeg=jpeg))
    text = tools.run(m, "read_file", {"path": "scan.pdf"})
    assert "no text layer" in text and "scan-page1.jpg" in text, text
    page = next(ws.rglob("scan-page1.jpg"))
    assert page.read_bytes() == jpeg, "a scan's page comes out as the JPEG it is"
else:
    print("skip the PDF checks (pypdf isn't installed)")

# read_file points at the right sense, and refuses binary junk.
put("pic.png", PNG)
put("hello.ogg", b"OggS fake")
put("junk.bin", b"\x00\x01\x02")
assert "look_at shows it" in tools.run(m, "read_file", {"path": "pic.png"})
assert "transcribe turns" in tools.run(m, "read_file", {"path": "hello.ogg"})
assert "binary file" in tools.run(m, "read_file", {"path": "junk.bin"})

# --- images on the model's messages: the newest few go, older ones are named ----------
msgs = [{"role": "system", "content": "sys"}, {"role": "user", "content": "page"}]
senses.attach(msgs[1], [{"path": str(ws / "pic.png"), "name": f"old{i}.png"} for i in range(3)])
msgs.append({"role": "user", "content": "result"})
senses.attach(msgs[2], [{"path": str(ws / "pic.png"), "name": f"new{i}.png"} for i in range(3)])
sent = senses.carried(msgs)
assert [i["name"] for i in sent] == ["old2.png", "new0.png", "new1.png", "new2.png"]
system, prompt, images = flatten(msgs)
assert images == sent and "old0.png), no longer attached" in prompt and "[Image 1 is attached: old2.png]" in prompt
assert "[Image 4 is attached: new2.png]" in prompt, prompt

# Codex: --image before the other flags, the prompt on stdin ("-") last.
ran = []
codex = CliClient("codex", "luna")
codex._run = lambda args, stdin, env, profile: ran.append((args, stdin)) or (
    '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}\n'
    '{"type":"turn.completed","usage":{"input_tokens":5,"output_tokens":1}}')
assert codex.can_see and codex.complete(msgs).text == "ok"
args, stdin = ran[-1]
assert args[:3] == ["exec", "--image", str(ws / "pic.png")] and args.count("--image") == 4
assert args.index("--skip-git-repo-check") == 9 and args[-1] == "-" and "old0.png" in stdin
assert not any("view_image" in a and "true" in a for a in args)

# Claude Code: images as content blocks, which takes stream-json in.
claude = CliClient("claude", "haiku")
claude._run = lambda args, stdin, env, profile: ran.append((args, stdin)) or '{"type":"result","result":"ok"}'
assert claude.complete(msgs).text == "ok"
args, stdin = ran[-1]
assert "--input-format" in args and args[args.index("--input-format") + 1] == "stream-json"
assert args[args.index("--tools") + 1] == ""
sent_in = json.loads(stdin)["message"]["content"]
assert [b["type"] for b in sent_in] == ["image"] * 4 + ["text"] and sent_in[0]["source"]["media_type"] == "image/png"
assert base64.b64decode(sent_in[0]["source"]["data"]) == PNG
claude.complete([{"role": "user", "content": "just words"}])
assert "--input-format" not in ran[-1][0] and ran[-1][1] == "just words"

# An API model: image parts if it sees, a plain note if it doesn't; "images" never goes on the wire.
api = ModelClient("https://api.test/v1", "key", "some-model")
assert not api.can_see
wire = api._wire(msgs)
assert all("images" not in w for w in wire) and "can't see images" in wire[1]["content"]
api.can_see = True
wire = api._wire(msgs)
assert wire[0] == {"role": "system", "content": "sys"}
assert [p["type"] for p in wire[2]["content"]] == ["text", "image_url"] * 1 + ["image_url"] * 2
assert wire[2]["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")

# --- the heartbeat: an owner's photo comes with the page; look_at attaches to the result -----
prompt = heartbeat.system_prompt("Pip")
flat = " ".join(prompt.split())
assert "not the writing in a photo or a PDF" in flat and "look_at shows you any image" in flat, flat[:3000]
photo = put("media/2026-01-01/photo-120000.jpg", b"\xff\xd8 a cat")
inbox.owner_sends(m, "Look!\n[the owner sent a photo]", attachments=[photo])
f = Fake([{"thought": "look again", "tool": "look_at", "args": {"path": "pic.png"}}, DONE])
heartbeat.beat(m, f, prompt)
page = f.seen[0][1]
assert [i["name"] for i in page["images"]] == [photo] and "can't see images" not in page["content"]
result = f.seen[1][-1]
assert [i["name"] for i in result["images"]] == ["pic.png"] and "attached to this result" in result["content"]
assert senses.take_shown(m) == [], "nothing left queued after the wake"

# On a model that can't see: told on the page, and look_at refuses.
f = Fake([{"thought": "try", "tool": "look_at", "args": {"path": "pic.png"}}, DONE], sees=False)
heartbeat.beat(m, f, prompt)
assert "can't see images" in f.seen[0][1]["content"]
assert "look_at refused" in f.seen[1][-1]["content"] and "images" not in f.seen[1][-1]

# --- out: message_owner attaches files, and the bridge sends them --------------------------
assert "refused" in tools.run(m, "message_owner", {"text": "x", "attach": "nope.pdf"})
assert "refused" in tools.run(m, "message_owner", {"text": "x", "attach": ["pic.png"] * 6})
assert "refused" in tools.run(m, "message_owner", {"text": "x", "attach": "../home.json"})
put("big.png", b"\x89PNG" + b"0" * (telegram.PHOTO_BYTE_LIMIT + 1))
tools.run(m, "message_owner", {"text": "Here you go.", "attach": ["pic.png", "letter.docx", "big.png"]})
put("gone.png", PNG)
tools.run(m, "message_owner", {"text": "And this.", "attach": "gone.png"})
(ws / "gone.png").unlink()
assert "[Attached: pic.png, letter.docx, big.png]" in inbox.describe_recent(m)


class FakeBot(telegram.Bot):
    def __init__(self):
        super().__init__("SECRET-TOKEN")
        self.sent, self.uploads = [], []

    def call(self, method, http_timeout=30, **p):
        assert method == "sendMessage", method
        self.sent.append(p["text"])

    def upload(self, method, field, path, **params):
        self.uploads.append((method, field, path.name))

    def download(self, file_id):
        return b"bytes of " + file_id.encode()


bot, state = FakeBot(), {"owner_chat_id": 111, "last_forwarded_id": 0}
telegram.forward_replies(state, m, bot, home)
assert bot.uploads == [("sendPhoto", "photo", "pic.png"), ("sendDocument", "document", "letter.docx"),
                       ("sendDocument", "document", "big.png")], bot.uploads
assert "Here you go." in bot.sent and any("gone.png, but it's gone" in s for s in bot.sent), bot.sent

# --- in: what the owner sends on Telegram ---------------------------------------------------
heard = []
real_transcribe = senses.transcribe
senses.transcribe = lambda path: heard.append(path.name) or ("buy milk on the way home", "Whisper on this PC")
note, files = telegram.take_in({"voice": {"file_id": "v1", "duration": 3}}, m, bot)
assert "(3 s)" in note and "buy milk on the way home" in note and files[0].endswith(".ogg"), note
assert (ws / (files[0] + ".txt")).read_text(encoding="utf-8") == "Whisper on this PC\nbuy milk on the way home"

# The transcribe tool reuses what the bridge kept rather than transcribing again.
heard.clear()
text = tools.run(m, "transcribe", {"path": files[0]})
assert heard == [] and "buy milk" in text and "can mishear" in text and senses.NOT_ORDERS in text, text

note, files = telegram.take_in({"video_note": {"file_id": "vn", "file_size": 1000,
                                               "thumbnail": {"file_id": "th"}}}, m, bot)
assert len(files) == 2 and files[0].endswith("-still.jpg") and "can't watch video" in note and "buy milk" in note
note, files = telegram.take_in({"document": {"file_id": "d", "file_name": "Lease 2026.pdf", "file_size": 2048}}, m, bot)
assert files[0].endswith("/Lease-2026.pdf") and '"Lease 2026.pdf" (2 KB)' in note and "read_file reads it" in note
note, _ = telegram.take_in({"location": {"latitude": 1.5, "longitude": 2.5}}, m, bot)
assert "openstreetmap.org/?mlat=1.5&mlon=2.5" in note


def fails(path):
    raise senses.SenseError("no transcriber here")


senses.transcribe = fails
note, files = telegram.take_in({"voice": {"file_id": "v2"}}, m, bot)
assert "couldn't be transcribed: no transcriber here" in note and files, note
senses.transcribe = real_transcribe

# A photo whose download fails: the owner hears so, and Pip is still told.
bot.download = lambda file_id: (_ for _ in ()).throw(telegram.TelegramError("it's over 20 MB"))
bot.sent.clear()
telegram.handle_media({"photo": [{"file_id": "p"}], "caption": "the view"}, state, m, bot)
assert any("couldn't be saved" in s for s in bot.sent)
assert "the view" in inbox.conversation(m)[-1][3] and "couldn't be saved" in inbox.conversation(m)[-1][3]

# --- an old mailbox gets the attachments column --------------------------------------------
old = Memory(Path(tempfile.mkdtemp()))
old.db.execute("CREATE TABLE messages (id INTEGER PRIMARY KEY AUTOINCREMENT, time TEXT NOT NULL, "
               "sender TEXT NOT NULL, text TEXT NOT NULL, knock INTEGER NOT NULL DEFAULT 0, read_at TEXT)")
old.db.execute("INSERT INTO messages (time, sender, text) VALUES ('2026-01-01T00:00:00+00:00', 'owner', 'hi')")
old.db.commit()
assert inbox.deliver(old) == 1 and inbox.owner_sends(old, "a photo", attachments=["media/x.jpg"])

# --- hearing: which transcriber, by what's set up -------------------------------------------
saved_env = {k: os.environ.pop(k, None) for k in ("PERRO_HEARING", "GROQ_API_KEY", "OPENAI_API_KEY")}
os.environ["PERRO_HEARING"] = "groq"
assert senses.hearing() is None
os.environ["GROQ_API_KEY"] = "test"
assert senses.hearing() == "groq"
os.environ["PERRO_HEARING"] = "openai"
assert senses.hearing() is None
for k, v in saved_env.items():
    os.environ.pop(k, None)
    if v is not None:
        os.environ[k] = v
assert "isn't a recording" in tools.run(m, "transcribe", {"path": "pic.png"})

# --- saving from email and Drive, through fake relays ---------------------------------------
asked = []


def fake_call(path, _limit=None, **params):
    asked.append(path)
    if path == "perro/email/read":
        return {"message": {"payload": {"parts": [{"filename": "notes.txt", "body": {"size": 5, "data": "aGVsbG8"}},
                                                  {"filename": "Plan.PDF", "body": {"size": 9,
                                                   "attachmentId": "ANGjdJ_abcdefghij"}}]}}}
    if path == "perro/email/attachment":
        return {"data": base64.urlsafe_b64encode(b"%PDF-plan").decode().rstrip("=")}
    if path == "perro/drive/download":
        return {"data": base64.b64encode(PNG).decode()}
    raise AssertionError(path)


n8n.call = fake_call
text = tools.run(m, "save_attachment", {"id": "abc123def", "name": "plan.pdf (9 bytes)"})
assert "Saved Plan.PDF" in text and "read_file reads it" in text and asked[-1] == "perro/email/attachment", text
assert next(ws.rglob("Plan.pdf")).read_bytes() == b"%PDF-plan"
text = tools.run(m, "save_attachment", {"id": "abc123def", "name": "notes.txt"})
assert "Saved notes.txt" in text and asked[-1] == "perro/email/read", "inline data needs no second call"
assert "has no attachment called" in tools.run(m, "save_attachment", {"id": "abc123def", "name": "x.zip"})
assert "id must be" in tools.run(m, "save_attachment", {"id": "../x", "name": "a"})

google._google = lambda *a, **k: {"mimeType": "image/png", "name": "chart.png", "size": str(len(PNG))}
text = tools.run(m, "save_drive_file", {"id": "1AbCdEfGhIjKlMnOp"})
assert "Saved chart.png" in text and "look_at shows it" in text and "your Drive" in text, text
google._google = lambda *a, **k: {"mimeType": "application/vnd.google-apps.document", "name": "Doc"}
assert "read_drive_file reads directly" in tools.run(m, "save_drive_file", {"id": "1AbCdEfGhIjKlMnOp"})

# --- a browser screenshot: saved, and shown if the model sees -------------------------------
browser._act = lambda memory, command, raw=False: {"url": "https://a.test/", "title": "A map",
                                                   "image": base64.b64encode(b"\xff\xd8 map").decode()}
senses.set_sight(m, Fake([]))
text = tools.run(m, "browse_screenshot", {})
assert "Screenshot of A map" in text and "attached to this result" in text, text
assert senses.take_shown(m)[0]["name"].endswith(".jpg")
senses.set_sight(m, Fake([], sees=False))
assert "can't see images" in tools.run(m, "browse_screenshot", {}) and senses.take_shown(m) == []

# --- a worker can look too -------------------------------------------------------------------
workers._spawn = lambda h, wid: None
assert tools.run(m, "start_worker", {"task": "Look at pic.png and say what's in it, briefly."}).startswith("Started")
f = Fake([{"thought": "look", "tool": "look_at", "args": {"path": "pic.png"}},
          {"thought": "done", "report": "It's a picture.", "wake_pip": False}])
workers.run(home, 1, f)
assert [i["name"] for i in f.seen[1][-1]["images"]] == ["pic.png"], f.seen[1][-1]

print("senses ok")
