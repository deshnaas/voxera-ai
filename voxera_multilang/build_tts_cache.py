"""Render every FIXED Hindi/Marathi line once and store it on disk.

    .venv312\\Scripts\\python.exe -m voxera_multilang.build_tts_cache

Why: synthesising a Hindi/Marathi sentence takes several seconds on this CPU. Fixed lines (emergency replies, triage
questions, ID prompts, greetings ...) are therefore rendered here, once, and loaded instantly during a call - no speech
synthesis competes with live speech recognition. Re-run it after editing catalog.py (changed lines are re-rendered,
unchanged ones are skipped). Takes a few minutes the first time.
"""

from __future__ import annotations

import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    sys.path.insert(0, ROOT)
    sys.path.insert(0, os.path.join(ROOT, "models", "goonj"))
    import voxera_core as vx
    from voxera_multilang import catalog as cat
    from voxera_multilang.integration import MultiLang

    vx.load_tts()
    ml = MultiLang(vx)
    jobs = []
    for lang in ("hi", "mr"):
        jobs += [(t, lang) for t in ml.fixed_phrases(lang)]
    jobs += [(t, "en") for t in ml.fixed_phrases_en()]
    jobs += [(t, l) for l, t in cat.GREETING_PARTS]
    jobs += [("नमस्ते, मैं वॉक्सेरा हूँ, मैं आपकी क्या मदद कर सकती हूँ?", "hi"),
             ("नमस्कार, मी वॉक्सेरा आहे, मी तुम्हाला कशी मदत करू?", "mr"),
             ("Hi, this is Voxera. How can I help you today?", "en")]
    seen, todo = set(), []
    for t, l in jobs:
        if (t, l) not in seen:
            seen.add((t, l))
            todo.append((t, l))
    print(f"[tts-cache] {len(todo)} fixed lines")
    t0 = time.time()
    ok = 0
    for i, (text, lang) in enumerate(todo, 1):
        if ml.tts.render_to_cache(text, lang):
            ok += 1
        if i % 10 == 0 or i == len(todo):
            print(f"[tts-cache] {i}/{len(todo)}  ({time.time() - t0:.0f}s)", flush=True)
    print(f"[tts-cache] done: {ok}/{len(todo)} ready")
    return 0 if ok == len(todo) else 1


if __name__ == "__main__":
    raise SystemExit(main())
