"""voice_test.yml: short samples of 3 female + 3 male Groq voices (and the edge-tts pair).

Uploads them to a release (tag voice-test-YYYYMMDD-HHMMSS) and writes
reports/voice_test.json so the UI can show play/download buttons.
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
from pathlib import Path

from . import config as C
from . import errors as E
from . import media, tts
from .state import State

SAMPLE_Q = "What\u2019s this called?"
SAMPLE_A = "Umbrella stand."


def to_mp3(wav: Path, dest: Path) -> Path:
    p = media.run(["ffmpeg", "-y", "-v", "error", "-i", str(wav), "-c:a", "libmp3lame",
                   "-q:a", "3", str(dest)])
    if p.returncode == 0 and dest.exists():
        return dest
    return wav


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--no-state", action="store_true")
    ap.add_argument("--no-release", action="store_true")
    ap.add_argument("--out", default="voice-out")
    ap.add_argument("--state-dir", default=os.environ.get("STATE_DIR", "state"))
    ap.add_argument("--remote", default=os.environ.get("STATE_REMOTE", ""))
    a = ap.parse_args(argv)
    s = C.load_settings()
    key = os.environ.get("GROQ_API_KEY", "")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tag = "voice-test-" + dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d-%H%M%S")
    repo = os.environ.get("GITHUB_REPOSITORY", "owner/repo")
    base = f"https://github.com/{repo}/releases/download/{tag}"
    jobs = [("groq", v, "female") for v in tts.FEMALE_VOICES] + \
           [("groq", v, "male") for v in tts.MALE_VOICES] + \
           [("edge", s.edge_voice_question, "female"), ("edge", s.edge_voice_answer, "male")]
    samples, files = [], []
    for engine, voice, gender in jobs:
        text = f"{SAMPLE_Q} {SAMPLE_A}"
        name = f"{engine}-{voice}"
        rec = {"engine": engine, "voice": voice, "gender": gender, "ok": False}
        tmpd = Path(tempfile.mkdtemp())
        try:
            if a.mock:
                x = tts.mock_tone(text, "question" if gender == "female" else "answer")
                raw = tmpd / "raw.wav"
                media.write_wav(raw, x)
            elif engine == "groq":
                raw = tmpd / "raw.wav"
                tts._groq_render(text, s.tts_model, voice, key, raw)
            else:
                raw = tmpd / "raw.mp3"
                tts._edge_render(text, voice, raw)
            wav = tmpd / "dec.wav"
            media.write_wav(wav, tts.clean_clip(media.decode_audio(raw)))
            final = to_mp3(wav, out / f"{name}.mp3")
            if final == wav:
                final = out / f"{name}.wav"
                shutil.copyfile(wav, final)
            rec.update(ok=True, file=final.name, url=f"{base}/{final.name}")
            files.append(final)
            print(f"[voice_test] OK   {name}")
        except E.WCError as ex:
            rec["error"] = f"{ex.code}: {ex.detail[:160]}"
            print(f"[voice_test] FAIL {name}: {rec['error']}")
        except Exception as ex:  # noqa: BLE001
            rec["error"] = str(ex)[:160]
            print(f"[voice_test] FAIL {name}: {rec['error']}")
        finally:
            shutil.rmtree(tmpd, ignore_errors=True)
        samples.append(rec)
    report = {"tag": tag, "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "model": s.tts_model, "samples": samples,
              "current": {"question": s.voice_question, "answer": s.voice_answer}}
    (out / "voice_test.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
    if not files:
        print("no voice worked", file=sys.stderr)
    elif not a.no_release:
        notes = out / "notes.md"
        notes.write_text("Voice samples. Pick names for the Variables GROQ_TTS_VOICE_QUESTION "
                         "(female) and GROQ_TTS_VOICE_ANSWER (male).\n\n" +
                         "\n".join(f"- {r['engine']}/{r['voice']} ({r['gender']}): "
                                   f"{'ok' if r['ok'] else r.get('error', 'failed')}" for r in samples),
                         encoding="utf-8")
        p = subprocess.run(["gh", "release", "create", tag] + [str(f) for f in files] +
                           ["--title", "Voice test", "--notes-file", str(notes)],
                           capture_output=True, text=True)
        if p.returncode != 0:
            print("release failed:", p.stderr[:300], file=sys.stderr)
            return 1
    if not a.no_state:
        st = State(a.state_dir, a.remote or None).init()
        st.write_json("reports/voice_test.json", report)
        st.commit_push("voice test report")
    return 0 if files else 1


if __name__ == "__main__":
    sys.exit(main())
