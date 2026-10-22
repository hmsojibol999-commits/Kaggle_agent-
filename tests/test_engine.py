"""Unit tests (stdlib unittest). Run:  python -m unittest discover -s tests -v"""
import json
import math
import os
import random
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from engine import config as C  # noqa: E402
from engine import draw as D  # noqa: E402
from engine import errors as E  # noqa: E402
from engine import geometry as G  # noqa: E402
from engine import pixabay as P  # noqa: E402
from engine import sfx, tts, words as Wd  # noqa: E402
from engine.selection import parse_selection  # noqa: E402
from engine.timeline import (WordAudio, audio_events, build_timeline, ease_out_back,  # noqa: E402
                             spring_scale, zoom_at)


def sample_tl():
    return build_timeline(1.1, [WordAudio(0.9, 0.6, 0.9, 13), WordAudio(0.9, 0.7, 1.0, 10),
                                WordAudio(0.9, 0.5, 0.8, 6)], 1.7)


class TimelineTests(unittest.TestCase):
    def test_total_is_whole_frames(self):
        tl = sample_tl()
        self.assertAlmostEqual(tl.total * C.FPS, round(tl.total * C.FPS), places=6)
        self.assertEqual(tl.frames(), round(tl.total * C.FPS))

    def test_segments_are_contiguous(self):
        tl = sample_tl()
        t = tl.hook_dur
        for seg in tl.words:
            self.assertAlmostEqual(seg.start, t, places=6)
            t = seg.end
        self.assertAlmostEqual(tl.outro_start, t, places=6)
        self.assertAlmostEqual(tl.outro_start + tl.outro_dur, tl.total, places=6)

    def test_word_order_and_real_durations(self):
        tl = sample_tl()
        a = WordAudio(0.9, 0.6, 0.9, 13)
        s = tl.words[0]
        self.assertAlmostEqual(s.q_start, C.SLIDE_IN_SEC)
        self.assertAlmostEqual(s.q_end - s.q_start, a.question)       # from real audio
        self.assertGreaterEqual(s.cd_end - s.cd_start, C.COUNTDOWN_MIN)
        self.assertAlmostEqual(s.reveal, s.cd_end)
        self.assertGreaterEqual(s.a_start, s.reveal)
        self.assertAlmostEqual(s.a_end - s.a_start, a.answer)
        self.assertGreaterEqual(s.slow_start, s.a_end)
        self.assertAlmostEqual(s.slow_end - s.slow_start, a.slow)
        self.assertGreater(s.content_end, s.slow_end)
        self.assertGreaterEqual(s.duration, C.MIN_WORD_SEC + C.TRANSITION_SEC)

    def test_longer_audio_stretches_segment(self):
        short = build_timeline(1.0, [WordAudio(0.6, 0.4, 0.5, 5)], 1.0).words[0].duration
        long_ = build_timeline(1.0, [WordAudio(2.5, 2.0, 3.0, 5)], 1.0).words[0].duration
        self.assertGreater(long_, short)

    def test_duration_inside_gate_for_three_words(self):
        tl = sample_tl()
        self.assertTrue(C.DURATION_RANGE[0] <= tl.total <= C.DURATION_RANGE[1], tl.total)

    def test_spelling_step_clamped(self):
        for slow, n in [(0.2, 20), (5.0, 3)]:
            seg = build_timeline(1, [WordAudio(.8, .5, slow, n)], 1).words[0]
            self.assertTrue(0.10 <= seg.spell_step <= 0.30)

    def test_audio_events_cover_everything(self):
        tl = sample_tl()
        ev = audio_events(tl)
        keys = {k for _, kind, k in ev if kind == "voice"}
        self.assertEqual(keys, {"hook", "outro", "q0", "q1", "q2", "a0", "a1", "a2", "s0", "s1", "s2"})
        self.assertEqual(sum(1 for _, _, k in ev if k == "key"), 13 + 10 + 6)
        self.assertEqual(sum(1 for _, _, k in ev if k == "tick"), 6)
        self.assertEqual(sum(1 for _, _, k in ev if k == "ding"), 3)
        self.assertTrue(all(0 <= t < tl.total for t, _, _ in ev))

    def test_easing_and_zoom(self):
        self.assertAlmostEqual(spring_scale(0), 0.6)
        self.assertAlmostEqual(spring_scale(10), 1.0)
        self.assertGreater(max(spring_scale(x / 100) for x in range(41)), 1.07)
        self.assertAlmostEqual(ease_out_back(1.0), 1.0)
        self.assertAlmostEqual(zoom_at(0, 6.0), 1.0)
        self.assertAlmostEqual(zoom_at(6.0, 6.0), C.KEN_BURNS_END)
        self.assertLessEqual(max(abs(zoom_at(i / 30, 6) - zoom_at((i + 1) / 30, 6)) for i in range(179)), 0.001)


