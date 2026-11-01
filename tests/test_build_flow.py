"""Build orchestration: failures must be recorded for the UI and must never touch used_words."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import build, errors as E  # noqa: E402
from engine.state import State  # noqa: E402

SID = "20261021-1000-aa11"
SEL = json.dumps({"v": 1, "sid": SID, "theme": "auto", "x": ["junk"],
                  "w": [{"w": "umbrella stand", "p": 1001, "f": [0.4, 0.5]},
                        {"w": "wheelbarrow", "p": 1002, "f": [0.5, 0.5]},
                        {"w": "wood chisel", "p": 1003, "f": [0.5, 0.5]}]})


class BuildFailureTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.remote = f"{self.d.name}/remote.git"
        subprocess.run(["git", "init", "-q", "--bare", self.remote], check=True)
        self.url = f"file://{self.remote}"
        st = State(f"{self.d.name}/seed", self.url).init()
        st.append_lines("used_words.txt", ["kettle"])
        st.commit_push("seed")

    def tearDown(self):
        self.d.cleanup()

    def run_build(self, selection=SEL, session=SID):
        return build.main(["--session", session, "--selection", selection, "--mock", "--no-release",
                           "--out", f"{self.d.name}/out", "--state-dir", f"{self.d.name}/s", "--remote", self.url])

    def read_state(self):
        return State(f"{self.d.name}/v", self.url).init()

    def test_render_failure_is_reported_and_used_words_untouched(self):
        with mock.patch("engine.render.render_video", side_effect=RuntimeError("ffmpeg exploded")):
            self.assertEqual(self.run_build(), 1)
        st = self.read_state()
        self.assertEqual(st.read_lines("used_words.txt"), ["kettle"])
        self.assertEqual(st.read_lines("bad_words.txt"), [])                 # skipped words not recorded either
        res = st.read_json(f"sessions/{SID}/build.json")
        self.assertEqual(res["state"], "error")
        self.assertEqual(res["code"], E.RENDER_FAILED)
        self.assertIn("ffmpeg exploded", res["detail"])

    def test_quality_gate_failure_is_reported(self):
        def fake_render(tl, words, theme, handle, audio, out, **kw):
            Path(out).write_bytes(b"not a video")
        with mock.patch("engine.render.render_video", side_effect=fake_render):
            self.assertEqual(self.run_build(), 1)
        st = self.read_state()
        self.assertEqual(st.read_lines("used_words.txt"), ["kettle"])
        res = st.read_json(f"sessions/{SID}/build.json")
        self.assertEqual(res["state"], "error")
        self.assertIn(res["code"], (E.RENDER_FAILED, E.GATE_FAILED))

    def test_bad_selection_is_reported_with_code(self):
        self.assertEqual(self.run_build(selection="{broken"), 1)
        res = self.read_state().read_json(f"sessions/{SID}/build.json")
        self.assertEqual(res["code"], E.SELECTION_BAD)       # the UI can show a Bengali message for it

    def test_unsafe_session_id_is_never_used_as_a_path(self):
        self.assertEqual(self.run_build(selection="{broken", session="../../evil"), 1)
        st = self.read_state()
        self.assertFalse((Path(st.path) / "sessions").exists() and any((Path(st.path) / "sessions").iterdir()))
        self.assertFalse((Path(self.d.name) / "evil").exists())

    def test_session_mismatch(self):
        self.assertEqual(self.run_build(session="20261021-1000-zz99"), 1)
        res = self.read_state().read_json("sessions/20261021-1000-zz99/build.json")
        self.assertEqual(res["code"], E.SELECTION_BAD)

    def test_missing_upload_is_clear(self):
        sel = json.dumps({"v": 1, "sid": SID, "w": [
            {"w": "umbrella stand", "u": f"sessions/{SID}/uploads/missing.jpg"},
            {"w": "wheelbarrow", "p": 2}, {"w": "wood chisel", "p": 3}]})
        self.assertEqual(self.run_build(selection=sel), 1)
        res = self.read_state().read_json(f"sessions/{SID}/build.json")
        self.assertEqual(res["code"], E.IMAGE_MISSING)


if __name__ == "__main__":
    unittest.main()
