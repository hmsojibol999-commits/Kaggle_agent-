"""Tests for the vision check, part-word filter and end-screen social icons."""
import os
os.environ.setdefault("USE_GROQ", "1")   # the Groq code paths stay tested even though production defaults to Cloudflare
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from PIL import Image  # noqa: E402

from engine import build as B  # noqa: E402
from engine import config as C  # noqa: E402
from engine import errors as E  # noqa: E402
from engine import prepare as P  # noqa: E402
from engine import render as R  # noqa: E402
from engine import vision as V  # noqa: E402
from engine import words as Wd  # noqa: E402
from engine.selection import Pick, Selection  # noqa: E402
from engine.timeline import WordAudio, build_timeline  # noqa: E402


class ParseVerdictTests(unittest.TestCase):
    def test_plain_json(self):
        self.assertEqual(V.parse_verdict('{"verdict":"yes","main_subject":"cruise ship"}')["verdict"], "yes")

    def test_fenced_and_noisy(self):
        r = V.parse_verdict('Sure!\n```json\n{"verdict": "NO", "main_subject": "bird cage"}\n```')
        self.assertEqual((r["verdict"], r["subject"]), ("no", "bird cage"))

    def test_reasoning_block_is_ignored(self):
        r = V.parse_verdict('<think>maybe a {"verdict":"no"} cage?</think>\n{"verdict":"yes","main_subject":"kettle"}')
        self.assertEqual((r["verdict"], r["subject"]), ("yes", "kettle"))

    def test_garbage_is_unsure(self):
        for t in ("", "I think so", "{bad", '{"verdict":"maybe"}', "[]"):
            self.assertEqual(V.parse_verdict(t)["verdict"], "unsure")


class CheckPhotoTests(unittest.TestCase):
    def _resp(self, status, body):
        r = mock.Mock()
        r.status_code, r.text = status, str(body)
        r.json.return_value = body
        return r

    def test_ok(self):
        body = {"choices": [{"message": {"content": '{"verdict":"yes","main_subject":"kettle"}'}}]}
        with mock.patch("engine.vision.requests.post", return_value=self._resp(200, body)):
            r = V.check_photo("kettle", "http://x/y.jpg", key="k")
        self.assertEqual((r["verdict"], r["error"]), ("yes", None))

    def test_no_key_is_unsure_not_error(self):
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            r = V.check_photo("kettle", "http://x/y.jpg", key="")
        self.assertEqual((r["verdict"], r["error"]), ("unsure", E.GROQ_NO_KEY))

    def test_model_missing(self):
        with mock.patch("engine.vision.requests.post", return_value=self._resp(404, {})):
            r = V.check_photo("kettle", "http://x/y.jpg", key="k")
        self.assertEqual(r["error"], E.GROQ_MODEL)

    def test_retired_model_400_is_model_error_immediately(self):
        body = {"error": {"code": "model_decommissioned", "message": "The model has been decommissioned"}}
        with mock.patch("engine.vision.requests.post", return_value=self._resp(400, body)) as post:
            r = V.check_photo("kettle", "http://x/y.jpg", key="k")
        self.assertEqual(r["error"], E.GROQ_MODEL)
        self.assertEqual(post.call_count, 1)                    # no pointless retries

    def test_default_model_is_not_the_retired_one(self):
        self.assertNotIn("llama-4-scout", V.DEFAULT_MODEL)
        self.assertNotIn("llama-4-scout", C.Settings().vision_model)
        self.assertNotIn("llama-4-scout", (C.ROOT / "config.yaml").read_text())

    def test_rate_limit_ends_unsure(self):
        with mock.patch("engine.vision.requests.post", return_value=self._resp(429, {})), \
                mock.patch("engine.vision.time.sleep"):
            r = V.check_photo("kettle", "http://x/y.jpg", key="k")
        self.assertEqual((r["verdict"], r["error"]), ("unsure", E.GROQ_RATE))

    def test_data_url(self):
        with tempfile.TemporaryDirectory() as d:
            f = Path(d) / "a.png"
            Image.new("RGB", (2000, 1000), (200, 50, 50)).save(f)
            u = V.data_url_from_file(f)
        self.assertTrue(u.startswith("data:image/jpeg;base64,"))
        self.assertLess(len(u), 200_000)