class LayoutTests(unittest.TestCase):
    """Everything that carries information stays inside the safe zone."""

    def inside(self, x0, y0, x1, y1):
        self.assertGreaterEqual(x0, C.SAFE_X0 - 0.5, (x0, y0, x1, y1))
        self.assertLessEqual(x1, C.SAFE_X1 + 0.5, (x0, y0, x1, y1))
        self.assertGreaterEqual(y0, C.SAFE_Y0 - 0.5, (x0, y0, x1, y1))
        self.assertLessEqual(y1, C.SAFE_Y1 + 0.5, (x0, y0, x1, y1))

    def test_constants_in_frame_and_safe(self):
        for box in (C.TITLE_BOX, C.ANSWER_BOX, C.PILL_BOX, C.LABEL_BOX, C.TITLE_TEXT_BOX):
            self.inside(*box)
        self.assertEqual((C.CARD_X, C.CARD_Y, C.CARD_W, C.CARD_H), (90, 330, 900, 1000))
        self.assertLessEqual(C.CARD_X + C.CARD_W, C.W)
        # documented exception: the spec's card sticks 30 px past x=960
        self.assertEqual(C.CARD_X + C.CARD_W - C.SAFE_X1, 30)

    def test_title_and_pills(self):
        t = D.build_title(C.THEMES[0])
        x0 = C.CENTER_X - t.width / 2
        self.inside(x0, C.TITLE_TEXT_BOX[1] - 4, x0 + t.width, C.TITLE_TEXT_BOX[1] - 4 + t.height)
        for n in (3,):
            for i in range(n):
                p = D.build_pills(C.THEMES[0], i, n)
                px = C.CENTER_X - p.width / 2
                self.inside(px, C.PILL_BOX[1], px + p.width, C.PILL_BOX[1] + p.height)

    def test_ring_bounds(self):
        ring = D.draw_ring(C.THEMES[0], 1.0, 2, 1.0)
        cx, cy = C.RING_CENTER
        self.inside(cx - ring.width / 2, cy - ring.height / 2, cx + ring.width / 2, cy + ring.height / 2 - 6)
        # ring pops to at most 1.1x for the number bump
        big = D.draw_ring(C.THEMES[0], 1.0, 2, 1.1)
        self.assertLessEqual(cy + big.height / 2, C.SAFE_Y1 + 12)

    def test_label_and_tiles_for_long_words(self):
        for theme in C.THEMES:
            for word in ("wheelbarrow", "umbrella stand", "stapler", "tape measure", "pomegranate"):
                lab = D.build_label(theme, word)
                visible = lab.width / D.SS - 20            # sprite has a 10 px transparent margin per side
                half = visible / 2 * C.LABEL_POP_MAX
                self.assertLessEqual(half, C.SAFE_HALF_W + 0.5, (theme["name"], word, half))
                tiles, total, ts = D.build_tiles(theme, word)
                self.assertLessEqual(total / 2, C.SAFE_HALF_W - 20 + 1, (word, total))
                self.assertLessEqual(ts * 1.1, C.SPELL_Y[1] - C.SPELL_Y[0] + 8)

    def test_hook_and_outro_content(self):
        total = 3 * 260 + 2 * 25
        x0, x1 = C.CENTER_X - total / 2, C.CENTER_X + total / 2
        self.assertGreaterEqual(x0, C.SAFE_X0)
        self.assertLessEqual(x1, C.SAFE_X1)

    def test_theme_contrast(self):
        for t in C.THEMES:
            self.assertGreaterEqual(C.contrast_ratio(t["label_fg"], t["label_bg"]), 4.5, t["name"])
            self.assertGreaterEqual(C.contrast_ratio(t["ink"], t["bg1"]), 4.5, t["name"])
            self.assertGreaterEqual(C.contrast_ratio(t["title_edge"], t["bg1"]), 3.0, t["name"])  # large text
        self.assertEqual(len(C.THEMES), 6)

    def test_theme_rotation_is_stable_and_varied(self):
        self.assertEqual(C.pick_theme("abc")["name"], C.pick_theme("abc")["name"])
        names = {C.pick_theme(f"20260101-0000-{i:04d}")["name"] for i in range(40)}
        self.assertGreaterEqual(len(names), 4)
        self.assertEqual(C.pick_theme("x", "teal")["name"], "teal")


class GeometryTests(unittest.TestCase):
    def test_cover_and_base_box(self):
        for iw, ih in [(4000, 3000), (3000, 4000), (1600, 1200), (2000, 2000)]:
            x0, y0, x1, y1 = G.base_box(iw, ih)
            self.assertAlmostEqual((x1 - x0) / (y1 - y0), C.CARD_W / C.CARD_H, places=6)
            self.assertTrue(0 <= x0 and x1 <= iw + 1e-6 and 0 <= y0 and y1 <= ih + 1e-6)

    def test_view_box_stays_inside_and_zoom_shrinks(self):
        rnd = random.Random(1)
        for _ in range(200):
            iw, ih = rnd.choice([(4000, 3000), (3000, 4000), (1600, 1200)])
            f = (rnd.random(), rnd.random())
            o = (rnd.random(), rnd.random())
            z = rnd.uniform(1.0, 1.12)
            x0, y0, x1, y1 = G.view_box(iw, ih, f, o, z)
            self.assertTrue(-1e-6 <= x0 and x1 <= iw + 1e-6 and -1e-6 <= y0 and y1 <= ih + 1e-6)
            bx = G.base_box(iw, ih, o)
            self.assertAlmostEqual((x1 - x0) * z, bx[2] - bx[0], places=4)

    def test_focus_keeps_screen_position_when_room(self):
        iw, ih = 4000, 3000
        f = (0.5, 0.5)
        p0 = G.focus_in_card(G.view_box(iw, ih, f, (0.5, 0.5), 1.0), f, iw, ih)
        p1 = G.focus_in_card(G.view_box(iw, ih, f, (0.5, 0.5), 1.12), f, iw, ih)
        self.assertAlmostEqual(p0[0], p1[0], delta=0.5)
        self.assertAlmostEqual(p0[1], p1[1], delta=0.5)

    def test_upscale_detection(self):
        self.assertFalse(G.needs_upscale(4000, 3000))
        self.assertTrue(G.needs_upscale(800, 600))
        self.assertFalse(G.needs_upscale(900, 1000, 1.0))      # exactly card size: 1.0x
        self.assertTrue(G.needs_upscale(900, 1000, 1.12))      # Ken Burns would enlarge it
        self.assertTrue(G.needs_upscale(890, 1000, 1.0))

    def test_arrow_always_inside_card_safe_rect_and_points_at_focus(self):
        rect = G.card_safe_rect()
        worst = 0
        for ix in range(0, 21):
            for iy in range(0, 21):
                px, py = ix / 20 * C.CARD_W, iy / 20 * C.CARD_H
                a = G.arrow_geometry(px, py)
                ux, uy = math.cos(a["angle"]), math.sin(a["angle"])
                # biggest drawn size (1.08x) at both ends of the pulse: whole polygon stays safe
                for sh in (-G.ARROW_PULSE, 0, G.ARROW_PULSE):
                    tip = (a["tip"][0] + ux * sh, a["tip"][1] + uy * sh)
                    tail = (a["tail"][0] + ux * sh, a["tail"][1] + uy * sh)
                    for (x, y) in G.arrow_polygon(tip, tail, G.ARROW_MAX_SCALE):
                        self.assertTrue(rect[0] <= x <= rect[2] and rect[1] <= y <= rect[3], (px, py, sh, x, y))
                # the tip stays close to the focus (only pulled inwards near edges) and travels towards it
                dist = math.hypot(a["tip"][0] - px, a["tip"][1] - py)
                self.assertLess(dist, G.ARROW_GAP + math.hypot(G.ARROW_INSET + 30, G.ARROW_INSET) + 1)   # +30: card sticks past the safe zone
                ax = min(max(px, rect[0] + G.ARROW_INSET), rect[2] - G.ARROW_INSET)
                ay = min(max(py, rect[1] + G.ARROW_INSET), rect[3] - G.ARROW_INSET)
                self.assertAlmostEqual(math.hypot(a["tip"][0] - ax, a["tip"][1] - ay), G.ARROW_GAP, places=4)
                self.assertGreater((ax - a["tail"][0]) * ux + (ay - a["tail"][1]) * uy, 0)
                worst = max(worst, a["length"])
        self.assertLessEqual(worst, G.ARROW_MAX)

    def test_arrow_in_the_middle_hits_the_focus_exactly(self):
        a = G.arrow_geometry(450, 500)
        self.assertAlmostEqual(math.hypot(a["tip"][0] - 450, a["tip"][1] - 500), G.ARROW_GAP, places=4)

    def test_arrow_comes_from_nearest_edge_when_room(self):
        a = G.arrow_geometry(600, 900)           # right half: nearest edge is right
        self.assertEqual(a["side"], "right")
        a = G.arrow_geometry(450, 200)           # 200 px from the top: enough room
        self.assertEqual(a["side"], "top")
        a = G.arrow_geometry(450, 120)           # too close to the top: next side with room
        self.assertNotEqual(a["side"], "top")

    def test_rendered_arrow_stays_in_safe_zone_in_frame_coordinates(self):
        from engine import draw as Dr
        for (px, py) in [(20, 20), (880, 980), (450, 500), (30, 500), (860, 40)]:
            a = G.arrow_geometry(px, py)
            tip = (a["tip"][0] + C.CARD_X, a["tip"][1] + C.CARD_Y)
            tail = (a["tail"][0] + C.CARD_X, a["tail"][1] + C.CARD_Y)
            img, (x0, y0) = Dr.draw_arrow(tip, tail, G.ARROW_MAX_SCALE)
            bbox = img.getchannel("A").point(lambda v: 255 if v > 160 else 0).getbbox()   # solid pixels only
            self.assertGreaterEqual(x0 + bbox[0], C.SAFE_X0 - 1)
            self.assertLessEqual(x0 + bbox[2], C.SAFE_X1 + 1)
            self.assertGreaterEqual(y0 + bbox[1], C.SAFE_Y0 - 1)
            self.assertLessEqual(y0 + bbox[3], C.SAFE_Y1 + 1)


