"""SpeechBrain emotion-posterior worker  (runs in its OWN venv / process).

    voxera_patientfetch/.venv_voice/Scripts/python.exe -m voxera_patientfetch.voice_worker

Why a separate process: SpeechBrain needs torchaudio, and torchaudio 2.13 does not
exist for the torch 2.13 that Whisper/Kokoro run on. Isolating it means (a) no
dependency clash, (b) a crash or slow inference can never touch the audio
pipeline, (c) it can be killed/restarted freely.

Model: speechbrain/emotion-recognition-wav2vec2-IEMOCAP (pretrained, local CPU,
no training, no network calls after the first download).

Protocol (binary over stdin/stdout, one request at a time):
    request : 4-byte big-endian length N, then N bytes of float32 mono 16 kHz PCM
    reply   : one JSON line  {"ok":true,"probs":{"neu":..,"ang":..,"hap":..,"sad":..},"ms":123}
                            or {"ok":false,"error":"..."}
    startup : one JSON line  {"ready":true,"labels":[...]}  (or {"ready":false,"error":..})

Audio is processed in memory and never written to disk.
"""

from __future__ import annotations

import json
import os
import struct
import sys
import time

SOURCE = "speechbrain/emotion-recognition-wav2vec2-IEMOCAP"


def _out(obj) -> None:
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def main() -> int:
    # keep stdout clean for the protocol: send library chatter to stderr
    real_stdout = sys.stdout
    sys.stdout = sys.stderr
    try:
        import numpy as np
        import torch
        from speechbrain.inference.interfaces import foreign_class
        from speechbrain.utils.fetching import LocalStrategy
        cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".model_cache", "emotion_iemocap")
        clf = foreign_class(source=SOURCE, pymodule_file="custom_interface.py",
                            classname="CustomEncoderWav2vec2Classifier", savedir=cache,
                            local_strategy=LocalStrategy.COPY,      # Windows: symlinks need admin (WinError 1314)
                            run_opts={"device": "cpu"})
        torch.set_num_threads(max(1, int(os.getenv("VOXERA_VOICE_THREADS", "2"))))
        labels = [clf.hparams.label_encoder.ind2lab[i] for i in sorted(clf.hparams.label_encoder.ind2lab)]
    except Exception as e:                                      # noqa: BLE001
        sys.stdout = real_stdout
        _out({"ready": False, "error": f"{type(e).__name__}: {str(e)[:200]}"})
        return 1
    sys.stdout = real_stdout
    try:                                    # marker: weights fully downloaded + loaded at least once
        open(os.path.join(os.path.dirname(cache), "READY"), "w").write("ok")
    except OSError:
        pass
    _out({"ready": True, "labels": labels})

    inp = sys.stdin.buffer
    while True:
        hdr = inp.read(4)
        if len(hdr) < 4:
            return 0
        (n,) = struct.unpack(">I", hdr)
        if n == 0:
            return 0
        buf = inp.read(n)
        if len(buf) < n:
            return 0
        t0 = time.time()
        try:
            wav = np.frombuffer(buf, dtype=np.float32).copy()
            wav = np.clip(wav, -1.0, 1.0)
            with torch.no_grad():
                x = torch.from_numpy(wav).unsqueeze(0)
                out_prob, _score, _idx, _lab = clf.classify_batch(x)
            p = out_prob.squeeze(0).tolist()
            probs = {labels[i]: round(float(p[i]), 4) for i in range(len(labels))}
            _out({"ok": True, "probs": probs, "ms": int((time.time() - t0) * 1000)})
        except Exception as e:                                  # noqa: BLE001
            _out({"ok": False, "error": f"{type(e).__name__}: {str(e)[:160]}"})


if __name__ == "__main__":
    raise SystemExit(main())
