"""Headless-browser test of the web app against a FAKE GitHub backend.

  python tests/ui_test.py [screenshot_dir]

Nothing here talks to the real GitHub, Groq or Pexels.
"""
import base64
import http.server
import io
import json
import socketserver
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from playwright.sync_api import sync_playwright  # noqa: E402

from engine import config as C  # noqa: E402
from engine import prepare as prep  # noqa: E402
from engine.mock import mock_photo  # noqa: E402
from engine.selection import parse_selection  # noqa: E402
from engine.state import State  # noqa: E402

SHOTS = Path(sys.argv[1] if len(sys.argv) > 1 else "/tmp/ui-shots")
SHOTS.mkdir(parents=True, exist_ok=True)
REPO = "alice/wordclip"
failures, checks = [], []


def check(name, cond, detail=""):
    checks.append((name, bool(cond)))
    print(("PASS " if cond else "FAIL ") + name + (f"  [{detail}]" if detail and not cond else ""))
    if not cond:
        failures.append(name)


# ---------------------------------------------------------------- fake GitHub
class FakeGitHub:
    def __init__(self):
        self.files = {}            # state-branch path -> text or bytes
        self.runs = []             # {id, workflow, display_title, status, conclusion}
        self.dispatches = []       # (workflow, inputs)
        self.puts = []
        self.mode = "ok"           # or 'prepare_fails'
        self.next_id = 100
        self.tmp = tempfile.mkdtemp()
        self.video = Path("/tmp/o1/video.mp4")

    def photo_url(self, pid, kind="medium"):
        return f"https://images.pexels.com/photos/{pid}/pexels-{pid}-{kind}.jpg"

    def make_run(self, wf, title):
        self.next_id += 1
        r = {"id": self.next_id, "display_title": title, "status": "completed", "conclusion": "success",
             "html_url": f"https://github.com/{REPO}/actions/runs/{self.next_id}", "workflow": wf}
        self.runs.append(r)
        return r

    def on_dispatch(self, wf, inputs):
        self.dispatches.append((wf, inputs))
        sid = inputs.get("session_id", "")
        if wf == "prepare.yml":
            run = self.make_run(wf, f"Prepare {sid}")
            if self.mode == "prepare_fails":
                run["conclusion"] = "failure"
                self.files[f"sessions/{sid}/status.json"] = json.dumps({"state": "error", "code": "pexels_rate_limit", "detail": "429"})
                return
            st = State(self.tmp)
            cand = prep.build_candidates(C.Settings(), st, sid, inputs.get("category") or None, True)
            for w in cand["words"]:
                for p in w["photos"]:
                    p["thumb"] = self.photo_url(p["id"], "medium")
                    p["large"] = self.photo_url(p["id"], "large")
            self.files[f"sessions/{sid}/candidates.json"] = json.dumps(cand)
            self.files[f"sessions/{sid}/status.json"] = json.dumps({"state": "ok"})
        elif wf == "more_images.yml":
            self.make_run(wf, f"More images {sid} {inputs['word']}")
            cand = json.loads(self.files[f"sessions/{sid}/candidates.json"])
            for w in cand["words"]:
                if w["word"] == inputs["word"]:
                    base = max(p["id"] for p in w["photos"])
                    for i in range(4):
                        pid = base + 1 + i
                        w["photos"].append({**w["photos"][0], "id": pid, "thumb": self.photo_url(pid, "medium"),
                                            "large": self.photo_url(pid, "large"), "photographer": f"Extra {i}"})
            self.files[f"sessions/{sid}/candidates.json"] = json.dumps(cand)
            self.files[f"sessions/{sid}/more_status.json"] = json.dumps({"state": "ok"})
        elif wf == "build.yml":
            self.make_run(wf, f"Build {sid}")
            words = [w["w"] for w in json.loads(inputs["selection"])["w"]]
            self.files[f"sessions/{sid}/build.json"] = json.dumps(
                {"state": "done", "tag": "video-20261021-101010", "words": words, "voices": {"mock-tone": 11}, "flagged": []})
        elif wf == "check_setup.yml":
            self.make_run(wf, "Check setup")
            self.files["reports/check_setup.json"] = json.dumps({"overall": "yellow", "checks": [
                {"id": "a", "label": "Secret GROQ_API_KEY", "status": "ok", "detail": "set", "fix": ""},
                {"id": "b", "label": "Groq TTS test", "status": "warn", "detail": "groq_terms: accept the terms", "fix": "accept the Orpheus terms"}]})
        elif wf == "voice_test.yml":
            self.make_run(wf, "Voice test")
            self.files["reports/voice_test.json"] = json.dumps({"tag": "voice-test-1", "current": {"question": "autumn", "answer": "troy"}, "samples": [
                {"engine": "groq", "voice": "autumn", "gender": "female", "ok": True, "url": f"https://github.com/{REPO}/releases/download/voice-test-1/groq-autumn.mp3"},
                {"engine": "groq", "voice": "troy", "gender": "male", "ok": False, "error": "groq_terms"}]})

    # -- request router
    def handle(self, route, request):
        cors = {"access-control-allow-origin": "*", "access-control-allow-headers": "authorization,content-type,accept",
                "access-control-allow-methods": "GET,POST,PUT,OPTIONS"}
        if request.method == "OPTIONS":
            return route.fulfill(status=204, headers=cors)
        from urllib.parse import urlparse
        u = urlparse(request.url)
        p = u.path
        if request.headers.get("authorization") != "Bearer github_pat_TEST":
            return route.fulfill(status=401, headers=cors, content_type="application/json", body='{"message":"Bad credentials"}')

        def ok(body, status=200):
            return route.fulfill(status=status, headers=cors, content_type="application/json", body=json.dumps(body))

        pre = f"/repos/{REPO}"
        if p == pre:
            return ok({"default_branch": "main"})
        if p == f"{pre}/actions/workflows":
            return ok({"workflows": []})
        if p.startswith(f"{pre}/actions/workflows/") and p.endswith("/runs"):
            wf = p.split("/")[-2]
            return ok({"workflow_runs": [r for r in self.runs if r["workflow"] == wf][-10:]})
        if p.startswith(f"{pre}/actions/workflows/") and p.endswith("/dispatches"):
            wf = p.split("/")[-2]
            self.on_dispatch(wf, json.loads(request.post_data)["inputs"])
            return route.fulfill(status=204, headers=cors)
        if p.startswith(f"{pre}/actions/runs/"):
            rid = int(p.split("/")[-1])
            return ok(next(r for r in self.runs if r["id"] == rid))
        if p.startswith(f"{pre}/releases/tags/"):
            tag = p.split("/")[-1]
            names = ["video.mp4", "cover.jpg", "contact-sheet.jpg", "credits.json"]
            return ok({"html_url": f"https://github.com/{REPO}/releases/tag/{tag}",
                       "assets": [{"name": n, "browser_download_url": f"https://github.com/{REPO}/releases/download/{tag}/{n}"} for n in names]})
        if p.startswith(f"{pre}/contents/"):
            path = p[len(f"{pre}/contents/"):]
            if request.method == "PUT":
                body = json.loads(request.post_data)
                self.puts.append((path, body))
                self.files[path] = base64.b64decode(body["content"])
                return ok({"content": {"path": path}}, 201)
            if path == "sessions":
                names = sorted({k.split("/")[1] for k in self.files if k.startswith("sessions/")})
                return ok([{"name": n, "type": "dir"} for n in names])
            if path == "":
                return ok([{"name": "README.md", "type": "file"}])
            if path in self.files:
                data = self.files[path]
                data = data.encode() if isinstance(data, str) else data
                return ok({"content": base64.b64encode(data).decode(), "encoding": "base64"})
            return ok({"message": "Not Found"}, 404)
        return ok({"message": "unhandled " + p}, 404)