class WordTests(unittest.TestCase):
    def test_singular_and_normalize(self):
        self.assertEqual(Wd.normalize("Scissors"), "scissors")
        self.assertEqual(Wd.normalize("Knives"), "knife")
        self.assertEqual(Wd.normalize("boxes"), "box")
        self.assertEqual(Wd.normalize("Wheelbarrows"), "wheelbarrow")
        self.assertEqual(Wd.normalize("glass"), "glass")
        self.assertEqual(Wd.normalize("Wood  Chisels!"), "wood chisel")

    def test_filter_candidates_dedupe(self):
        items = [{"word": "Hammer"}, {"word": "hammers"}, {"word": "umbrella"},
                 {"word": "bat"}, {"word": "a"}, {"word": "very long phrase here"},
                 {"word": "ladle"}, {"word": "Ladles"}, {"word": "spoon", "difficulty": 9}]
        out = Wd.filter_candidates(items, used=["umbrellas"], bad=["spoon holder"])
        words = [o["word"] for o in out]
        self.assertEqual(words, ["hammer", "ladle", "spoon"])      # dupes, used, ambiguous, long dropped
        self.assertEqual(out[-1]["difficulty"], 3)
        self.assertEqual(out[0]["search_query"], "hammer")

    def test_bad_list_blocks(self):
        out = Wd.filter_candidates([{"word": "kettle"}], used=[], bad=["Kettles"])
        self.assertEqual(out, [])

    def test_parse_words_json_variants(self):
        good = '{"words":[{"word":"ladle","search_query":"ladle spoon"},{"word":"whisk"}]}'
        self.assertEqual(len(Wd.parse_words_json(good)), 2)
        fenced = "```json\n" + good + "\n```"
        self.assertEqual(len(Wd.parse_words_json(fenced)), 2)
        chatty = "Sure! Here you go:\n" + good + "\nHope it helps."
        self.assertEqual(len(Wd.parse_words_json(chatty)), 2)
        self.assertEqual(Wd.parse_words_json('["ladle","whisk"]')[0]["word"], "ladle")
        for bad in ("", "no json here", '{"words": []}'):
            with self.assertRaises(ValueError):
                Wd.parse_words_json(bad)

    def test_builtin_fallback_is_valid(self):
        items = Wd.builtin_words(random.Random(1))
        self.assertGreater(len(items), 50)
        ok = Wd.filter_candidates(items, [], [])
        self.assertEqual(len(ok), len(items))

    def test_groq_error_classification(self):
        self.assertEqual(Wd._classify_groq_error(401, "x").code, E.GROQ_NO_KEY)
        self.assertEqual(Wd._classify_groq_error(404, "model_not_found").code, E.GROQ_MODEL)
        self.assertEqual(Wd._classify_groq_error(429, "slow").code, E.GROQ_RATE)
        self.assertEqual(Wd._classify_groq_error(400, "model terms required: accept").code, E.GROQ_TERMS)
        self.assertEqual(Wd._classify_groq_error(500, "boom").code, E.GROQ_BAD)

    def test_groq_without_key_is_clear_error(self):
        with self.assertRaises(E.WCError) as cm:
            Wd.groq_words(6, [], [], None, "m", key="")
        self.assertEqual(cm.exception.code, E.GROQ_NO_KEY)


