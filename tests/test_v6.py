"""v6: 3-2-1 clock, safe zone, bigger tiles, music bed, chime, deny-list, photo size floor."""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402
from PIL import Image  # noqa: E402

from engine import audio as A  # noqa: E402
from engine import config as C  # noqa: E402
from engine import draw as D  # noqa: E402
from engine import musicgen, sfx, tts  # noqa: E402
from engine import pixabay as P  # noqa: E402
from engine import render as R  # noqa: E402
from engine import timeline as T  # noqa: E402
from engine import words as Wd  # noqa: E402

WORDS = ("anvil", "shears", "tambourine", "wheelbarrow", "umbrella stand", "pomegranate")


def tl3():
    return T.build_timeline(1.1, [T.WordAudio(0.9, 0.6, 0.9, 6)] * 3, 4.8)


class CountdownTests(unittest.TestCase):
    def test_counts_three_two_one(self):
        self.assertEqual(T.countdown_numbers(), [3, 2, 1])
        self.assertEqual(T.countdown_at(0.0)[0], 3)
        self.assertEqual(T.countdown_at(0.99)[0], 3)
        self.assertEqual(T.countdown_at(1.0)[0], 2)
        self.assertEqual(T.countdown_at(2.5)[0], 1)
        self.assertEqual(T.countdown_at(2.999)[0], 1)
        self.assertAlmostEqual(T.countdown_at(1.25)[1], 0.25)

    def test_segment_has_three_seconds_of_clock(self):
        s = tl3().words[0]
        self.assertAlmostEqual(s.cd_end - s.cd_start, 3.0)

    def test_three_ticks_per_word_and_chime_replaces_ding(self):
        ev = T.audio_events(tl3())
        self.assertEqual(sum(1 for _, k, n in ev if n == "tick"), 9)
        self.assertEqual(sum(1 for _, k, n in ev if n == "chime"), 3)
        self.assertEqual(sum(1 for _, k, n in ev if n == "ding"), 0)

    def test_clock_gone_before_label_appears(self):
        self.assertGreaterEqual(C.LABEL_DELAY, C.CLOCK_OUT_SEC)
        s = tl3().words[0]
        self.assertLessEqual(s.a_start, s.reveal + C.LABEL_DELAY + 0.05)   # voice follows the label

    def test_total_length_in_gate(self):
        tl = tl3()
        self.assertTrue(C.DURATION_RANGE[0] <= tl.total <= C.DURATION_RANGE[1], tl.total)

    def test_zoom_is_six_to_eight_percent(self):
        self.assertTrue(1.06 <= C.KEN_BURNS_END <= 1.08)


