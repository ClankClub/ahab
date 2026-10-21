"""Claude models sometimes write a tool call in their own format instead of
the JSON asked for; the heartbeat reads it as the tool request it is. These
are shaped like real slips from a Sonnet brain."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from ahab import heartbeat

edit = ('<invoke name="edit_file">\n<parameter name="path">USER.md</parameter>\n'
        '<parameter name="old">## Basics\n</parameter>\n<parameter name="new">## Basics\n\n- Call them T.\n</parameter>\n'
        '</invoke>\n')
reply, rest = heartbeat.parse_reply(edit)
assert reply["tool"] == "edit_file" and rest == ""
assert reply["args"] == {"path": "USER.md", "old": "## Basics\n", "new": "## Basics\n\n- Call them T.\n"}

# A tool call, then the final answer it expected to give: the call is used, the rest dropped (and logged).
both = ('<function_calls>\n<invoke name="message_owner">\n<parameter name="text">You\'re welcome.</parameter>\n'
        '</invoke>\n</function_calls>\n\n{"thought": "Sleep.", "message": null, "next_wake_minutes": 1440}')
reply, rest = heartbeat.parse_reply(both)
assert reply["tool"] == "message_owner" and reply["args"] == {"text": "You're welcome."}
assert rest.startswith('{"thought"') and "function_calls" not in rest

# Numbers and lists come through as such; a plain word stays a string.
reply, _ = heartbeat.parse_reply('<invoke name="shell"><parameter name="command">ls</parameter>'
                                 '<parameter name="timeout">30</parameter></invoke>')
assert reply["args"] == {"command": "ls", "timeout": 30}

# JSON first means JSON: an <invoke> inside a JSON string isn't a tool call.
reply, _ = heartbeat.parse_reply('{"thought": "x", "message": "they wrote <invoke name=\\"a\\"></invoke>", '
                                 '"next_wake_minutes": 60}')
assert "tool" not in reply and reply["next_wake_minutes"] == 60
print("ok")