class PixabayTests(unittest.TestCase):
    def photos(self):
        # sizes are what can really be downloaded from Pixabay (at most 1280 px)
        def ph(i, w, h, alt):
            return {"id": i, "width": w, "height": h, "alt": alt, "photographer": "P", "photographer_url": "u",
                    "url": "x", "src": {"original": "o", "large": "l", "medium": "m"}}
        return [ph(1, 1280, 960, "wood chisel, bench"), ph(2, 1280, 960, "man, holding, chisel"),
                ph(3, 1100, 1000, "chisel"), ph(4, 640, 480, "tiny, chisel"), ph(5, 1280, 1000, "carpenter, tools"),
                ph(6, 640, 1280, "wood chisel, tall"), ph(7, 1280, 1280, "wood chisel, set")]

    def test_ranking_and_person_penalty(self):
        out = P.filter_and_rank(self.photos(), "wood chisel", want=6)
        ids = [o["id"] for o in out]
        self.assertEqual(len(ids), 6)
        self.assertNotIn(4, ids)                       # 640x480 would be enlarged 2x: below every level
        self.assertEqual(ids[0], 1)                    # exact phrase match, landscape
        self.assertLess(ids.index(1), ids.index(2))    # person photo ranked lower
        top4 = [o["id"] for o in P.filter_and_rank(self.photos(), "wood chisel", want=4)]
        self.assertNotIn(2, top4)                      # soft penalty pushes it out of the top 4
        for o in out:
            self.assertTrue(o["photographer"] and o["url"])

    def test_stepwise_relaxation(self):
        few = [p for p in self.photos() if p["id"] in (1, 4)]
        few += [{"id": 10 + i, "width": 1000, "height": 800, "alt": "chisel", "src": {}, "photographer": "a"} for i in range(4)]
        out = P.filter_and_rank(few, "chisel", want=4)
        self.assertEqual(len(out), 4)
        self.assertNotIn(4, [o["id"] for o in out])
        self.assertTrue(all(G.cover_scale(o["w"], o["h"]) <= 1.30 for o in out))   # second level was needed

    def test_exclude_ids(self):
        out = P.filter_and_rank(self.photos(), "wood chisel", want=4, exclude_ids=[1, 5])
        self.assertTrue({1, 5}.isdisjoint({o["id"] for o in out}))

    def test_no_key_error(self):
        with self.assertRaises(E.WCError) as cm:
            P.Pixabay(key="").search_raw("x")
        self.assertEqual(cm.exception.code, E.PIXABAY_NO_KEY)

    def test_mock_is_deterministic(self):
        a = P.Pixabay(mock=True).candidates("lamp", "table lamp")
        b = P.Pixabay(mock=True).candidates("lamp", "table lamp")
        self.assertEqual([x["id"] for x in a], [x["id"] for x in b])
        self.assertEqual(len(a), 4)

    HIT = {"id": 195893, "pageURL": "https://pixabay.com/photos/blossom-bloom-flower-195893/", "type": "photo",
           "tags": "blossom, bloom, flower", "webformatURL": "https://pixabay.com/get/w_640.jpg",
           "largeImageURL": "https://pixabay.com/get/l_1280.jpg", "imageWidth": 4000, "imageHeight": 3000,
           "user": "Josch13", "user_id": 1664300}

    def test_normalize_hit(self):
        p = P._normalize(self.HIT)
        self.assertEqual((p["width"], p["height"]), (1280, 960))      # largeImageURL is capped at 1280 px
        self.assertEqual(p["photographer"], "Josch13")
        self.assertEqual(p["photographer_url"], "https://pixabay.com/users/Josch13-1664300/")
        self.assertEqual(p["url"], self.HIT["pageURL"])
        self.assertEqual(p["src"]["original"], self.HIT["largeImageURL"])   # no full API access
        slim = P._slim(p, "flower")
        self.assertEqual((slim["thumb"], slim["large"]), (self.HIT["webformatURL"], self.HIT["largeImageURL"]))

    def test_normalize_full_access_keeps_original(self):
        p = P._normalize(dict(self.HIT, imageURL="https://pixabay.com/get/orig.jpg"))
        self.assertEqual((p["width"], p["height"]), (4000, 3000))
        self.assertEqual(p["src"]["original"], "https://pixabay.com/get/orig.jpg")
        p = P._normalize(dict(self.HIT, fullHDURL="https://pixabay.com/get/hd.jpg"))
        self.assertEqual((p["width"], p["height"]), (1920, 1440))

    def test_normalize_rejects_unusable_hits(self):
        self.assertIsNone(P._normalize({"id": 1}))
        self.assertIsNone(P._normalize(dict(self.HIT, largeImageURL="")))
        self.assertIsNone(P._normalize(dict(self.HIT, imageWidth=0)))


class SelectionTests(unittest.TestCase):
    def sel(self, **over):
        d = {"v": 1, "sid": "20261021-1000-aa11", "theme": "blue",
             "w": [{"w": "umbrella stand", "p": 11, "f": [0.4, 0.6], "o": [0.2, 0.7]},
                   {"w": "ladle", "u": "sessions/20261021-1000-aa11/uploads/a.jpg"},
                   {"w": "Wood Chisel", "p": 33, "f": [1, 0]}], "x": ["Bad Word"]}
        d.update(over)
        return json.dumps(d)

    def test_valid(self):
        s = parse_selection(self.sel())
        self.assertEqual(s.sid, "20261021-1000-aa11")
        self.assertEqual(s.theme, "blue")
        self.assertEqual([p.word for p in s.picks], ["umbrella stand", "ladle", "wood chisel"])
        self.assertEqual(s.picks[0].focus, (0.4, 0.6))
        self.assertEqual(s.picks[0].offset, (0.2, 0.7))
        self.assertEqual(s.picks[1].focus, (0.5, 0.5))
        self.assertEqual(s.picks[1].upload, "sessions/20261021-1000-aa11/uploads/a.jpg")
        self.assertEqual(s.skipped, ["bad word"])

    def expect(self, text, code=E.SELECTION_BAD, n=3):
        with self.assertRaises(E.WCError) as cm:
            parse_selection(text, n)
        self.assertEqual(cm.exception.code, code)

    def test_invalid(self):
        self.expect("not json")
        self.expect(self.sel(v=2))
        self.expect(self.sel(sid="../etc"))
        self.expect(self.sel(w=[]))
        self.expect(self.sel(w=[{"w": "a cup", "p": 1}]))                          # wrong count
        self.expect(self.sel(w=[{"w": "ladle", "p": 1}, {"w": "Ladles", "p": 2}, {"w": "lamp", "p": 3}]))  # duplicate
        self.expect(self.sel(w=[{"w": "ladle"}, {"w": "lamp", "p": 3}, {"w": "cup", "p": 4}]))             # no photo
        self.expect(self.sel(w=[{"w": "ladle", "p": 1, "u": "x"}, {"w": "lamp", "p": 3}, {"w": "cup", "p": 4}]))
        self.expect(self.sel(w=[{"w": "ladle", "p": 1, "f": [1.5, 0]}, {"w": "lamp", "p": 3}, {"w": "cup", "p": 4}]))
        self.expect(self.sel(w=[{"w": "ladle", "u": "sessions/other/uploads/a.jpg"}, {"w": "lamp", "p": 3}, {"w": "cup", "p": 4}]))
        self.expect(self.sel(w=[{"w": "ladle", "u": "sessions/20261021-1000-aa11/uploads/../../x"}, {"w": "lamp", "p": 3}, {"w": "cup", "p": 4}]))
        self.expect(" " * 60001)

    def test_other_word_counts(self):
        two = self.sel(w=[{"w": "ladle", "p": 1}, {"w": "lamp", "p": 3}])
        self.assertEqual(len(parse_selection(two, 2).picks), 2)
        self.expect(two, n=3)