class FilterPhotosTests(unittest.TestCase):
    PH = [{"id": i, "large": f"u{i}"} for i in range(10)]

    def test_keeps_only_yes_up_to_limit(self):
        verdicts = {0: "no", 1: "yes", 2: "unsure", 3: "yes", 4: "yes", 5: "yes", 6: "yes", 7: "yes"}
        calls = []

        def fake(word, url, model, key):
            calls.append(url)
            return {"verdict": verdicts.get(int(url[1:]), "no"), "subject": "", "error": None}

        kept, rep = V.filter_photos("kettle", self.PH, 4, check=fake)
        self.assertEqual([p["id"] for p in kept], [1, 3, 4, 5])
        self.assertTrue(all(p["vision"] == "yes" for p in kept))
        self.assertEqual(len(calls), 6)                      # stopped as soon as 4 were found
        self.assertFalse(rep["unavailable"])

    def test_unavailable_model_keeps_photos_unchecked(self):
        fake = lambda *a: {"verdict": "unsure", "subject": "", "error": E.GROQ_MODEL}  # noqa: E731
        kept, rep = V.filter_photos("kettle", self.PH, 4, check=fake)
        self.assertTrue(rep["unavailable"])
        self.assertEqual(len(kept), 4)
        self.assertTrue(all(p["vision"] == "unsure" for p in kept))

    def test_repeated_failures_stop_early(self):
        calls = []

        def fake(*a):
            calls.append(1)
            return {"verdict": "unsure", "subject": "", "error": E.GROQ_BAD}

        _, rep = V.filter_photos("kettle", self.PH, 4, check=fake)
        self.assertTrue(rep["unavailable"])
        self.assertEqual(len(calls), 3)


class PartWordTests(unittest.TestCase):
    def test_parts_rejected(self):
        for w in ("tail", "wing", "handle", "lid", "wheels", "leaf"):
            self.assertFalse(Wd.is_valid_shape(w), w)

    def test_whole_objects_still_fine(self):
        for w in ("kettle", "cruise ship", "bird cage", "rolling pin", "wheelbarrow"):
            self.assertTrue(Wd.is_valid_shape(w), w)

    def test_builtin_list_has_no_parts(self):
        bad = [w for (w, *_rest) in Wd.BUILTIN_WORDS if not Wd.is_valid_shape(w)]
        self.assertEqual(bad, [])

    def test_prompt_demands_whole_objects(self):
        self.assertIn("WHOLE object", Wd.PROMPT)


class PrepareVisionTests(unittest.TestCase):
    def test_vision_off_in_mock_and_without_key(self):
        s = C.Settings()
        self.assertFalse(P.vision_on(s, True))
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            self.assertFalse(P.vision_on(s, False))
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "k"}):
            self.assertTrue(P.vision_on(s, False))
            s.vision_check = False
            self.assertFalse(P.vision_on(s, False))

    def test_warning_added_once_when_unavailable(self):
        s, warns = C.Settings(), []
        ph = [{"id": i, "large": "u"} for i in range(8)]
        fake = lambda *a: {"verdict": "unsure", "subject": "", "error": E.GROQ_MODEL}  # noqa: E731
        with mock.patch("engine.vision.check_photo", fake), mock.patch("engine.prepare.VISION_PAUSE", 0):
            P.vision_filter(s, "kettle", ph, 4, warns)
            P.vision_filter(s, "teapot", ph, 4, warns)
        self.assertEqual([w["code"] for w in warns], ["vision_unavailable"])


class BuildGateTests(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.imgs = []
        for i in range(2):
            f = Path(self.d.name) / f"w{i}.png"
            Image.new("RGB", (400, 300), (10, 10, 10)).save(f)
            self.imgs.append(R.WordRes(["kettle", "teapot"][i], str(f), (0.5, 0.5), (0.5, 0.5)))
        self.sel = Selection("sid123456", "auto",
                             [Pick("kettle", 1, None, (.5, .5), (.5, .5)),
                              Pick("teapot", 2, None, (.5, .5), (.5, .5))])
        self.credits = [{"word": "kettle"}, {"word": "teapot"}]
        self.s = C.Settings()

    def tearDown(self):
        self.d.cleanup()

    def _run(self, verdicts, **kw):
        it = iter(verdicts)
        fake = lambda *a, **k: {"verdict": next(it), "subject": "bird cage", "error": None}  # noqa: E731
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "k"}), mock.patch("engine.vision.check_photo", fake):
            return B.vision_gate(self.sel, self.imgs, self.s, self.credits, False, **kw)

    def test_all_yes(self):
        self.assertEqual(self._run(["yes", "yes"]), [])
        self.assertEqual(self.credits[0]["vision"], "yes")

    def test_clear_no_blocks_with_word_in_detail(self):
        with self.assertRaises(E.WCError) as cm:
            self._run(["yes", "no"])
        self.assertEqual(cm.exception.code, E.IMAGE_MISMATCH)
        self.assertIn("teapot", cm.exception.detail)
        self.assertIn("bird cage", cm.exception.detail)

    def test_unsure_only_adds_note(self):
        notes = self._run(["unsure", "yes"])
        self.assertEqual(len(notes), 1)
        self.assertIn("kettle", notes[0])

    def test_gate_can_be_disabled_and_skips_mock_and_no_key(self):
        self.s.vision_gate = False
        self.assertEqual(self._run([]), [])
        self.s.vision_gate = True
        self.assertEqual(B.vision_gate(self.sel, self.imgs, self.s, self.credits, True), [])
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": ""}):
            self.assertEqual(B.vision_gate(self.sel, self.imgs, self.s, self.credits, False), [])

    def test_uploads_are_not_checked(self):
        self.sel.picks[0] = Pick("kettle", None, "sessions/sid123456/uploads/a.jpg", (.5, .5), (.5, .5))
        called = []
        fake = lambda *a, **k: called.append(1) or {"verdict": "yes", "subject": "", "error": None}  # noqa: E731
        with mock.patch.dict(os.environ, {"GROQ_API_KEY": "k"}), mock.patch("engine.vision.check_photo", fake):
            B.vision_gate(self.sel, self.imgs, self.s, self.credits, False)
        self.assertEqual(len(called), 1)