class SafeZoneTests(unittest.TestCase):
    def inside(self, x0, y0, x1, y1, tol=0.5):
        self.assertGreaterEqual(x0, C.SAFE_X0 - tol, (x0, y0, x1, y1))
        self.assertLessEqual(x1, C.SAFE_X1 + tol, (x0, y0, x1, y1))
        self.assertGreaterEqual(y0, C.SAFE_Y0 - tol, (x0, y0, x1, y1))
        self.assertLessEqual(y1, C.SAFE_Y1 + tol, (x0, y0, x1, y1))

    def test_safe_zone_numbers(self):
        self.assertGreaterEqual(C.SAFE_Y0, 200)
        self.assertLessEqual(C.SAFE_Y1, C.H - 380)
        self.assertLessEqual(C.SAFE_X1, C.W - 140)

    def test_big_clock_below_photo_and_safe(self):
        ring = D.draw_ring(C.THEMES[0], 1.0, 3, 1.1)
        cx, cy = C.RING_CENTER
        self.inside(cx - ring.width / 2, cy - ring.height / 2, cx + ring.width / 2, cy + ring.height / 2)
        self.assertGreaterEqual(cy - ring.height / 2, C.CARD_Y + C.CARD_H)      # not over the photo
        self.assertGreaterEqual(2 * C.RING_RADIUS, 220)                          # genuinely big

    def test_label_and_tiles_bigger_and_inside(self):
        for theme in C.THEMES:
            for w in WORDS:
                lab = D.build_label(theme, w)
                vis_w, vis_h = lab.width / D.SS - 20, lab.height / D.SS - 20
                cx = C.CENTER_X
                m = C.LABEL_POP_MAX
                self.inside(cx - vis_w / 2 * m, C.LABEL_CY - vis_h / 2 * m,
                            cx + vis_w / 2 * m, C.LABEL_CY + vis_h / 2 * m)
                tiles, total, ts = D.build_tiles(theme, w)
                self.inside(cx - total / 2 - ts * 0.15, C.SPELL_Y[0], cx + total / 2 + ts * 0.15, C.SPELL_Y[1] + 14)
                self.assertGreaterEqual(ts, 60 if len(w.replace(" ", "")) <= 11 else 40, (w, ts))
        self.assertEqual(D.build_tiles(C.THEMES[0], "anvil")[2], C.TILE_MAX)
        self.assertGreater(C.TILE_MAX, 60)                                        # was 60 in v5

    def test_label_below_photo_and_clear_of_tiles(self):
        self.assertGreater(C.LABEL_BOX[1], C.CARD_Y + C.CARD_H)
        self.assertLess(C.LABEL_BOX[3], C.SPELL_Y[0] + 4)

    def test_outro_pieces_inside_safe_width(self):
        tl = tl3()
        d = tempfile.mkdtemp()
        f = Path(d) / "w.png"
        Image.new("RGB", (1400, 1000), (90, 140, 200)).save(f)
        words = [R.WordRes(w, str(f), None, (0.5, 0.5)) for w in ("kettle", "teapot", "ladle")]
        sc = R.Scene(tl, words, C.THEMES[1], "@Lingobits-Official")
        for spr in sc.outro_sprites + sc.outro_sub + [sc.handle_pill]:
            w = spr.width / D.SS * 1.08
            self.assertLessEqual(w / 2, C.SAFE_HALF_W, w)
        self.assertLessEqual(sc.follow_btn.width * 1.1 / 2, C.SAFE_HALF_W)
        # vertical positions used by Scene._outro (kept in sync here on purpose)
        Y = R.OUTRO_Y
        sh = sc.outro_sub[0].height / D.SS
        for y, h in ((Y["how"], 140), (Y["how"] + 120, 140), (Y["sub"] - 8, sh), (Y["sub"] + 135 + 8, sh),
                     (Y["handle"], sc.handle_pill.height / D.SS), (Y["follow"], sc.follow_btn.height * 1.1),
                     (Y["icons"], 170 * 1.1)):
            self.assertGreaterEqual(y - h / 2, C.SAFE_Y0)
            self.assertLessEqual(y + h / 2, C.SAFE_Y1)
        self.assertGreater(sh, 110)                                                # "Comment below!" is big (was 64)
        # no two outro blocks overlap vertically
        self.assertLess(Y["how"] + 120 + 70, Y["sub"] - sh / 2 + 20)
        self.assertLess(Y["sub"] + 135 + sh / 2, Y["handle"] - sc.handle_pill.height / D.SS / 2)
        self.assertLess(Y["handle"] + sc.handle_pill.height / D.SS / 2, Y["follow"] - sc.follow_btn.height / 2)

    def test_sparkles_never_cover_the_label(self):
        for w in (420, 560, 730):
            for seed in (1251, 5, 99, 4242):
                lh = 134
                for (x, y, *_r) in D.sparkle_points(C.CENTER_X, C.LABEL_CY, w, lh, seed=seed):
                    on_label = abs(x - C.CENTER_X) < w / 2 + 10 and abs(y - C.LABEL_CY) < lh / 2 + 10
                    self.assertFalse(on_label, (w, seed, x, y))
                    self.assertLessEqual(x, C.SAFE_X1)
                    self.assertGreaterEqual(x, C.SAFE_X0)
                    self.assertLess(y, C.SPELL_Y[0])


