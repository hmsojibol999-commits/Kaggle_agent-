"""check_setup.yml: verifies secrets, Groq models, Pixabay, ffmpeg, fonts, state branch.

Writes reports/check_setup.json on the state branch (the UI shows it) and exits
non-zero when anything is red so the Actions run is red too.
"""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
import subprocess
import sys

import requests

from . import config as C
from . import errors as E
from . import fonts, media
from .state import State
from .words import GROQ_CHAT


def item(cid, label, status, detail="", fix=""):
    return {"id": cid, "label": label, "status": status, "detail": detail[:400], "fix": fix}


def check_secrets():
    out = []
    for name, cid in (("GROQ_API_KEY", "secret_groq"), ("PIXABAY_API_KEY", "secret_pixabay")):
        v = os.environ.get(name, "").strip()
        out.append(item(cid, f"Secret {name}", "ok" if v else "fail",
                        "set" if v else "empty or missing",
                        "" if v else f"Settings > Secrets and variables > Actions > New secret: {name}"))
    return out


def check_groq_models(settings):
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return [item("groq_models", "Groq model list", "fail", "no key", "add GROQ_API_KEY")]
    try:
        r = requests.get("https://api.groq.com/openai/v1/models",
                         headers={"Authorization": f"Bearer {key}"}, timeout=30)
    except requests.RequestException as ex:
        return [item("groq_models", "Groq model list", "fail", str(ex), "check internet / retry")]
    if r.status_code == 401:
        return [item("groq_models", "Groq model list", "fail", "key rejected (401)", "create a new Groq key")]
    if r.status_code != 200:
        return [item("groq_models", "Groq model list", "fail", f"HTTP {r.status_code}", "retry later")]
    ids = sorted(m["id"] for m in r.json().get("data", []))
    orph = [i for i in ids if "orpheus" in i.lower()]
    print("[check] Groq models containing 'orpheus':", orph or "NONE")
    print("[check] all Groq models:", ", ".join(ids))
    out = [item("groq_models", "Groq model list", "ok", f"{len(ids)} models; orpheus: {', '.join(orph) or 'none'}")]
    out.append(item("groq_text_model", f"Text model {settings.text_model}",
                    "ok" if settings.text_model in ids else "warn",
                    "found" if settings.text_model in ids else "not in the list: built-in word list will be used",
                    "" if settings.text_model in ids else "set Variable GROQ_TEXT_MODEL to a model from the list"))
    out.append(item("groq_tts_model", f"TTS model {settings.tts_model}",
                    "ok" if settings.tts_model in ids else "warn",
                    "found" if settings.tts_model in ids else "not listed: edge-tts fallback will be used",
                    "" if settings.tts_model in ids else "accept the model terms in the Groq console or set GROQ_TTS_MODEL"))
    return out


def check_groq_tts(settings):
    key = os.environ.get("GROQ_API_KEY", "").strip()
    if not key:
        return [item("groq_tts", "Groq TTS test", "warn", "no key", "")]
    from . import tts
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        tts._groq_render("Hello", settings.tts_model, settings.voice_question, key, d / "t.wav")
        return [item("groq_tts", "Groq TTS test", "ok", f"voice {settings.voice_question} works")]
    except E.WCError as ex:
        return [item("groq_tts", "Groq TTS test", "warn",
                     f"{ex.code}: {ex.detail[:200]} (edge-tts will be used instead)",
                     "accept the Orpheus terms in console.groq.com or ignore: fallback is automatic")]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_pixabay():
    from .pixabay import Pixabay
    try:
        photos = Pixabay().search_raw("umbrella", 1, 5)
        return [item("pixabay", "Pixabay search", "ok" if photos else "fail", f"{len(photos)} photos returned",
                     "" if photos else "no result: retry")]
    except E.WCError as ex:
        return [item("pixabay", "Pixabay search", "fail", f"{ex.code}: {ex.detail}",
                     "check PIXABAY_API_KEY" if ex.code == E.PIXABAY_NO_KEY else "wait a minute and retry (rate limit)")]


