"""build.yml entry point.

  python -m engine.build --session SID --selection '<json>' [--mock] [--no-state] [--no-release]

Renders the video, runs the quality gates, publishes a GitHub Release and only
then records the words in used_words.txt.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from pathlib import Path

import requests
from PIL import Image

from . import audio as A
from . import config as C
from . import errors as E
from . import media, quality, sfx
from . import render as R
from . import tts
from .mock import mock_photo
from .selection import SID_RE, Selection, parse_selection
from .state import State
from .timeline import WordAudio, build_timeline

HOOK_TEXT = "Can you name these?"
QUESTION_TEXT = "What\u2019s this called?"
OUTRO_TEXT = "How many did you know? Comment below!"
STATIC_TEXTS = {HOOK_TEXT, QUESTION_TEXT, OUTRO_TEXT, "What's this called?"}


def log(msg: str) -> None:
    print(f"[build] {msg}", flush=True)


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ------------------------------------------------------------- images
def _download(urls: list, dest: Path) -> str:
    last = ""
    for url in urls:
        if not url:
            continue
        for attempt in range(3):
            try:
                r = requests.get(url, timeout=120)
                if r.status_code == 200 and len(r.content) > 5000:
                    dest.write_bytes(r.content)
                    return url
                last = f"HTTP {r.status_code}"
            except requests.RequestException as ex:
                last = str(ex)
            time.sleep(1 + attempt)
    raise E.WCError(E.IMAGE_MISSING, f"download failed: {last}", "download")


def collect_images(sel: Selection, cand: dict | None, state: State | None, workdir: Path,
                   mock: bool) -> tuple:
    """Return (list[R.WordRes], image_infos, credits list)."""
    by_id = {}
    if cand:
        for w in cand.get("words", []):
            for p in w.get("photos", []):
                by_id[p["id"]] = p
    words, infos, credits = [], [], []
    for i, pick in enumerate(sel.picks):
        raw = workdir / f"raw{i}.img"
        meta = None
        if pick.upload:
            if state is None:
                raise E.WCError(E.IMAGE_MISSING, "uploads need the state branch", "download")
            src = state.p(pick.upload)
            if not src.exists():
                raise E.WCError(E.IMAGE_MISSING, f"uploaded image missing: {pick.upload}", "download")
            shutil.copyfile(src, raw)
            credits.append({"word": pick.word, "credit": "User-supplied image",
                            "license": "provided by the channel owner"})
        else:
            meta = by_id.get(pick.photo_id)
            if mock:
                mock_photo(pick.photo_id or 1, pick.word).save(raw, "JPEG", quality=92)
            else:
                if meta is None:
                    raise E.WCError(E.SELECTION_BAD,
                                    f"photo {pick.photo_id} not in candidates.json", "download")
                _download([meta.get("original"), meta.get("large")], raw)
            credits.append({
                "word": pick.word,
                "pexels_id": pick.photo_id,
                "photographer": (meta or {}).get("photographer", "Mock Photographer"),
                "photographer_url": (meta or {}).get("photographer_url", ""),
                "photo_url": (meta or {}).get("url", ""),
                "source": "Pexels",
                "license": "https://www.pexels.com/license/",
            })
        try:
            info = R.prep_image(raw, workdir / f"work{i}.png")
        except Exception as ex:  # noqa: BLE001
            raise E.WCError(E.IMAGE_MISSING, f"cannot read image for {pick.word}: {ex}", "download")
        infos.append(info)
        credits[-1]["resolution"] = f"{info['width']}x{info['height']}"
        credits[-1]["upscaled_flag"] = bool(info["upscaled"] or info["upscaled_at_zoom"])
        words.append(R.WordRes(pick.word, str(workdir / f"work{i}.png"), pick.focus, pick.offset))
    return words, infos, credits


# -------------------------------------------------------------- voices
def make_clips(engine: tts.VoiceEngine, words: list) -> tuple:
    clips = {"hook": engine.render(HOOK_TEXT, "question"),
             "outro": engine.render(OUTRO_TEXT, "question")}
    audios = []
    q = engine.render(QUESTION_TEXT, "question")
    for i, w in enumerate(words):
        clips[f"q{i}"] = q
        clips[f"a{i}"] = engine.render(w.word, "answer")
        clips[f"s{i}"] = engine.render(w.word, "answer", slow=True)
        sr = C.AUDIO_RATE
        audios.append(WordAudio(len(q) / sr, len(clips[f"a{i}"]) / sr, len(clips[f"s{i}"]) / sr,
                                sum(1 for c in w.word if c != " ")))
    return clips, audios


# ------------------------------------------------------------- outputs
def key_times(tl) -> list:
    t = [("hook", 1.0)]
    for seg in tl.words:
        s = seg.start
        t += [(f"w{seg.index + 1} question", s + seg.q_start + 0.7),
              (f"w{seg.index + 1} countdown", s + seg.cd_start + 0.45),
              (f"w{seg.index + 1} reveal", s + seg.reveal + 0.55),
              (f"w{seg.index + 1} spelled", s + seg.content_end - 0.15)]
    t.append(("outro", tl.outro_start + 1.4))
    return t


def extract_frame(video: Path, t: float, dest: Path) -> None:
    p = media.run(["ffmpeg", "-y", "-v", "error", "-ss", f"{t:.3f}", "-i", str(video),
                   "-frames:v", "1", "-q:v", "2", str(dest)])
    if p.returncode != 0 or not dest.exists():
        raise RuntimeError(f"frame extract failed at {t}: {p.stderr.decode(errors='ignore')[:200]}")


def make_contact_sheet(video: Path, tl, out: Path, workdir: Path) -> None:
    kts = key_times(tl)
    thumbs = []
    for k, (label, t) in enumerate(kts):
        fp = workdir / f"ks{k}.jpg"
        extract_frame(video, min(t, tl.total - 0.1), fp)
        thumbs.append(Image.open(fp).convert("RGB").resize((270, 480), Image.LANCZOS))
    cols = 7
    rows = (len(thumbs) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * 270, rows * 480), (20, 20, 24))
    for i, im in enumerate(thumbs):
        sheet.paste(im, ((i % cols) * 270, (i // cols) * 480))
    sheet.save(out, "JPEG", quality=88)


def release_notes(tag: str, sel: Selection, theme: dict, credits: list, engine: tts.VoiceEngine,
                  checks: list, mock: bool) -> str:
    lines = [f"# WordClip video {tag}", "",
             "Words: " + ", ".join(p.word for p in sel.picks), "",
             f"Theme: {theme['name']}  |  Voices: {engine.engines_summary()}"]
    if engine.fallback_notes:
        lines += ["", "Voice notes:"] + [f"- {n}" for n in engine.fallback_notes]
    lines += ["", "## Photo credits", "Photos provided by [Pexels](https://www.pexels.com)."]
    for c in credits:
        if c.get("photographer"):
            lines.append(f"- {c['word']}: [{c['photographer']}]({c.get('photographer_url', '')}) on Pexels"
                         + (f" ([photo]({c['photo_url']}))" if c.get("photo_url") else ""))
        else:
            lines.append(f"- {c['word']}: {c.get('credit', '')}")
    flagged = [c["word"] for c in credits if c.get("upscaled_flag")]
    if flagged:
        lines += ["", "Flagged (source smaller than the card, upscaled): " + ", ".join(flagged)]
    lines += ["", "## Quality checks"]
    lines += [f"- {'PASS' if r['ok'] else 'FAIL'}: {r['name']} ({r['detail']})" for r in checks]
    if mock:
        lines += ["", "MOCK BUILD: synthetic images and tones, not for publishing."]
    return "\n".join(lines) + "\n"


def publish_release(tag: str, files: list, notes: Path, title: str) -> str:
    cmd = ["gh", "release", "create", tag] + [str(f) for f in files] + [
        "--title", title, "--notes-file", str(notes)]
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise E.WCError(E.RELEASE_FAILED, (p.stderr or p.stdout)[:400], "release")
    return p.stdout.strip().splitlines()[-1] if p.stdout.strip() else ""


# ---------------------------------------------------------------- main
def run_build(sel: Selection, settings, state: State | None, mock: bool, out_dir: Path,
              release: bool, workers: int | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="wordclip-"))
    t0 = time.time()
    try:
        cand = state.read_json(f"sessions/{sel.sid}/candidates.json") if state else None
        log("1/6 collecting images")
        words, infos, credits = collect_images(sel, cand, state, work, mock)

        log("2/6 voices")
        cache = work / "audio-cache"
        if state and state.p("audio-cache").exists():
            shutil.copytree(state.p("audio-cache"), cache, dirs_exist_ok=True)
        engine = tts.VoiceEngine(settings, cache, mock=mock)
        clips, audios = make_clips(engine, words)
        log(f"voice engines: {engine.engines_summary()}")

        theme = C.pick_theme(sel.sid, None if sel.theme == "auto" else sel.theme)
        if settings.theme not in ("auto", "") and sel.theme == "auto":
            theme = C.pick_theme(sel.sid, settings.theme)
        tl = build_timeline(len(clips["hook"]) / C.AUDIO_RATE, audios,
                            len(clips["outro"]) / C.AUDIO_RATE)
        log(f"theme={theme['name']} duration={tl.total:.2f}s frames={tl.frames()}")

        log("3/6 audio mix")
        music_dir = C.MUSIC_DIR if any(C.MUSIC_DIR.glob("*.mp3")) else None
        stereo, ainfo = A.build_mix(tl, clips, sfx.make_all(), settings, music_dir)
        wav = work / "mix.wav"
        media.write_wav(wav, stereo)

        log("4/6 rendering frames (this is the slow part)")
        video = out_dir / "video.mp4"
        R.render_video(tl, words, theme, settings.channel_handle, str(wav), str(video),
                       workers=workers,
                       progress=lambda i, n: log(f"   frame {i}/{n}"))

        log("5/6 quality gates + extras")
        cover = out_dir / "cover.jpg"
        extract_frame(video, tl.words[0].start + tl.words[0].reveal + 0.55, cover)
        sheet = out_dir / "contact-sheet.jpg"
        make_contact_sheet(video, tl, sheet, work)
        credits_path = out_dir / "credits.json"
        credits_path.write_text(json.dumps(
            {"generated": _now(), "photos_source": "Pexels (https://www.pexels.com)",
             "words": [p.word for p in sel.picks], "credits": credits,
             "voice_engines": engine.engines_used}, ensure_ascii=False, indent=1), encoding="utf-8")
        checks = quality.check_video(video, len(sel.picks), [p.word for p in sel.picks], infos, tl.total)
        checks += quality.check_assets({"video.mp4": video, "cover.jpg": cover,
                                        "contact-sheet.jpg": sheet, "credits.json": credits_path})
        checks.append(quality.check_frame_not_blank(cover))
        (out_dir / "quality-report.json").write_text(json.dumps(checks, indent=1), encoding="utf-8")
        for r in checks:
            log(f"   {'PASS' if r['ok'] else 'FAIL'} {r['name']}: {r['detail']}")
        quality.assert_all(checks)

        tag = "video-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
        notes = out_dir / "notes.md"
        notes.write_text(release_notes(tag, sel, theme, credits, engine, checks, mock), encoding="utf-8")

        result = {"state": "done", "tag": tag, "words": [p.word for p in sel.picks],
                  "theme": theme["name"], "voices": engine.engines_used,
                  "seconds": round(time.time() - t0), "at": _now(),
                  "flagged": [c["word"] for c in credits if c.get("upscaled_flag")]}
        if release:
            log("6/6 publishing release " + tag)
            url = publish_release(tag, [video, cover, sheet, credits_path], notes,
                                  "WordClip: " + ", ".join(result["words"]))
            result["url"] = url
        # only now, after success, remember the words
        if state:
            state.append_lines("used_words.txt", result["words"])
            if sel.skipped:
                state.append_lines("bad_words.txt", sel.skipped)
            ac = state.p("audio-cache")
            ac.mkdir(exist_ok=True)
            for text, path in engine.used_files:
                if text in STATIC_TEXTS and Path(path).exists():
                    shutil.copyfile(path, ac / Path(path).name)
            state.write_json(f"sessions/{sel.sid}/build.json", result)
            state.commit_push(f"build {sel.sid} {tag}")
        return result
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--session", required=True)
    ap.add_argument("--selection", required=True, help="compact JSON string or @file")
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--no-state", action="store_true")
    ap.add_argument("--no-release", action="store_true")
    ap.add_argument("--out", default="out")
    ap.add_argument("--workers", type=int, default=0)
    ap.add_argument("--state-dir", default=os.environ.get("STATE_DIR", "state"))
    ap.add_argument("--remote", default=os.environ.get("STATE_REMOTE", ""))
    a = ap.parse_args(argv)
    settings = C.load_settings()
    text = Path(a.selection[1:]).read_text(encoding="utf-8") if a.selection.startswith("@") else a.selection
    state = None
    try:
        if not a.no_state:           # first, so that even a bad selection can be reported to the UI
            state = State(a.state_dir, a.remote or None).init()
        sel = parse_selection(text, settings.words_per_video)
        if sel.sid != a.session:
            raise E.WCError(E.SELECTION_BAD, "session id mismatch", "selection")
        res = run_build(sel, settings, state, a.mock, Path(a.out), release=not a.no_release,
                        workers=a.workers or None)
        print(json.dumps(res, indent=1))
        return 0
    except E.WCError as ex:
        print(f"[build] FAILED {ex}", file=sys.stderr)
        _record_failure(state, a.session, ex.to_dict())
        return 1
    except Exception as ex:  # noqa: BLE001
        traceback.print_exc()
        _record_failure(state, a.session, {"state": "error", "code": E.RENDER_FAILED,
                                           "detail": str(ex)[:400], "stage": "build"})
        return 1


def _record_failure(state: State | None, sid: str, err: dict) -> None:
    if state is None or not SID_RE.match(sid or ""):   # never build a path from unchecked input
        return
    try:
        state.write_json(f"sessions/{sid}/build.json", {**err, "at": _now()})
        state.commit_push(f"build failed {sid}")
    except Exception:   # noqa: BLE001
        pass


if __name__ == "__main__":
    sys.exit(main())