class FrameSmokeTests(unittest.TestCase):
    def test_frames_render_at_key_times(self):
        tl = tl3()
        d = tempfile.mkdtemp()
        f = Path(d) / "w.png"
        Image.new("RGB", (1400, 1000), (90, 140, 200)).save(f)
        words = [R.WordRes(w, str(f), (0.5, 0.5) if i == 0 else None, (0.5, 0.5))
                 for i, w in enumerate(("anvil", "shears", "tambourine"))]
        sc = R.Scene(tl, words, C.THEMES[2], "@Lingobits-Official")
        s = tl.words[0]
        times = [0.0, 0.4, s.start + s.cd_start + 0.5, s.start + s.cd_start + 1.5, s.start + s.cd_start + 2.5,
                 s.start + s.reveal + 0.05, s.start + s.reveal + 0.5, s.start + s.content_end,
                 tl.outro_start + 0.3, tl.outro_start + 3.0, tl.total - 0.05]
        for t in times:
            im = sc.frame(min(tl.frames() - 1, int(round(t * C.FPS))))
            self.assertEqual(im.size, (C.W, C.H))

    def test_first_frame_is_not_empty(self):
        tl = tl3()
        d = tempfile.mkdtemp()
        f = Path(d) / "w.png"
        Image.new("RGB", (1400, 1000), (90, 140, 200)).save(f)
        words = [R.WordRes(w, str(f), None, (0.5, 0.5)) for w in ("kettle", "teapot", "ladle")]
        sc = R.Scene(tl, words, C.THEMES[0], "")
        a = np.asarray(sc.frame(0).convert("L"), dtype=np.int16)
        b = np.asarray(sc.bg.convert("L"), dtype=np.int16)
        self.assertGreater(int((np.abs(a - b)[400:1200] > 40).sum()), 2000)      # text + first photo already visible


def mock_clips(tl):
    clips = {}
    eng = tts.VoiceEngine(C.Settings(), tempfile.mkdtemp(), mock=True)
    for k in ["hook", "outro"] + [f"{p}{i}" for p in "qas" for i in range(3)]:
        clips[k] = eng.render("hello there " + k, "question" if k[0] in "hq" else "answer")
    return clips


class AudioV6Tests(unittest.TestCase):
    def test_music_file_exists_and_is_a_clean_loop(self):
        files = list(C.MUSIC_DIR.glob("*.mp3"))
        self.assertTrue(files, "assets/music/*.mp3 missing - run: python -m engine.musicgen")
        from engine import media
        x = media.decode_audio(files[0])
        self.assertTrue(18 <= len(x) / C.AUDIO_RATE <= 22)
        self.assertLessEqual(float(np.max(np.abs(x))), 0.8)

    def test_generated_loop_is_seamless_and_quiet_peak(self):
        x = musicgen.generate_loop()
        self.assertLessEqual(float(np.max(np.abs(x))), 0.51)
        self.assertLess(abs(float(x[0]) - float(x[-1])), 0.01)

    def test_fade_music_edges(self):
        y = A.fade_music(np.ones(C.AUDIO_RATE * 6, dtype=np.float32))
        self.assertLess(abs(float(y[0])), 1e-3)
        self.assertLess(abs(float(y[-1])), 1e-3)
        self.assertAlmostEqual(float(y[C.AUDIO_RATE * 3]), 1.0, places=3)

    def test_soft_limit_never_exceeds_ceiling(self):
        x = np.linspace(-5, 5, 20001).astype(np.float32)
        y = A.soft_limit(x, 0.9)
        self.assertLessEqual(float(np.max(np.abs(y))), 0.9 + 1e-6)
        small = np.linspace(-0.5, 0.5, 101).astype(np.float32)
        self.assertTrue(np.allclose(A.soft_limit(small, 0.9), small, atol=1e-6))

    def test_chime_is_soft_and_in_bank(self):
        c = sfx.make_all()["chime"]
        self.assertEqual(c.dtype, np.float32)
        self.assertLessEqual(float(np.max(np.abs(c))), 0.55)
        self.assertAlmostEqual(float(c[0]), 0.0, places=3)
        # no harsh click: starts from silence with a ramp (1 ms in is still near zero)
        self.assertLess(float(np.max(np.abs(c[: int(0.001 * C.AUDIO_RATE)]))), 0.03)
        self.assertLess(float(np.max(np.abs(np.diff(c)))), 0.2)      # a plain 1.5 kHz sine at this level is ~0.1

    def test_mix_with_music_does_not_clip_and_music_is_audible_but_quieter(self):
        tl = tl3()
        clips, bank, s = mock_clips(tl), sfx.make_all(), C.Settings()
        with_m, info = A.build_mix(tl, clips, bank, s, C.MUSIC_DIR)
        without, info0 = A.build_mix(tl, clips, bank, s, None)
        self.assertTrue(info["music"])
        self.assertFalse(info0["music"])
        self.assertLessEqual(info["peak"], 0.90)
        self.assertLessEqual(float(np.max(np.abs(with_m))), 0.90)
        self.assertEqual(len(with_m), len(without))
        # a quiet stretch (no voice, no sfx): countdown seconds of the 1st word, after the ticks
        seg = tl.words[0]
        t0 = seg.start + seg.cd_start + 0.35
        sl = slice(int(t0 * C.AUDIO_RATE), int((t0 + 0.4) * C.AUDIO_RATE))
        rms = lambda a: float(np.sqrt(np.mean(a[sl] ** 2)))   # noqa: E731
        self.assertGreater(rms(with_m), rms(without) * 1.2)            # music is audible in gaps
        # under the voice the music is much quieter than the voice itself
        va = seg.start + seg.q_start
        vs = slice(int((va + 0.1) * C.AUDIO_RATE), int((va + 0.6) * C.AUDIO_RATE))
        voice_rms = float(np.sqrt(np.mean(without[vs] ** 2)))
        music_under_voice = float(np.sqrt(np.mean((with_m[vs] - without[vs]) ** 2)))
        self.assertLess(music_under_voice, voice_rms * 0.5)