class AudioTests(unittest.TestCase):
    def test_sfx_bank(self):
        bank = sfx.make_all()
        self.assertEqual(set(bank), {"whoosh", "tick", "ding", "pop", "key", "sparkle"})
        for k, x in bank.items():
            self.assertEqual(x.dtype, np.float32)
            self.assertLessEqual(float(np.max(np.abs(x))), 1.0, k)
            self.assertGreater(float(np.max(np.abs(x))), 0.05, k)
            self.assertTrue(np.all(np.isfinite(x)), k)
        again = sfx.make_all()
        for k in bank:
            self.assertTrue(np.array_equal(bank[k], again[k]), f"{k} must be deterministic")

    def test_clip_cleanup(self):
        sr = C.AUDIO_RATE
        t = np.arange(sr // 2) / sr
        tone = np.sin(2 * np.pi * 300 * t).astype(np.float32) * 0.1
        padded = np.concatenate([np.zeros(sr // 2, np.float32), tone, np.zeros(sr, np.float32)])
        out = tts.clean_clip(padded)
        self.assertLess(len(out), len(padded) * 0.5)               # silence trimmed
        self.assertLessEqual(float(np.max(np.abs(out))), 10 ** (-2 / 20) + 1e-4)   # peak normalised
        self.assertLess(abs(float(out[0])), 0.01)                  # 30 ms fades
        self.assertLess(abs(float(out[-1])), 0.01)

    def test_mock_tone_depends_on_text(self):
        a, b = tts.mock_tone("hi there", "question"), tts.mock_tone("a much longer sentence here", "question")
        self.assertGreater(len(b), len(a))

    def test_voice_cache_key(self):
        s = C.Settings()
        with tempfile.TemporaryDirectory() as d:
            e = tts.VoiceEngine(s, d, mock=True)
            k1 = e._key("groq", "autumn", "hello")
            self.assertEqual(k1, e._key("groq", "autumn", "hello"))
            self.assertNotEqual(k1, e._key("groq", "troy", "hello"))
            self.assertNotEqual(k1, e._key("edge", "autumn", "hello"))

    def test_settings_env_override(self):
        old = {k: os.environ.get(k) for k in ("GROQ_TTS_VOICE_QUESTION", "GROQ_TEXT_MODEL")}
        try:
            os.environ["GROQ_TTS_VOICE_QUESTION"] = "diana"
            os.environ["GROQ_TEXT_MODEL"] = "some-model"
            s = C.load_settings()
            self.assertEqual(s.voice_question, "diana")
            self.assertEqual(s.text_model, "some-model")
            self.assertEqual(s.tts_model, "canopylabs/orpheus-v1-english")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


class VoiceEngineTests(unittest.TestCase):
    """The real (non-mock) voice paths, with the network calls stubbed."""

    @staticmethod
    def fake_wav(path, secs=0.6, hz=220):
        from engine import media
        x = (0.3 * np.sin(2 * np.pi * hz * np.arange(int(secs * C.AUDIO_RATE)) / C.AUDIO_RATE)).astype(np.float32)
        pad = np.zeros(int(0.2 * C.AUDIO_RATE), np.float32)
        media.write_wav(path, np.concatenate([pad, x, pad]))

    def engine(self, d, **kw):
        return tts.VoiceEngine(C.Settings(), d, mock=False, groq_key=kw.get("key", "k"))

    def test_groq_success_is_cached_and_recorded(self):
        calls = []

        def groq(text, model, voice, key, dest):
            calls.append((text, model, voice))
            self.fake_wav(dest)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(tts, "_groq_render", groq):
            e = self.engine(d)
            a = e.render("Hello", "question")
            b = e.render("Hello", "question")           # served from the cache
            self.assertEqual(len(calls), 1)
            self.assertEqual(calls[0], ("Hello", "canopylabs/orpheus-v1-english", "autumn"))
            self.assertTrue(np.array_equal(a, b))
            self.assertEqual(e.engines_used, {"groq": 2})
            self.assertEqual(e.used_files[0][0], "Hello")
            self.assertTrue(Path(e.used_files[0][1]).exists())
            self.assertLess(len(a) / C.AUDIO_RATE, 0.9)   # 0.2 s padding trimmed away
            e.render("Troy line", "answer")
            self.assertEqual(calls[-1][2], "troy")        # answer voice is the male one

    def test_vocal_tags_only_when_enabled(self):
        seen = []

        def groq(text, model, voice, key, dest):
            seen.append(text)
            self.fake_wav(dest)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(tts, "_groq_render", groq):
            s = C.Settings()
            s.use_vocal_tags = True
            e = tts.VoiceEngine(s, d, mock=False, groq_key="k")
            e.render("Hello", "question")
            self.assertEqual(seen, ["[cheerful] Hello"])
            self.assertEqual(e.used_files[0][0], "Hello")   # cache bookkeeping uses the plain text

    def test_fallback_to_edge_with_circuit_breaker(self):
        groq_calls, edge_calls = [], []

        def groq(text, model, voice, key, dest):
            groq_calls.append(text)
            raise E.WCError(E.GROQ_TERMS, "accept the terms first", "tts")

        def edge(text, voice, dest, rate="+0%"):
            edge_calls.append((text, voice))
            self.fake_wav(dest, hz=180)

        with tempfile.TemporaryDirectory() as d, mock.patch.object(tts, "_groq_render", groq), \
                mock.patch.object(tts, "_edge_render", edge):
            e = self.engine(d)
            x = e.render("Hello", "question")
            e.render("World", "answer")
            self.assertGreater(len(x), 1000)
            self.assertEqual(len(groq_calls), 1, "after a terms error Groq must not be retried for every clip")
            self.assertEqual([c[1] for c in edge_calls], ["en-US-AriaNeural", "en-US-GuyNeural"])
            self.assertEqual(e.engines_used, {"edge": 2})
            self.assertTrue(any("groq_terms" in n for n in e.fallback_notes))
            self.assertIn("groq_terms", e.groq_reason)

    def test_rate_limit_falls_back_but_keeps_trying_groq(self):
        def groq(text, model, voice, key, dest):
            raise E.WCError(E.GROQ_RATE, "slow down", "tts")

        with tempfile.TemporaryDirectory() as d, mock.patch.object(tts, "_groq_render", groq), \
                mock.patch.object(tts, "_edge_render", lambda t, v, dest, rate="+0%": self.fake_wav(dest)):
            e = self.engine(d)
            e.render("Hello", "question")
            self.assertFalse(e.groq_dead)                 # rate limits are temporary

    def test_no_key_goes_straight_to_edge(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(tts, "_groq_render", side_effect=AssertionError("must not be called")), \
                mock.patch.object(tts, "_edge_render", lambda t, v, dest, rate="+0%": self.fake_wav(dest)):
            e = self.engine(d, key="")
            e.render("Hello", "question")
            self.assertEqual(e.engines_used, {"edge": 1})

    def test_everything_failing_is_a_clear_error(self):
        def boom(*a, **k):
            raise E.WCError(E.TTS_FAILED, "down", "tts")

        with tempfile.TemporaryDirectory() as d, mock.patch.object(tts, "_groq_render", boom), \
                mock.patch.object(tts, "_edge_render", boom):
            with self.assertRaises(E.WCError) as cm:
                self.engine(d).render("Hello", "question")
            self.assertEqual(cm.exception.code, E.TTS_FAILED)

    def test_slow_clip_is_longer_by_about_atempo(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(tts, "_groq_render", lambda t, m, v, k, dest: self.fake_wav(dest, secs=1.0)):
            e = self.engine(d)
            normal, slow = e.render("Umbrella", "answer"), e.render("Umbrella", "answer", slow=True)
            ratio = len(slow) / len(normal)
            self.assertAlmostEqual(ratio, 1 / C.SLOW_ATEMPO, delta=0.06)


class GroqRequestShapeTests(unittest.TestCase):
    def test_speech_request_payload(self):
        captured = {}

        class R:
            status_code = 200
            content = b"RIFF" + b"\0" * 100
            text = ""

        def post(url, headers=None, json=None, timeout=None):
            captured.update(url=url, headers=headers, json=json)
            return R()

        with tempfile.TemporaryDirectory() as d, mock.patch.object(tts.requests, "post", post):
            tts._groq_render("Hi there", "canopylabs/orpheus-v1-english", "troy", "KEY", Path(d) / "x.wav")
        self.assertEqual(captured["url"], "https://api.groq.com/openai/v1/audio/speech")
        self.assertEqual(captured["json"], {"model": "canopylabs/orpheus-v1-english", "input": "Hi there",
                                            "voice": "troy", "response_format": "wav"})
        self.assertEqual(captured["headers"]["Authorization"], "Bearer KEY")

    def test_http_errors_map_to_codes(self):
        def mk(status, text):
            class R:
                status_code = status
                content = b""
            R.text = text
            return R

        cases = [(404, "model_not_found", E.GROQ_MODEL), (403, "nope", E.GROQ_MODEL), (401, "bad key", E.GROQ_NO_KEY),
                 (400, "model terms required, please accept", E.GROQ_TERMS)]
        for status, text, code in cases:
            with tempfile.TemporaryDirectory() as d, mock.patch.object(tts.requests, "post", lambda *a, **k: mk(status, text)):
                with self.assertRaises(E.WCError) as cm:
                    tts._groq_render("x", "m", "v", "k", Path(d) / "x.wav")
                self.assertEqual(cm.exception.code, code, (status, text))


class HttpFlowTests(unittest.TestCase):
    """Groq text + Pixabay HTTP behaviour with stubbed `requests` (no network)."""

    class Resp:
        def __init__(self, status=200, body=None, text=""):
            self.status_code, self._b, self.text = status, body, text or json.dumps(body or {})

        def json(self):
            return self._b

    def setUp(self):
        self.sleep = mock.patch("time.sleep", lambda s: None)
        self.sleep.start()

    def tearDown(self):
        self.sleep.stop()

    def groq_ok(self, words):
        return self.Resp(200, {"choices": [{"message": {"content": json.dumps({"words": words})}}]})

    def test_groq_words_request_and_parse(self):
        seen = {}

        def post(url, headers=None, json=None, timeout=None):
            seen.update(url=url, headers=headers, body=json)
            return self.groq_ok([{"word": "ladle", "search_query": "ladle spoon"}])

        with mock.patch.object(Wd.requests, "post", post):
            out = Wd.groq_words(6, ["kettle"] * 2 + ["whisk"], ["junk"], "kitchen", "some-model", key="KEY")
        self.assertEqual(out[0]["word"], "ladle")
        self.assertEqual(seen["url"], "https://api.groq.com/openai/v1/chat/completions")
        self.assertEqual(seen["body"]["model"], "some-model")
        self.assertEqual(seen["body"]["response_format"], {"type": "json_object"})
        prompt = seen["body"]["messages"][0]["content"]
        self.assertIn("kettle", prompt)
        self.assertIn("junk", prompt)
        self.assertIn("kitchen", prompt)
        self.assertEqual(seen["headers"]["Authorization"], "Bearer KEY")

    def test_groq_words_retries_then_succeeds(self):
        answers = [self.Resp(429, text="rate"), self.Resp(500, text="boom"), self.groq_ok([{"word": "whisk"}])]
        with mock.patch.object(Wd.requests, "post", lambda *a, **k: answers.pop(0)):
            self.assertEqual(Wd.groq_words(6, [], [], None, "m", key="k")[0]["word"], "whisk")

    def test_groq_words_gives_up_after_three_with_code(self):
        calls = []

        def post(*a, **k):
            calls.append(1)
            return self.Resp(429, text="rate")

        with mock.patch.object(Wd.requests, "post", post):
            with self.assertRaises(E.WCError) as cm:
                Wd.groq_words(6, [], [], None, "m", key="k")
        self.assertEqual(len(calls), 3)
        self.assertEqual(cm.exception.code, E.GROQ_RATE)

    def test_groq_words_does_not_retry_fatal_errors(self):
        calls = []

        def post(*a, **k):
            calls.append(1)
            return self.Resp(404, text='{"error":{"code":"model_not_found"}}')

        with mock.patch.object(Wd.requests, "post", post):
            with self.assertRaises(E.WCError) as cm:
                Wd.groq_words(6, [], [], None, "m", key="k")
        self.assertEqual(len(calls), 1)
        self.assertEqual(cm.exception.code, E.GROQ_MODEL)

    def test_groq_words_without_json_mode_retries_plain(self):
        answers = [self.Resp(400, text="response_format is not supported"), self.groq_ok([{"word": "whisk"}])]
        bodies = []

        def post(url, headers=None, json=None, timeout=None):
            bodies.append(dict(json))
            return answers.pop(0)

        with mock.patch.object(Wd.requests, "post", post):
            out = Wd.groq_words(6, [], [], None, "m", key="k")
        self.assertEqual(out[0]["word"], "whisk")
        self.assertIn("response_format", bodies[0])
        self.assertNotIn("response_format", bodies[1])

    def test_groq_garbage_then_good(self):
        answers = [self.Resp(200, {"choices": [{"message": {"content": "sorry, no json"}}]}),
                   self.groq_ok([{"word": "ladle"}])]
        with mock.patch.object(Wd.requests, "post", lambda *a, **k: answers.pop(0)):
            self.assertEqual(Wd.groq_words(6, [], [], None, "m", key="k")[0]["word"], "ladle")

    def pixabay_hit(self, **over):
        return dict({"id": 1, "pageURL": "p", "type": "photo", "tags": "ladle, spoon", "webformatURL": "w",
                     "largeImageURL": "l", "imageWidth": 4000, "imageHeight": 3000, "user": "A", "user_id": 7}, **over)

    def pixabay_body(self, **over):
        return {"total": 1, "totalHits": 1, "hits": [self.pixabay_hit(**over)]}

    def test_pixabay_request_shape(self):
        seen = {}

        def get(url, params=None, headers=None, timeout=None):
            seen.update(url=url, params=params, headers=headers)
            return self.Resp(200, self.pixabay_body())

        with mock.patch.object(P.requests, "get", get):
            photos = P.Pixabay(key="PKEY").search_raw("ladle spoon", page=2)
        self.assertEqual(len(photos), 1)
        self.assertEqual(seen["url"], "https://pixabay.com/api/")
        self.assertEqual(seen["params"], {"key": "PKEY", "q": "ladle spoon", "image_type": "photo",
                                          "safesearch": "true", "page": 2, "per_page": 30})
        self.assertIn("User-Agent", seen["headers"])

    def test_pixabay_rate_limit_retry_then_ok(self):
        answers = [self.Resp(429), self.Resp(200, self.pixabay_body())]
        with mock.patch.object(P.requests, "get", lambda *a, **k: answers.pop(0)):
            self.assertEqual(len(P.Pixabay(key="k").search_raw("x")), 1)

    def test_pixabay_rate_limit_waits_for_window_reset(self):
        class Limited(self.Resp):
            headers = {"X-RateLimit-Reset": "17"}
        waits = []
        answers = [Limited(429), self.Resp(200, self.pixabay_body())]
        with mock.patch.object(P.requests, "get", lambda *a, **k: answers.pop(0)), \
                mock.patch("time.sleep", waits.append):
            P.Pixabay(key="k").search_raw("x")
        self.assertEqual(waits, [18])

    def test_pixabay_rate_limit_exhausted(self):
        with mock.patch.object(P.requests, "get", lambda *a, **k: self.Resp(429)):
            with self.assertRaises(E.WCError) as cm:
                P.Pixabay(key="k").search_raw("x")
        self.assertEqual(cm.exception.code, E.PIXABAY_RATE)

    def test_pixabay_bad_key(self):
        for resp in (self.Resp(401), self.Resp(403), self.Resp(400, text='[ERROR 400] "key" is invalid')):
            with mock.patch.object(P.requests, "get", lambda *a, r=resp, **k: r):
                with self.assertRaises(E.WCError) as cm:
                    P.Pixabay(key="k").search_raw("x")
            self.assertEqual(cm.exception.code, E.PIXABAY_NO_KEY)

    def test_pixabay_page_out_of_range_means_no_more(self):
        calls = []

        def get(*a, **k):
            calls.append(1)
            return self.Resp(400, text='[ERROR 400] "page" is out of valid range.')

        with mock.patch.object(P.requests, "get", get):
            self.assertEqual(P.Pixabay(key="k").search_raw("x", page=99), [])
        self.assertEqual(len(calls), 1)

    def test_pixabay_other_bad_request_is_not_retried(self):
        calls = []

        def get(*a, **k):
            calls.append(1)
            return self.Resp(400, text="[ERROR 400] something else")

        with mock.patch.object(P.requests, "get", get):
            with self.assertRaises(E.WCError) as cm:
                P.Pixabay(key="k").search_raw("x")
        self.assertEqual((len(calls), cm.exception.code), (1, E.PIXABAY_EMPTY))

    def test_pixabay_key_never_leaks_into_errors(self):
        # the key is part of the URL, and requests puts the URL into its exception text
        def get(*a, **k):
            raise P.requests.ConnectionError("Max retries exceeded with url: /api/?key=SECRET123&q=x")

        with mock.patch.object(P.requests, "get", get):
            with self.assertRaises(E.WCError) as cm:
                P.Pixabay(key="SECRET123").search_raw("x")
        self.assertNotIn("SECRET123", cm.exception.detail)
        self.assertNotIn("SECRET123", str(cm.exception))
        self.assertNotIn("SECRET123", json.dumps(cm.exception.to_dict()))

    def test_pixabay_candidates_top_up_with_the_bare_word(self):
        queries = []

        def get(url, params=None, headers=None, timeout=None):
            queries.append(params["q"])
            narrow = params["q"] == "wood chisel tool"
            ids = range(2) if narrow else range(100, 108)       # the long query finds only two photos
            return self.Resp(200, {"hits": [self.pixabay_hit(id=i) for i in ids]})

        with mock.patch.object(P.requests, "get", get):
            out = P.Pixabay(key="k").candidates("wood chisel", "wood chisel tool")
        self.assertEqual(queries, ["wood chisel tool", "wood chisel"])
        self.assertEqual(len(out), 4)
        self.assertEqual(len({o["id"] for o in out}), 4)

    def test_pixabay_by_id_returns_fresh_links(self):
        seen = {}

        def get(url, params=None, headers=None, timeout=None):
            seen.update(params)
            return self.Resp(200, self.pixabay_body(id=42, largeImageURL="https://fresh/l.jpg"))

        with mock.patch.object(P.requests, "get", get):
            fresh = P.Pixabay(key="k").by_id(42)
            self.assertEqual(seen["id"], 42)
            self.assertEqual((fresh["large"], fresh["original"]), ("https://fresh/l.jpg", "https://fresh/l.jpg"))
            self.assertIsNone(P.Pixabay(key="k").by_id(43))       # answer was for another id

    def test_build_refreshes_expired_pixabay_links(self):
        from engine import build
        from engine.selection import parse_selection as parse
        from PIL import Image

        buf = tempfile.NamedTemporaryFile(suffix=".jpg", delete=False)
        buf.close()
        noise = np.random.RandomState(1).randint(0, 255, (640, 640, 3), dtype=np.uint8)
        Image.fromarray(noise).save(buf.name, "JPEG", quality=90)
        jpeg = Path(buf.name).read_bytes()
        os.unlink(buf.name)

        class Img:
            def __init__(self, status, content=b""):
                self.status_code, self.content = status, content

        asked = []

        def get(url, params=None, headers=None, timeout=None):
            asked.append(url)
            if url == "https://pixabay.com/api/":
                return self.Resp(200, self.pixabay_body(id=5, largeImageURL="https://fresh/l.jpg"))
            return Img(200, jpeg) if url == "https://fresh/l.jpg" else Img(403)

        sel = parse('{"v":1,"sid":"20261021-1000-aa11","w":[{"w":"ladle","p":5}]}', 1)
        cand = {"words": [{"photos": [{"id": 5, "original": "https://old/o.jpg", "large": "https://old/l.jpg",
                                       "photographer": "A", "photographer_url": "u", "url": "p"}]}]}
        with tempfile.TemporaryDirectory() as d, mock.patch.object(P.requests, "get", get), \
                mock.patch.dict(os.environ, {"PIXABAY_API_KEY": "k"}):
            _words, _infos, credits = build.collect_images(sel, cand, None, Path(d), False)
        self.assertIn("https://fresh/l.jpg", asked)
        self.assertEqual((credits[0]["source"], credits[0]["pixabay_id"]), ("Pixabay", 5))

    def test_prepare_drops_words_with_too_few_photos_and_uses_spares(self):
        from engine import prepare
        poor = {"hits": [self.pixabay_hit(id=i) for i in range(2)]}   # only 2 usable photos
        rich_words = {"ladle", "whisk", "teapot", "kettle", "toaster", "blender"}

        def get(url, params=None, headers=None, timeout=None):
            q = params["q"].split()[0]
            if q in rich_words:
                body = {"hits": [self.pixabay_hit(id=hash(q) % 1000 * 10 + i) for i in range(8)]}
            else:
                body = poor
            return self.Resp(200, body)

        raw = [{"word": w, "search_query": w} for w in ("hammer", "ladle", "whisk", "wrench", "teapot", "kettle", "toaster", "blender")]
        with tempfile.TemporaryDirectory() as d, mock.patch.object(P.requests, "get", get), \
                mock.patch.object(prepare, "groq_words", lambda *a, **k: raw), mock.patch.dict(os.environ, {"PIXABAY_API_KEY": "k", "GROQ_API_KEY": "k"}):
            from engine.state import State
            cand = prepare.build_candidates(C.Settings(), State(d), "20261021-1000-aa11", None, False)
        words = [w["word"] for w in cand["words"]]
        self.assertEqual(len(words), 6)
        self.assertNotIn("hammer", words)       # dropped: not enough photos
        self.assertNotIn("wrench", words)
        self.assertEqual(cand["source"], "groq")


class StateTests(unittest.TestCase):
    def test_state_branch_lifecycle(self):
        from engine.state import State
        with tempfile.TemporaryDirectory() as d:
            remote = f"{d}/remote.git"
            subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
            url = f"file://{remote}"
            a = State(f"{d}/a", url).init()
            self.assertTrue(a.created)
            a.append_lines("used_words.txt", ["ladle", "whisk", "ladle"])
            a.write_json("sessions/20260101-000000-aaaa/candidates.json", {"x": 1})
            self.assertTrue(a.commit_push("test"))
            self.assertFalse(a.commit_push("nothing to commit"))
            b = State(f"{d}/b", url).init()                     # existing branch is reused
            self.assertFalse(b.created)
            self.assertEqual(b.read_lines("used_words.txt"), ["ladle", "whisk"])
            self.assertEqual(b.read_json("sessions/20260101-000000-aaaa/candidates.json"), {"x": 1})
            # a concurrent writer: rebase-retry keeps both changes
            b.append_lines("bad_words.txt", ["junk"])
            a.append_lines("used_words.txt", ["kettle"])
            a.commit_push("a2")
            b.commit_push("b2")
            c = State(f"{d}/c", url).init()
            self.assertEqual(c.read_lines("used_words.txt"), ["ladle", "whisk", "kettle"])
            self.assertEqual(c.read_lines("bad_words.txt"), ["junk"])

    def test_prune_keeps_last_ten(self):
        from engine.state import State
        with tempfile.TemporaryDirectory() as d:
            st = State(d)
            for i in range(14):
                st.write_json(f"sessions/20260101-0000{i:02d}-aaaa/x.json", {})
            removed = st.prune_sessions(10)
            self.assertEqual(len(removed), 4)
            left = sorted(p.name for p in (Path(d) / "sessions").iterdir())
            self.assertEqual(len(left), 10)
            self.assertEqual(left[0], "20260101-000004-aaaa")


class PrepareFlowTests(unittest.TestCase):
    def test_prepare_and_more_images_mock(self):
        from engine import prepare
        from engine.state import State
        with tempfile.TemporaryDirectory() as d:
            remote = f"{d}/remote.git"
            subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
            args = ["--session", "20261021-1000-aa11", "--mock", "--state-dir", f"{d}/s", "--remote", f"file://{remote}"]
            self.assertEqual(prepare.main(args), 0)
            st = State(f"{d}/v", f"file://{remote}").init()
            cand = st.read_json("sessions/20261021-1000-aa11/candidates.json")
            self.assertEqual(len(cand["words"]), 6)
            self.assertEqual(len({Wd.normalize(w["word"]) for w in cand["words"]}), 6)
            for w in cand["words"]:
                self.assertEqual(len(w["photos"]), 4)
            self.assertEqual(st.read_json("sessions/20261021-1000-aa11/status.json")["state"], "ok")
            word = cand["words"][1]["word"]
            self.assertEqual(prepare.main(args + ["--more", word]), 0)
            st2 = State(f"{d}/w", f"file://{remote}").init()
            cand2 = st2.read_json("sessions/20261021-1000-aa11/candidates.json")
            self.assertEqual(len(cand2["words"][1]["photos"]), 8)
            ids = [p["id"] for p in cand2["words"][1]["photos"]]
            self.assertEqual(len(ids), len(set(ids)))
            # used words are excluded next time
            st2.append_lines("used_words.txt", [w["word"] for w in cand["words"]])
            st2.commit_push("used")
            args2 = ["--session", "20261021-1100-bb22", "--mock", "--state-dir", f"{d}/s2", "--remote", f"file://{remote}"]
            self.assertEqual(prepare.main(args2), 0)
            st3 = State(f"{d}/x", f"file://{remote}").init()
            cand3 = st3.read_json("sessions/20261021-1100-bb22/candidates.json")
            first = {Wd.normalize(w["word"]) for w in cand["words"]}
            self.assertTrue(first.isdisjoint({Wd.normalize(w["word"]) for w in cand3["words"]}))

    def test_prepare_failure_writes_error_status(self):
        from engine import prepare
        from engine.state import State
        with tempfile.TemporaryDirectory() as d:
            remote = f"{d}/remote.git"
            subprocess.run(["git", "init", "-q", "--bare", remote], check=True)
            env = {k: os.environ.pop(k, None) for k in ("GROQ_API_KEY", "PIXABAY_API_KEY")}
            try:
                rc = prepare.main(["--session", "20261021-1000-cc33", "--state-dir", f"{d}/s",
                                   "--remote", f"file://{remote}"])      # not mock, no keys
            finally:
                for k, v in env.items():
                    if v is not None:
                        os.environ[k] = v
            self.assertEqual(rc, 1)
            st = State(f"{d}/v", f"file://{remote}").init()
            status = st.read_json("sessions/20261021-1000-cc33/status.json")
            self.assertEqual(status["state"], "error")
            self.assertEqual(status["code"], E.PIXABAY_NO_KEY)    # Groq failure falls back; Pixabay key is fatal


if __name__ == "__main__":
    unittest.main()
