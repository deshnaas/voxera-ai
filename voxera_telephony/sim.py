"""A stand-in for Exotel: dial Voxera's phone backend without a phone, a SIM or any credits.

    .venv312\\Scripts\\python.exe -m voxera_telephony.sim --from +919876543210 \\
        --say "en:I have a mild fever and a headache." --say "en:No, that's all. Thank you." --say "en:VX 421"

It speaks the same WebSocket protocol Exotel does (connected / start / media / stop, 8 kHz 16-bit PCM in 100 ms
chunks, continuous audio including silence), plays the caller in real time, listens to Voxera in real time and
reports what a caller would experience: how long Voxera took to start answering after each sentence, whether it
was interrupted, how long the whole call felt. Voxera's replies are saved as .wav files so you can listen.

--say "LANG:text"   speak this (LANG = en | hi | mr), synthesised with Priya at phone quality
--wav file.wav      use a recording instead
--hangup-after N    hang up (send `stop`) N turns in, like a caller dropping the line
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import os
import sys
import time
import wave
from collections import deque

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from voxera_telephony.audio import float_to_pcm16, pcm16_to_float, resample  # noqa: E402

SR = 8000
CHUNK = 1600                     # 100 ms of 8 kHz 16-bit PCM (what the uplink sends every 100 ms)


def _token_from_env_file() -> str:
    """The server reads VOXERA_STREAM_TOKEN from .env; so does the simulator (never printed)."""
    try:
        import re
        m = re.search(r"^VOXERA_STREAM_TOKEN=(.+)$", open(os.path.join(ROOT, ".env"), encoding="utf-8").read(), re.M)
        return m.group(1).strip() if m else ""
    except OSError:
        return ""


def synth_caller(turns: list) -> list:
    """[('en', 'text'), ...] -> [float32 @ 8 kHz]. Uses Priya (any language) so no recordings are needed."""
    import voxera_core as vx
    from voxera_multilang.tts import MultiTTS
    vx.load_tts()
    tts = MultiTTS(vx)
    out = []
    for lang, text in turns:
        a = tts.synth(text, lang, "priya")
        out.append(resample(a, 24000, SR))
    return out


def read_wav(path: str) -> np.ndarray:
    with wave.open(path, "rb") as w:
        sr, ch = w.getframerate(), w.getnchannels()
        x = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2").astype(np.float32) / 32768.0
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    return resample(x, sr, SR)


def write_wav(path: str, pcm: bytes) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SR)
        w.writeframes(pcm)


class Line:
    """The caller's end of the line: a steady 100 ms uplink (silence when idle) + a downlink that 'plays' in real time."""

    def __init__(self, ws):
        self.ws = ws
        self.up: deque = deque()
        self.down_bytes = 0
        self.first_media_at = None
        self.play_end = 0.0               # when the audio received so far finishes playing
        self.last_media_at = 0.0
        self.reply = bytearray()
        self.events = {"media": 0, "mark": 0, "clear": 0}
        self.since_mark: list = []        # (time, bytes) of media since the last mark
        self.answers: list = []           # (answer_start_time, audio_bytes) for each completed non-filler utterance
        self.fillers = 0
        self.closed = False
        self.stream_sid = "MZsim0000000000000000000000000001"
        self.noise = np.random.default_rng(7)

    def queue_speech(self, a: np.ndarray) -> float:
        pcm = float_to_pcm16(a)
        pcm += b"\x00" * (-len(pcm) % CHUNK)
        for i in range(0, len(pcm), CHUNK):
            self.up.append(pcm[i:i + CHUNK])
        return len(pcm) / 2 / SR

    async def uplink(self, seq0: int = 3):
        seq, chunk = seq0, 0
        t0 = time.monotonic()
        while not self.closed:
            if self.up:
                data = self.up.popleft()
            else:                                                     # a real line streams quiet noise, not nothing
                data = float_to_pcm16(self.noise.normal(0, 0.0015, CHUNK // 2).astype(np.float32))
            chunk += 1
            seq += 1
            try:
                await self.ws.send(json.dumps({"event": "media", "sequence_number": seq, "stream_sid": self.stream_sid,
                                               "media": {"chunk": chunk, "timestamp": str(int((time.monotonic() - t0) * 1000)),
                                                         "payload": base64.b64encode(data).decode()}}))
            except Exception:                                         # noqa: BLE001
                return
            await asyncio.sleep(0.1)

    async def downlink(self):
        try:
            async for raw in self.ws:
                ev = json.loads(raw)
                k = ev.get("event")
                now = time.monotonic()
                if k == "media":
                    b = base64.b64decode(ev["media"]["payload"])
                    if len(b) % 320:
                        print(f"  !! protocol: media chunk of {len(b)} bytes is not a multiple of 320")
                    self.events["media"] += 1
                    self.since_mark.append((now, len(b)))
                    self.reply += b
                    if self.first_media_at is None:
                        self.first_media_at = now
                    self.last_media_at = now
                    self.play_end = max(self.play_end, now) + len(b) / 2 / SR
                elif k == "clear":
                    self.events["clear"] += 1
                elif k == "mark":
                    self.events["mark"] += 1
                    if (ev.get("mark") or {}).get("name") == "filler":
                        self.fillers += 1
                    elif self.since_mark:
                        self.answers.append((self.since_mark[0][0], sum(n for _, n in self.since_mark)))
                    self.since_mark = []
        finally:
            self.closed = True


async def run(args) -> int:
    import websockets
    turns = []
    for s in args.say or []:
        lang, _, text = s.partition(":")
        turns.append((lang.strip(), text.strip()))
    audios = synth_caller(turns) if turns else []
    for w in args.wav or []:
        audios.append(read_wav(w))
    if not audios:
        print("nothing to say: use --say or --wav")
        return 2
    labels = [f"{l}: {t}" for l, t in turns] + [f"wav {w}" for w in (args.wav or [])]

    url = args.url + (("&" if "?" in args.url else "?") + f"token={args.token}" if args.token else "")
    os.makedirs(args.out, exist_ok=True)
    print(f"\ndialling {args.url}  as {args.from_number}\n")
    async with websockets.connect(url, max_size=None) as ws:
        line = Line(ws)
        await ws.send(json.dumps({"event": "connected"}))
        await ws.send(json.dumps({"event": "start", "sequence_number": 1, "stream_sid": line.stream_sid,
                                  "start": {"stream_sid": line.stream_sid, "call_sid": "CAsim0001", "account_sid": "sim",
                                            "from": args.from_number, "to": "+911234567890", "custom_parameters": {},
                                            "media_format": {"encoding": "raw", "sample_rate": str(SR), "bit_rate": "128"}}}))
        up = asyncio.create_task(line.uplink())
        down = asyncio.create_task(line.downlink())
        t_call = time.monotonic()
        rows = []

        async def wait_reply(after: float, timeout: float):
            """Wait for Voxera's next COMPLETE answer (a non-filler mark), then for it to finish playing.
            Returns (time the first sound arrived, time the real answer began)."""
            n0 = len(line.answers)
            first = None
            end = time.monotonic() + timeout
            while not line.closed and time.monotonic() < end:
                if first is None and line.last_media_at > after:
                    first = line.last_media_at
                if len(line.answers) > n0:
                    break
                await asyncio.sleep(0.05)
            answer_start = line.answers[-1][0] if len(line.answers) > n0 else None
            while not line.closed and time.monotonic() < line.play_end + 0.25:
                await asyncio.sleep(0.05)
            return first, answer_start

        # Voxera greets first; let it finish
        first, started = await wait_reply(t_call, args.timeout)
        if first is None:
            print("no greeting received (is the server ready? see /health)")
        else:
            print(f"  greeting: {len(line.reply) / 2 / SR:.1f}s of audio, began {first - t_call:.1f}s after answer")
            write_wav(os.path.join(args.out, "reply_0_greeting.wav"), bytes(line.reply))
        for i, a in enumerate(audios, 1):
            if line.closed:
                break
            if args.hangup_after and i > args.hangup_after:
                break
            line.reply = bytearray()
            f0 = line.fillers
            spoke_s = line.queue_speech(a)
            t_speech_end = time.monotonic() + spoke_s
            await asyncio.sleep(spoke_s)
            first, started = await wait_reply(t_speech_end - 0.05, args.timeout)
            sound = (first - t_speech_end) if first else None          # first thing the caller hears (may be "one moment")
            lat = (started - t_speech_end) if started else None         # when the actual answer starts
            filler = line.fillers - f0
            dur = line.answers[-1][1] / 2 / SR if started else 0.0
            wav = os.path.join(args.out, f"reply_{i}.wav")
            write_wav(wav, bytes(line.reply))
            rows.append((i, labels[i - 1], lat, dur))
            heard = ("first sound %.1fs%s, " % (sound, " (\"one moment\")" if filler else "")) if sound is not None else ""
            print(f"  turn {i}: you said [{labels[i - 1][:44]}]")
            print(f"          {heard}answer began {('%.1fs' % lat) if lat is not None else 'NEVER'} "
                  f"after you finished, spoke {dur:.1f}s")
        if args.hangup_after and not line.closed:
            await ws.send(json.dumps({"event": "stop", "sequence_number": 99, "stream_sid": line.stream_sid,
                                      "stop": {"call_sid": "CAsim0001", "account_sid": "sim", "reason": "stopped"}}))
            print("  (caller hung up)")
        # after the last sentence Voxera may wrap up and close the line itself
        end = time.monotonic() + 20
        while not line.closed and time.monotonic() < end:
            await asyncio.sleep(0.2)
        line.closed = True
        up.cancel()
        down.cancel()

    total = time.monotonic() - t_call
    lats = [r[2] for r in rows if r[2] is not None]
    print(f"\ncall lasted {total:.0f}s;  events from server: {line.events}")
    if lats:
        print(f"response time (end of your sentence -> Voxera starts speaking):  "
              f"avg {sum(lats) / len(lats):.1f}s  max {max(lats):.1f}s   (a natural call feels good under ~2s)")
    print(f"replies saved in {args.out}\n")
    return 0 if all(r[2] is not None for r in rows) else 1


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--url", default="ws://127.0.0.1:8200/exotel/stream")
    ap.add_argument("--token", default=os.getenv("VOXERA_STREAM_TOKEN", "") or _token_from_env_file())
    ap.add_argument("--from", dest="from_number", default="+919990001111")
    ap.add_argument("--say", action="append", help='"en:text" | "hi:text" | "mr:text" (repeat for each turn)')
    ap.add_argument("--wav", action="append")
    ap.add_argument("--hangup-after", type=int, default=0)
    ap.add_argument("--timeout", type=float, default=60.0)
    ap.add_argument("--out", default=os.path.join(ROOT, "voxera_telephony", ".sim_out"))
    return asyncio.run(run(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