class WordRulesTests(unittest.TestCase):
    def test_deny_list_blocks_near_synonyms(self):
        for w in ("gazebo", "pavilion", "bandstand", "shed", "hut", "poncho"):
            self.assertTrue(Wd.is_denied(w), w)
        items = [{"word": w} for w in ("gazebo", "Pavilion", "bandstand", "anvil")]
        out = Wd.filter_candidates(items, [], [])
        self.assertEqual([o["word"] for o in out], ["anvil"])

    def test_bank_still_large_after_deny_list(self):
        import random
        bank = Wd.filter_candidates(Wd.builtin_words(random.Random(1), None), [], [])
        self.assertGreater(len(bank), 1200)
        self.assertFalse(any(Wd.is_denied(b["word"]) for b in bank))

    def test_similar_words_not_together(self):
        self.assertTrue(Wd.too_similar("tangerine", ["kumquat", "anvil"]))
        self.assertTrue(Wd.too_similar("Cello", ["viola"]))
        self.assertFalse(Wd.too_similar("anvil", ["kumquat", "viola"]))
        self.assertFalse(Wd.too_similar("unknownword", ["anvil"]))

    def test_prepare_skips_look_alikes(self):
        from engine import prepare
        self.assertTrue(hasattr(prepare, "too_similar"))


class PhotoSizeTests(unittest.TestCase):
    def photo(self, i, w, h):
        u = f"https://cdn.example/img{i}.jpg"
        return {"id": i, "width": w, "height": h, "alt": "anvil tool", "src": {"large": u, "medium": u, "original": u},
                "photographer": "p", "photographer_url": "", "url": "u"}

    def test_small_photos_rejected(self):
        photos = [self.photo(1, 1280, 580), self.photo(2, 580, 1280), self.photo(3, 1280, 853), self.photo(4, 960, 640)]
        out = P.filter_and_rank(photos, "anvil", want=4)
        ids = {o["id"] for o in out}
        self.assertEqual(ids, {3, 4})
        self.assertEqual(P.MIN_SHORT_SIDE, 600)


class EncodeTests(unittest.TestCase):
    def test_encode_quality_settings(self):
        cmd = R.encode_cmd("a.wav", "o.mp4")
        j = " ".join(cmd)
        self.assertIn("yuv420p", j)
        self.assertIn("+faststart", j)
        self.assertLessEqual(C.CRF, 16)
        self.assertIn("-maxrate", cmd)


if __name__ == "__main__":
    unittest.main()