def jpeg_bytes(seed, w=800, h=600):
    im = mock_photo(seed, "x", size=(w, h))
    b = io.BytesIO()
    im.save(b, "JPEG", quality=80)
    return b.getvalue()


def serve_docs():
    class Q(http.server.SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(ROOT / "docs"), **k)

        def log_message(self, *a):
            pass

    socketserver.TCPServer.allow_reuse_address = True
    srv = socketserver.TCPServer(("127.0.0.1", 0), Q)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def main():
    srv, port = serve_docs()
    fake = FakeGitHub()
    base = f"http://127.0.0.1:{port}/index.html"
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        ctx = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, is_mobile=True,
                                  has_touch=True, locale="bn-BD", service_workers="block")
        console_errors = []
        page = ctx.new_page()
        page.on("console", lambda m: console_errors.append(m.text) if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append("PAGEERROR " + str(e)))
        page.route("https://api.github.com/**", fake.handle)
        page.route("https://images.pexels.com/**", lambda r, q: r.fulfill(status=200, content_type="image/jpeg",
                   body=jpeg_bytes(int(q.url.split("/photos/")[1].split("/")[0]), *((350 * 4 // 3, 350) if "medium" in q.url else (940, 650)))))
        vid = fake.video.read_bytes() if fake.video.exists() else b""
        page.route("https://github.com/**", lambda r, q: r.fulfill(status=200, content_type="video/mp4" if q.url.endswith("mp4") else "image/jpeg",
                   body=vid if q.url.endswith("mp4") else jpeg_bytes(1)))

        def shot(name, full=False):
            page.screenshot(path=str(SHOTS / f"{name}.png"), full_page=full)

        def inner(loc):
            """content box (inside borders) of an element, same as the app uses"""
            return loc.evaluate("e => {const r=e.getBoundingClientRect(); return {x:r.left+e.clientLeft,y:r.top+e.clientTop,width:e.clientWidth,height:e.clientHeight}}")

        def no_overflow(label):
            w = page.evaluate("[document.documentElement.scrollWidth, window.innerWidth]")
            check(f"no horizontal overflow: {label}", w[0] <= w[1], str(w))

        # 1. first run, nothing configured
        page.goto(base + "#home")
        page.wait_for_selector("text=প্রথমে Settings ঠিক করুন")
        check("home warns when token missing", page.locator("button[data-act=prepare]").is_disabled())
        shot("01-home-empty")
        no_overflow("home")

        # 2. settings
        page.click("a[data-nav=settings]")
        page.fill("#tok", "github_pat_TEST")
        page.fill("#repo", REPO)
        page.click("button[data-act=save-settings]")
        page.wait_for_selector("text=সংরক্ষণ হয়েছে")
        page.click("button[data-act=test-token]")
        page.wait_for_selector(".check .dot.ok >> nth=2")
        check("token test shows 3 green checks", page.locator(".check .dot.ok").count() == 3)
        page.select_option("#theme", "teal")
        page.click("button[data-act=check-setup]")
        page.wait_for_selector("text=Groq TTS test")
        check("setup report is rendered with a warning chip", page.locator(".chip.warn").count() >= 1)
        page.click("button[data-act=voice-test]")
        page.wait_for_selector("audio")
        check("voice test shows player + failed sample", page.locator("audio").count() == 1 and page.locator("text=groq_terms").count() >= 1)
        shot("02-settings", full=True)
        no_overflow("settings")

        # 3. wrong token -> Bengali message
        page.fill("#tok", "wrong")
        page.click("button[data-act=save-settings]")
        page.click("button[data-act=test-token]")
        page.wait_for_selector(".token-test .fix")
        check("wrong token gives a Bengali hint", "Token ভুল" in page.locator(".token-test .fix, .token-test strong").all_inner_texts()[0] + page.locator(".token-test .fix").first.inner_text())
        page.fill("#tok", "github_pat_TEST")
        page.click("button[data-act=save-settings]")

        # 4. prepare fails with Pexels limit -> message
        fake.mode = "prepare_fails"
        page.click("a[data-nav=home]")
        page.click("button[data-act=prepare]")
        page.wait_for_selector("text=Pexels সীমা শেষ")
        check("prepare failure shows plain-Bengali reason + action", page.locator("text=এক ঘণ্টা পরে").count() == 1)
        shot("03-prepare-error")
        fake.mode = "ok"

        # 5. prepare ok
        page.select_option("#cat", "kitchen")
        page.click("button[data-act=prepare]")
        page.wait_for_selector(".word", timeout=20000)
        check("3 word blocks shown", page.locator(".word").count() == 3)
        check("each word has 4 photos", all(page.locator(".word").nth(i).locator(".grid .thumb").count() == 4 for i in range(3)))
        check("category was dispatched", fake.dispatches[-1][1]["category"] == "kitchen")
        check("build button disabled until ready", page.locator("button[data-act=build]").is_disabled())
        shot("04-session", full=False)
        no_overflow("session")
        sid = page.url.split("#s/")[1]

        # preview frames have the exact card aspect (900:1000)
        box = page.locator(".word").first.locator(".thumb").first.bounding_box()
        check("candidate preview uses the card aspect 0.9", abs(box["width"] / box["height"] - 0.9) < 0.02, str(box))

        # 6. word 1: pick photo (tap), then tap the object
        words = [page.locator(".word").nth(i).locator("h3").inner_text().lower() for i in range(3)]
        w1 = page.locator(".word").first
        w1.locator(".grid .thumb").nth(1).scroll_into_view_if_needed()
        b = w1.locator(".grid .thumb").nth(1).bounding_box()
        page.touchscreen.tap(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        page.wait_for_selector(".phone .p-card")
        check("editor opens after first tap", page.locator(".phone").count() == 1)
        check("no focus yet -> no crosshair", page.locator(".cross").first.evaluate("e => getComputedStyle(e).display") == "none")
        card = page.locator(".p-card").first
        card.scroll_into_view_if_needed()
        page.wait_for_timeout(900)          # let the app's smooth scroll finish before measuring
        cb = inner(card)
        # exact card geometry inside the mini frame: 8.333% / 17.19% / 83.3% / 52.1% of the 9:16 frame
        pb = inner(page.locator(".phone").first)
        check("mini frame is 9:16", abs(pb["width"] / pb["height"] - 1080 / 1920) < 0.01)
        check("card sits at x=90/1080, y=330/1920 of the frame",
              abs((cb["x"] - 2 - pb["x"]) / pb["width"] - 90 / 1080) < 0.006 and abs((cb["y"] - 2 - pb["y"]) / pb["height"] - 330 / 1920) < 0.006,
              f"cb={cb} pb={pb}")
        check("card is 900x1000 of the frame", abs((cb["width"] + 4) / pb["width"] - 900 / 1080) < 0.006 and abs((cb["height"] + 4) / pb["height"] - 1000 / 1920) < 0.006)   # +4: the 2 px card border
        page.touchscreen.tap(cb["x"] + cb["width"] * 0.25, cb["y"] + cb["height"] * 0.75)
        page.wait_for_selector("text=ফোকাস ঠিক আছে")
        cross = page.locator(".cross").first.bounding_box()
        check("crosshair drawn where tapped", abs(cross["x"] + cross["width"] / 2 - (cb["x"] + cb["width"] * 0.25)) < 1.5
              and abs(cross["y"] + cross["height"] / 2 - (cb["y"] + cb["height"] * 0.75)) < 1.5,
              f"cross centre=({cross['x'] + 22:.1f},{cross['y'] + 22:.1f}) wanted=({cb['x'] + cb['width'] * 0.25:.1f},{cb['y'] + cb['height'] * 0.75:.1f})")
        shot("05-focus-set")

        # drag nudges the crop (4:3 photo -> horizontal slack) and the crosshair follows the image
        pos0 = page.locator(".p-card img").first.evaluate("e => e.style.objectPosition")
        x0 = cb["x"] + cb["width"] * 0.5
        y0 = cb["y"] + cb["height"] * 0.5
        page.mouse.move(x0, y0)
        page.mouse.down()
        page.mouse.move(x0 + 40, y0 + 4, steps=4)
        page.mouse.move(x0 + 70, y0 + 6, steps=4)
        page.mouse.up()
        pos1 = page.locator(".p-card img").first.evaluate("e => e.style.objectPosition")
        check("dragging changes the crop offset", pos0 != pos1, f"{pos0} -> {pos1}")
        check("drag did not move the focus into a new tap", page.locator("text=ফোকাস ঠিক আছে").count() == 1)

        # 7. word 2: pick, then tap near top-left
        w2 = page.locator(".word").nth(1)
        w2.locator(".grid .thumb").first.scroll_into_view_if_needed()
        b = w2.locator(".grid .thumb").first.bounding_box()
        page.touchscreen.tap(b["x"] + b["width"] / 2, b["y"] + b["height"] / 2)
        page.wait_for_selector(".word >> nth=1 >> .p-card")
        check("opening word 2 collapses word 1 to a summary", page.locator(".word").first.locator(".phone").count() == 0)
        card = page.locator(".word").nth(1).locator(".p-card")
        card.scroll_into_view_if_needed()
        page.wait_for_timeout(900)
        cb2 = inner(card)
        page.touchscreen.tap(cb2["x"] + cb2["width"] * 0.1, cb2["y"] + cb2["height"] * 0.1)
        page.wait_for_selector(".word >> nth=1 >> text=ফোকাস ঠিক আছে")

        # 8. word 3: skip it (replaced by a spare), more photos, upload own image
        w3_name = page.locator(".word").nth(2).locator("h3").inner_text().lower()
        page.locator(".word").nth(2).locator("button[data-act=skip]").scroll_into_view_if_needed()
        page.locator(".word").nth(2).locator("button[data-act=skip]").click()
        page.wait_for_function("n => document.querySelectorAll('.word h3')[2].textContent.toLowerCase() !== n", arg=w3_name)
        w3_new = page.locator(".word").nth(2).locator("h3").inner_text().lower()
        check("skip replaces the word with a spare", w3_new != w3_name and w3_new not in words, f"{w3_name}->{w3_new}")
        page.locator(".word").nth(2).locator("button[data-act=more]").scroll_into_view_if_needed()
        page.locator(".word").nth(2).locator("button[data-act=more]").click()
        page.wait_for_function("document.querySelectorAll('.word')[2].querySelectorAll('.grid .thumb').length === 8", timeout=20000)
        check("more photos adds 4 candidates", page.locator(".word").nth(2).locator(".grid .thumb").count() == 8)
        img = Path(fake.tmp) / "own.png"
        from PIL import Image
        Image.new("RGB", (3600, 2400), (200, 120, 40)).save(img)
        page.locator(".word").nth(2).locator("input[type=file]").set_input_files(str(img))
        page.wait_for_selector(".word >> nth=2 >> .badge.mine", timeout=20000)
        up = [p for p in fake.puts if "/uploads/" in p[0]]
        check("upload committed to the state branch", len(up) == 1 and up[0][1]["branch"] == "state" and up[0][0].startswith(f"sessions/{sid}/uploads/"))
        raw = base64.b64decode(up[0][1]["content"])
        im = Image.open(io.BytesIO(raw))
        check("upload resized to <= 3000 px and <= 10 MB", max(im.size) <= 3000 and len(raw) <= 10 * 1024 * 1024, str(im.size))
        check("uploaded image is auto-selected for editing", page.locator(".word").nth(2).locator(".p-card").count() == 1)
        card = page.locator(".word").nth(2).locator(".p-card")
        card.scroll_into_view_if_needed()
        page.wait_for_timeout(900)
        cb3 = inner(card)
        page.touchscreen.tap(cb3["x"] + cb3["width"] * 0.6, cb3["y"] + cb3["height"] * 0.4)
        page.wait_for_selector(".word >> nth=2 >> text=ফোকাস ঠিক আছে")
        shot("06-all-ready")

        # 9. build
        build_btn = page.locator("button[data-act=build]")
        check("build enabled when all three are ready", build_btn.is_enabled())
        build_btn.click()
        page.wait_for_selector("text=ভিডিও তৈরি হয়েছে", timeout=30000)
        wf, inputs = fake.dispatches[-1]
        check("build dispatched with session id", wf == "build.yml" and inputs["session_id"] == sid)
        check("selection string well under 60k", len(inputs["selection"]) < 1500, str(len(inputs["selection"])))
        parsed = parse_selection(inputs["selection"], 3)         # the real engine parser accepts the UI output
        check("selection is accepted by the Python parser and keeps word order",
              [p.word for p in parsed.picks][:2] == words[:2] and len(parsed.picks) == 3,
              str([p.word for p in parsed.picks]))
        check("theme from settings is sent", parsed.theme == "teal")
        check("skipped word is reported for bad_words", parsed.skipped == [w3_name], str(parsed.skipped))
        check("upload path is sent for word 3", bool(parsed.picks[2].upload) and parsed.picks[2].upload.startswith(f"sessions/{sid}/uploads/"))
        from engine import geometry as G
        cand = json.loads(fake.files[f"sessions/{sid}/candidates.json"])
        by_word = {w["word"]: w for w in cand["words"]}

        def expect_focus(photo, off, fx, fy):
            x0, y0, x1, y1 = G.base_box(photo["w"], photo["h"], off)
            return ((x0 + fx * (x1 - x0)) / photo["w"], (y0 + fy * (y1 - y0)) / photo["h"])

        e1 = expect_focus(by_word[words[0]]["photos"][1], (0.5, 0.5), 0.25, 0.75)     # tapped at 25%/75% of the card
        check("word 1 focus = tapped point converted to image coordinates",
              abs(parsed.picks[0].focus[0] - e1[0]) < 0.03 and abs(parsed.picks[0].focus[1] - e1[1]) < 0.03,
              f"got {parsed.picks[0].focus} want {e1}")
        e2 = expect_focus(by_word[words[1]]["photos"][0], (0.5, 0.5), 0.1, 0.1)       # tapped near the top-left
        check("word 2 focus = tapped point converted to image coordinates",
              abs(parsed.picks[1].focus[0] - e2[0]) < 0.03 and abs(parsed.picks[1].focus[1] - e2[1]) < 0.03,
              f"got {parsed.picks[1].focus} want {e2}")
        e3 = expect_focus({"w": 3000, "h": 2000}, (0.5, 0.5), 0.6, 0.4)               # uploaded 3600x2400 -> 3000x2000
        check("word 3 (upload) focus converted using the resized image",
              abs(parsed.picks[2].focus[0] - e3[0]) < 0.03 and abs(parsed.picks[2].focus[1] - e3[1]) < 0.03,
              f"got {parsed.picks[2].focus} want {e3}")
        check("word 1 has a non-default crop offset from dragging", parsed.picks[0].offset != (0.5, 0.5), str(parsed.picks[0].offset))
        check("untouched words keep the default offset", parsed.picks[1].offset == (0.5, 0.5) and parsed.picks[2].offset == (0.5, 0.5))
        dl = page.locator("a:has-text('ভিডিও ডাউনলোড করুন')")
        check("download link points at the release asset", dl.get_attribute("href").endswith("/video-20261021-101010/video.mp4"))
        check("video preview present", page.locator("video").count() == 1)
        page.evaluate("window.scrollTo(0,0)")
        shot("07-result")

        # 10. refresh keeps the session, picks and result
        page.reload()
        page.wait_for_selector("text=ভিডিও তৈরি হয়েছে", timeout=20000)
        page.wait_for_selector(".word")
        check("refresh keeps all three focus points", page.locator(".chip:has-text('ফোকাস ✓')").count() + page.locator(".focus-note:has-text('ফোকাস ঠিক আছে')").count() == 3)
        page.click("a[data-nav=home]")
        page.wait_for_selector(".list li")
        check("session list shows done chip", page.locator(".chip.ok:has-text('ভিডিও তৈরি')").count() == 1)

        # 11. dark mode
        dark = browser.new_context(viewport={"width": 390, "height": 844}, device_scale_factor=2, color_scheme="dark",
                                   is_mobile=True, has_touch=True, service_workers="block")
        dp = dark.new_page()
        dp.route("https://api.github.com/**", fake.handle)
        dp.route("https://images.pexels.com/**", lambda r, q: r.fulfill(status=200, content_type="image/jpeg", body=jpeg_bytes(3)))
        dp.goto(base + "#settings")
        dp.evaluate("localStorage.setItem('wc.token', JSON.stringify('github_pat_TEST')); localStorage.setItem('wc.repo', JSON.stringify('%s'))" % REPO)
        dp.goto(base + f"#s/{sid}")
        dp.reload()
        dp.wait_for_selector(".word", timeout=20000)
        dp.screenshot(path=str(SHOTS / "08-dark-session.png"))
        bg = dp.evaluate("getComputedStyle(document.body).backgroundColor")
        check("dark mode is applied", bg != "rgb(242, 241, 237)", bg)
        dark.close()

        check("no console errors", not [e for e in console_errors if "Failed to load resource" not in e], "; ".join(console_errors)[:300])
        browser.close()
    srv.shutdown()
    print(f"\n{sum(1 for _, ok in checks if ok)}/{len(checks)} UI checks passed; screenshots in {SHOTS}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
