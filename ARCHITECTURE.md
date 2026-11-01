# WordClip Studio: architecture

Everything heavy runs in GitHub Actions. The phone only shows a static web app (GitHub Pages, `/docs`)
that talks to `api.github.com` with a fine-grained token kept in `localStorage`.

```
phone (docs/*)  --workflow_dispatch-->  Actions (engine/*)  --git push-->  branch `state`
      ^   ^                                   |  \--gh release create-->  Release (video, cover, credits)
      |   +-- GET contents?ref=state  <-------+
      +------ GET releases/tags/<tag> <-------+
```

## Workflows (`.github/workflows`)

All share `concurrency: group: wordclip, cancel-in-progress: false` because they all write to the `state` branch.
Note: GitHub keeps at most ONE pending run per group (an older pending run is cancelled by a newer one), so the UI
disables buttons while something runs.

| file | inputs | does |
|---|---|---|
| `prepare.yml` | `session_id`, `category` | 6 words (3 + 3 spares) via Groq (fallback WordNet/built-in), Pixabay search, writes `sessions/<id>/candidates.json` + `status.json` |
| `more_images.yml` | `session_id`, `word` | next Pixabay page for one word, appends 4 photos |
| `build.yml` | `session_id`, `selection` | render, quality gates, Release `video-YYYYMMDD-HHMMSS`, then `used_words.txt` |
| `check_setup.yml` | none | secrets, Groq model list (prints `orpheus` ones), Groq TTS, Pixabay, edge-tts, ffmpeg, fonts -> `reports/check_setup.json` |
| `voice_test.yml` | none | 3 female + 3 male Groq voices + 2 edge voices -> Release `voice-test-*`, `reports/voice_test.json` |

`run-name` carries the session id; the UI finds its run as "first run with id greater than the last one before dispatch whose title matches".

## `state` branch (created automatically)

```
used_words.txt  bad_words.txt            plain lists, one word per line
audio-cache/                             only static phrases (hook, question, outro)
reports/check_setup.json  voice_test.json
sessions/<id>/candidates.json status.json more_status.json build.json uploads/*.jpg
```
Only the last 10 sessions are kept. `used_words.txt` is appended only after the Release exists.
Skipped words go to `bad_words.txt` after a successful build (`x` field of the selection).

## Selection JSON (UI -> build.yml, well under 60k chars)

```json
{"v":1,"sid":"20261021-101500-ab12","theme":"auto",
 "w":[{"w":"umbrella stand","p":123456,"f":[0.41,0.62],"o":[0.5,0.5]},
      {"w":"ladle","u":"sessions/<sid>/uploads/x.jpg","f":[0.5,0.3]}],
 "x":["skipped word"]}
```
`p` Pixabay id or `u` upload path (exactly one). `f` focus in image fractions. `o` crop offset (0 = left/top aligned, 1 = right/bottom).

## Engine modules

| module | role |
|---|---|
| `config.py` | all layout/timing constants, 6 themes (contrast-checked), settings (`config.yaml` + env Variables) |
| `geometry.py` | cover crop, Ken Burns box (zoom about the focus), arrow placement (always inside the safe zone), upscale check. Mirrored in `docs/core.js` (parity-tested) |
| `timeline.py` | timeline from REAL clip durations, snapped to whole frames; audio event list |
| `words.py`, `pixabay.py` | word generation/dedupe/fallbacks; Pixabay search, photo filtering (stepwise: how much the downloadable image must be enlarged to cover the card), scoring on the tags, fresh links by id |
| `tts.py` | Groq Orpheus (OpenAI-compatible endpoint) -> edge-tts fallback, cache by sha1(engine, model, voice, text), silence trim, normalise, 30 ms fades |
| `sfx.py`, `audio.py` | numpy-synthesised SFX; mix, ducked optional music, two-pass loudnorm (-14 LUFS, TP -1.5 target) |
| `draw.py`, `render.py` | Pillow sprites at 2x supersampling; frame renderer (float-box Lanczos zoom), multiprocessing -> raw RGB -> ffmpeg libx264 |
| `quality.py` | ffprobe/loudness/duration/duplicate/asset gates; build fails loudly |
| `state.py` | git-based state branch helper (works with any remote; tests use a local bare repo) |
| `prepare.py`, `build.py`, `check_setup.py`, `voice_test.py` | workflow entry points (`python -m engine.<name>`; `--mock` where it applies) |

Errors carry stable codes (`engine/errors.py`) that the UI maps to Bengali messages (`docs/core.js`, parity-tested).

## Deliberate deviations / notes

- The spec's 900 px card at x=90 ends at x=990, 30 px past the x=960 safe line. Kept as specified; every piece of text, the ring, label, spelling tiles and the arrow (incl. pulse and pop overshoot) stay inside the safe zone (unit-tested).
- Pills ("1/3") sit on the first line of the title band (y 170-204) so they are inside the safe zone.
- The slow pronunciation re-uses the cached answer render slowed with `atempo=0.8` (pitch preserved) instead of a second API call.
- The countdown is two beats (2, 1), at least 2.0 s, as specified.
- Pixabay photos use `imageURL`/`fullHDURL` when the key has full API access, otherwise `largeImageURL`, which Pixabay scales to at most 1280 px. So a 3:2 landscape photo is enlarged about 1.2x to cover the 900x1000 card; build.py flags that in the release notes and `quality.py` only fails above 2.0x. Sizes in `candidates.json` (`w`, `h`) are what can really be downloaded. The API key travels in the URL, so `pixabay.py` scrubs it from every error text, and image links expire after about 24 h (build.py asks for fresh ones by id when a download fails).
- Pixabay answers with `hits` (tags instead of alt text, `user`/`user_id` instead of photographer). `pixabay._normalize` turns a hit into the internal photo dict, so `prepare.py`, `build.py` and the web UI see the same `candidates.json` shape as before.
- Prepared work copies are reduced so the card is ~1.6x smaller than the source to keep per-frame Lanczos cheap, but never below 1.0x at max zoom.
- Music loudness (-26 LUFS) is measured with ffmpeg ebur128 on the looped track.

## Tests

```
python -m unittest discover -s tests -v     # 83 tests: timeline, safe zones, dedupe, selection, geometry, state,
                                            # prepare/build flows incl. failures, voice + HTTP paths with stubbed network
node --test tests/test_core.js              # 12 tests: GitHub client with mocked fetch, JS<->Python parity
python tests/ui_test.py                     # 46 checks: headless Chromium UI flow against a FAKE GitHub backend (needs playwright)
python -m engine.build --session <sid> --selection @sel.json --mock --no-state --no-release --out out   # full mock render
```
Not testable offline: real Groq / Pixabay / GitHub Actions / `gh release` / edge-tts / Pages hosting.

## Latest changes
- The arrow is optional: a selection word without `f` renders without the arrow.
- "Skip" has no limit: when the spare words run out the UI dispatches `prepare.yml` with `refill=true`, which appends new words to the session (`engine.prepare --refill`).
- Photos are unique per word (by Pixabay id and by image link) in `pixabay.py` and in the UI grid.
- Word list: curated uncommon words in `engine/words.py` (`BUILTIN_WORDS`) and the Groq prompt asks for uncommon, concrete words.
- Word history: `used_words.txt` lives on the `state` branch; Prepare reads it before choosing words, Build appends and pushes it.
