"""Prints geometry numbers computed by the Python engine (compared with core.js in test_core.js)."""
import json, sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine import geometry as G
cases = []
for iw, ih in [(4000, 3000), (3000, 4000), (1600, 1200), (2000, 2000), (890, 1000)]:
    for off in [(0.5, 0.5), (0.0, 1.0), (0.3, 0.8)]:
        for fx, fy in [(0.5, 0.5), (0.1, 0.9), (0.77, 0.2)]:
            box = G.base_box(iw, ih, off)
            cases.append({"iw": iw, "ih": ih, "off": off, "box": box, "cover": G.cover_scale(iw, ih),
                          "up": G.needs_upscale(iw, ih), "up_kb": G.needs_upscale(iw, ih, 1.12)})
print(json.dumps(cases))
