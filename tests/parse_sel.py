"""Reads a selection JSON on stdin and prints the parsed result (used by test_core.js)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.selection import parse_selection
s = parse_selection(sys.stdin.read(), int(sys.argv[1]))
print(json.dumps({"sid": s.sid, "theme": s.theme, "skipped": s.skipped,
                  "picks": [{"w": p.word, "p": p.photo_id, "u": p.upload, "f": p.focus, "o": p.offset} for p in s.picks]}))