def check_edge():
    try:
        import edge_tts  # noqa: F401
    except ImportError:
        return [item("edge_tts", "edge-tts library", "warn", "not installed", "pip install edge-tts")]
    from . import tts
    import tempfile, pathlib
    d = pathlib.Path(tempfile.mkdtemp())
    try:
        tts._edge_render("Hello", "en-US-AriaNeural", d / "t.mp3")
        return [item("edge_tts", "edge-tts fallback voice", "ok", "synthesis works")]
    except Exception as ex:  # noqa: BLE001
        return [item("edge_tts", "edge-tts fallback voice", "warn", str(ex)[:200],
                     "edge-tts is unofficial and can be blocked; retry later")]
    finally:
        shutil.rmtree(d, ignore_errors=True)


def check_ffmpeg():
    out = []
    ff = shutil.which("ffmpeg")
    if not ff:
        return [item("ffmpeg", "ffmpeg", "fail", "not installed", "sudo apt-get install ffmpeg (workflow does this)")]
    v = subprocess.run(["ffmpeg", "-version"], capture_output=True, text=True).stdout.splitlines()[0]
    enc = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], capture_output=True, text=True).stdout
    ok = "libx264" in enc and " aac " in enc
    out.append(item("ffmpeg", "ffmpeg + libx264 + aac", "ok" if ok else "fail", v[:120],
                    "" if ok else "ffmpeg lacks libx264/aac"))
    out.append(item("ffprobe", "ffprobe", "ok" if shutil.which("ffprobe") else "fail", ""))
    return out


def check_fonts():
    b, m = fonts.font_path("bold"), fonts.font_path("medium")
    names = {os.path.basename(x or "") for x in (b, m)}
    ok = bool(b and m) and not any(n.startswith("DejaVu") for n in names)
    return [item("fonts", "Fonts", "ok" if ok else "warn", f"bold={os.path.basename(b or '-')} medium={os.path.basename(m or '-')}",
                 "" if ok else "assets/fonts has no Poppins/Fredoka: DejaVu fallback is used (works, less pretty)")]


def run_checks(settings, mock=False):
    res = check_secrets()
    if not mock:
        res += check_groq_models(settings) + check_groq_tts(settings) + check_pixabay() + check_edge()
    res += check_ffmpeg() + check_fonts()
    return res


def overall(items):
    if any(i["status"] == "fail" for i in items):
        return "red"
    if any(i["status"] == "warn" for i in items):
        return "yellow"
    return "green"


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    ap.add_argument("--no-state", action="store_true")
    ap.add_argument("--state-dir", default=os.environ.get("STATE_DIR", "state"))
    ap.add_argument("--remote", default=os.environ.get("STATE_REMOTE", ""))
    a = ap.parse_args(argv)
    settings = C.load_settings()
    items = run_checks(settings, a.mock)
    report = {"generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "overall": overall(items), "checks": items,
              "run": os.environ.get("GITHUB_RUN_ID", "")}
    for i in items:
        print(f"[{i['status'].upper():4}] {i['label']}: {i['detail']}")
    print("OVERALL:", report["overall"].upper())
    if not a.no_state:
        try:
            st = State(a.state_dir, a.remote or None).init()
            st.write_json("reports/check_setup.json", report)
            st.commit_push("check_setup report")
        except E.WCError as ex:
            print("could not write state branch:", ex, file=sys.stderr)
            report["overall"] = "red"
    summ = os.environ.get("GITHUB_STEP_SUMMARY")
    if summ:
        with open(summ, "a", encoding="utf-8") as fh:
            fh.write(f"## Setup check: {report['overall'].upper()}\n")
            for i in items:
                fh.write(f"- **{i['status']}** {i['label']}: {i['detail']}\n")
    return 1 if report["overall"] == "red" else 0


if __name__ == "__main__":
    sys.exit(main())