class SocialOutroTests(unittest.TestCase):
    def _scene(self, social_dir, handle=""):
        d = tempfile.mkdtemp()
        f = Path(d) / "w.png"
        Image.new("RGB", (1400, 1000), (90, 140, 200)).save(f)
        words = [R.WordRes(w, str(f), (0.5, 0.5), (0.5, 0.5)) for w in ("kettle", "teapot", "ladle")]
        tl = build_timeline(1.1, [WordAudio(0.9, 0.6, 0.9, 6)] * 3, 1.7)
        with mock.patch.object(C, "SOCIAL_DIR", Path(social_dir)):
            return R.Scene(tl, words, C.THEMES[1], handle), tl

    def _icons(self, names, size=(300, 300)):
        d = tempfile.mkdtemp()
        for n in names:
            Image.new("RGBA", size, (255, 0, 0, 255)).save(Path(d) / f"{n}.png")
        return d

    def test_no_icons_no_handle_draws_nothing_extra(self):
        sc, _ = self._scene(tempfile.mkdtemp())
        self.assertEqual(sc.social, [])
        self.assertIsNone(sc.handle_pill)

    def test_default_handle_is_empty(self):
        self.assertEqual(C.Settings().channel_handle, "")
        self.assertNotIn("YourChannel", (C.ROOT / "config.yaml").read_text())

    def test_icons_load_in_fixed_order_and_fit_safe_zone(self):
        sc, _ = self._scene(self._icons(["facebook", "youtube", "tiktok", "instagram"]))
        self.assertEqual(len(sc.social), 4)
        side = sc.social[0].width / R.D.SS
        total = 4 * side + 3 * 44
        self.assertLessEqual(CENTER := C.CENTER_X + total / 2 + side * 0.1, C.SAFE_X1 + 1)
        self.assertGreaterEqual(C.CENTER_X - total / 2 - side * 0.1, C.SAFE_X0 - 1)

    def test_partial_set_and_wide_logo_aspect_kept(self):
        sc, _ = self._scene(self._icons(["youtube", "tiktok"], size=(600, 200)))
        self.assertEqual(len(sc.social), 2)
        self.assertEqual(sc.social[0].width, sc.social[0].height)      # square tile, logo letter-boxed

    def test_broken_logo_file_is_skipped(self):
        d = self._icons(["youtube"])
        (Path(d) / "tiktok.png").write_bytes(b"not an image")
        sc, _ = self._scene(d)
        self.assertEqual(len(sc.social), 1)

    def test_outro_frame_shows_icons_and_stays_in_safe_zone(self):
        sc, tl = self._scene(self._icons(["youtube", "instagram", "tiktok", "facebook"]))
        blank, _ = self._scene(tempfile.mkdtemp())
        t = tl.outro_start + 1.6
        a, b = sc.frame(round(t * C.FPS)), blank.frame(round(t * C.FPS))
        diff = Image.eval(__import__("PIL.ImageChops", fromlist=["x"]).difference(a, b).convert("L"),
                          lambda v: 255 if v > 40 else 0)
        box = diff.getbbox()
        self.assertIsNotNone(box)
        self.assertGreaterEqual(box[0], C.SAFE_X0)
        self.assertLessEqual(box[2], C.SAFE_X1)
        self.assertGreaterEqual(box[1], 900)
        self.assertLessEqual(box[3], 1260)


if __name__ == "__main__":
    unittest.main()


class V5FixTests(unittest.TestCase):
    def test_truncated_json_keeps_complete_items(self):
        from engine import words as W
        cut = ('{"words": [{"word": "quokka", "search_query": "quokka animal"}, '
               '{"word": "ocarina", "search_query": "oca')
        self.assertEqual([x["word"] for x in W.parse_words_json(cut)], ["quokka"])

    def test_fallback_is_curated_only(self):
        import random
        from engine import words as W
        items, src = W.fallback_words(random.Random(1))
        self.assertEqual(src, "builtin")
        self.assertGreater(len(items), 100)

    def test_dark_border_detection(self):
        from PIL import Image
        im = Image.new("RGB", (200, 200), (200, 120, 60))
        self.assertFalse(V.has_dark_borders(im))
        for y in range(0, 40):
            for x in range(200):
                im.putpixel((x, y), (0, 0, 0))
        self.assertTrue(V.has_dark_borders(im))
