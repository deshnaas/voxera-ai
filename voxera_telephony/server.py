"""Exotel-compatible voice backend for Voxera.

    .venv312\\Scripts\\python.exe -m voxera_telephony.server            # listens on :8200

Exotel Voicebot/Stream applet  --wss-->  /exotel/stream   (8 kHz 16-bit PCM in and out, JSON events)
Everything else - language detection, safety, triage, record Q&A, care guidance, patient-ID filing - is the same
Voxera call loop the laptop demo uses (voxera.Call.serve); only the audio channel differs.

One call at a time (the models are shared; a second caller gets a polite "busy" and the line is closed).
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import hmac
import json
import os
import re
import sys
import threading
import time
from contextlib import asynccontextmanager
from typing import Optional

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect  # noqa: E402

from .channel import PhoneChannel  # noqa: E402


def _log(msg: str) -> None:
    print(f"[PHONE] {msg}", flush=True)


# ------------------------------------------------------------------------------ auth
def check_auth(headers, query: dict) -> bool:
    """Exotel can reach us as wss://KEY:TOKEN@host/path (sent as HTTP Basic auth) or with ?token=... in the URL.
    If VOXERA_STREAM_TOKEN is unset the endpoint is open (local testing only)."""
    want = os.getenv("VOXERA_STREAM_TOKEN", "").strip()
    if not want:
        return True
    got = (query.get("token") or "").strip()
    auth = headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            user_pass = base64.b64decode(auth[6:]).decode("utf-8", "replace")
            got = got or user_pass.split(":", 1)[-1]
        except (binascii.Error, ValueError):
            pass
    return bool(got) and hmac.compare_digest(got.encode(), want.encode())


def caller_digits(number: str) -> str:
    d = re.sub(r"\D", "", number or "")
    return d[-10:] if len(d) >= 10 else d


# ------------------------------------------------------------------------------ the voice backend
class Runtime:
    """Loads the models once and runs one Voxera call per phone call."""

    def __init__(self, load_models: bool = True):
        self.ready = threading.Event()
        self._busy = threading.Lock()
        self.vx = self.ml = None
        self.fillers: dict = {}
        self.calls_served = 0
        self._load = load_models

    def boot(self) -> None:
        if not self._load:
            self.ready.set()
            return
        t = time.time()
        import voxera                                     # noqa: F401  (imports voxera_core, care, summary ...)
        import voxera_core as vx
        from voxera_multilang import catalog as cat
        from voxera_multilang.integration import MultiLang
        self.vx = vx
        vx.load_stt()
        vx.load_tts()
        if voxera.ML_OK:
            self.ml = MultiLang(vx)
            if self.ml.enabled:
                self.ml.boot()
                if self.ml._boot_thread:
                    self.ml._boot_thread.join()
                _log(f"English fixed lines from disk: {self.ml.load_english_cache()}")
                for lang in ("en", "hi", "mr"):           # short "one moment" clips, ready before anyone calls
                    try:
                        self.fillers[lang] = self.ml.tts.synth(cat.say("one_moment", lang), lang, "priya")
                    except Exception:                      # noqa: BLE001
                        pass
        # anything not already loaded from the disk cache is rendered now (skips what is cached)
        vx.prewarm_phrases([voxera.GREETING, voxera.FALLBACK_REPLY] + voxera.CANNED_RESPONSES)
        if getattr(voxera, "PF_PHRASES", None):                  # triage questions, ID prompts ... rendered before anyone calls
            vx.prewarm_phrases(voxera.PF_PHRASES)
        _log(f"ready in {time.time() - t:.1f}s  (multilingual={'on' if self.ml and self.ml.enabled else 'off'})")
        self.ready.set()

    # ---- one call ---------------------------------------------------------------------------------------
    def run_call(self, chan: PhoneChannel, info: dict) -> Optional[str]:
        if not self._busy.acquire(blocking=False):
            _log("second caller while a call is in progress: refused")
            return "busy"
        try:
            return self._run_call(chan, info)
        finally:
            self._busy.release()

    def _run_call(self, chan: PhoneChannel, info: dict) -> str:
        import voxera
        from voxera_multilang import catalog as cat
        vx = self.vx
        phone = caller_digits(info.get("from", ""))
        call = voxera.Call()
        call.speaker = chan.speak
        call.curated_only = True                        # on the phone Voxera never uses the free-form LLM
        chan.synth = vx.synthesize
        chan.is_cached = lambda text: vx._tts_key(text) in vx._tts_cache
        ml = self.ml if (self.ml and self.ml.enabled) else None
        if ml:
            ml.new_call()
            call.ml = ml
        lang_now = lambda: ml.lang if ml else "en"                       # noqa: E731
        # no "one moment" filler by default (replies are fast now); VOXERA_PHONE_FILLER=1 turns it back on
        chan.filler = (lambda: self.fillers.get(lang_now())) if os.getenv("VOXERA_PHONE_FILLER", "0") == "1" else None
        chan.idle_prompt = lambda: chan.speak(cat.say("still_there", lang_now()), allow_barge_in=False)
        chan.idle_goodbye = lambda: chan.speak(cat.say("goodbye", lang_now()), allow_barge_in=False)

        call.open_supabase_call(phone=phone or None, name=(f"Caller {phone[-4:]}" if phone else None))
        greeting = voxera.GREETING
        if call.pf:
            greeting = call.pf.greeting()
        if ml:
            with_id = bool(call.pf and call.pf.ask_first)
            ml.prewarm_greeting(with_id=with_id)
            greeting = ml.greeting_text(with_id=with_id)
        if call.pf:
            call.pf.warm()
        _log(f"call started  from=***{phone[-4:] if phone else '----'}  sample_rate={chan.sr}")
        outcome = call.serve(chan, greeting, greeting_barge=True)
        self.calls_served += 1
        _log(f"call ended  outcome={outcome}  {chan.stats}")
        return outcome


# ------------------------------------------------------------------------------ the web app
def create_app(runtime: Optional[Runtime] = None, boot: bool = True) -> FastAPI:
    rt = runtime or Runtime()
    @asynccontextmanager
    async def lifespan(_app):
        if boot:
            threading.Thread(target=rt.boot, name="boot", daemon=True).start()
        else:
            rt.ready.set()
        yield

    app = FastAPI(title="Voxera phone backend", lifespan=lifespan)
    app.state.runtime = rt

    @app.get("/health")
    def health():
        return {"ok": True, "ready": rt.ready.is_set(), "busy": rt._busy.locked(), "calls_served": rt.calls_served}

    @app.get("/exotel/stream-url")
    def stream_url(request: Request):
        """Exotel's Voicebot applet can call an HTTPS URL that returns the WebSocket URL (handy behind a tunnel)."""
        host = request.headers.get("x-forwarded-host") or request.headers.get("host") or "localhost"
        return {"url": f"wss://{host}/exotel/stream"}

    @app.websocket("/exotel/stream")
    async def stream(ws: WebSocket):
        if not check_auth(ws.headers, dict(ws.query_params)):
            await ws.close(code=1008)
            _log("rejected a connection with bad credentials")
            return
        await ws.accept()
        loop = asyncio.get_running_loop()
        chan: Optional[PhoneChannel] = None
        worker: Optional[threading.Thread] = None
        qsr = dict(ws.query_params).get("sample-rate")

        def send(obj: dict) -> None:
            asyncio.run_coroutine_threadsafe(ws.send_text(json.dumps(obj)), loop).result(timeout=5)

        def hang_up() -> None:
            asyncio.run_coroutine_threadsafe(ws.close(), loop)

        def call_thread(ch: PhoneChannel, info: dict) -> None:
            try:
                if not rt.ready.wait(timeout=120):
                    _log("models not ready; dropping call")
                    return
                rt.run_call(ch, info)
            except Exception as e:                                       # noqa: BLE001
                _log(f"call failed: {e!r}")
            finally:
                try:
                    hang_up()                                            # Exotel moves on to the next applet (Hangup)
                except Exception:                                        # noqa: BLE001
                    pass

        try:
            while True:
                ev = json.loads(await ws.receive_text())
                kind = ev.get("event")
                if kind == "connected":
                    _log("connected")
                elif kind == "start" and chan is None:
                    st = ev.get("start", {})
                    fmt = st.get("media_format", {}) or {}
                    sr = int(fmt.get("sample_rate") or qsr or 8000)
                    chan = PhoneChannel(send=send, stream_sid=ev.get("stream_sid") or st.get("stream_sid", ""), sample_rate=sr)
                    info = {"from": st.get("from", ""), "to": st.get("to", ""), "call_sid": st.get("call_sid", ""),
                            "custom": st.get("custom_parameters", {})}
                    worker = threading.Thread(target=call_thread, args=(chan, info), name="voxera-call", daemon=True)
                    worker.start()
                elif kind == "media" and chan is not None:
                    chan.feed(base64.b64decode(ev["media"]["payload"]))
                elif kind == "dtmf":
                    _log(f"keypad digit {ev.get('dtmf', {}).get('digit')}")
                elif kind == "stop":
                    _log("stop: caller hung up")
                    break
        except WebSocketDisconnect:
            pass
        except Exception as e:                                           # noqa: BLE001
            _log(f"stream error: {e!r}")
        finally:
            if chan is not None:
                chan.close()
            if worker is not None:
                await loop.run_in_executor(None, worker.join, 15)        # let the call write its summary
            try:
                await ws.close()
            except Exception:                                            # noqa: BLE001
                pass

    return app


def main() -> None:
    import uvicorn
    port = int(os.getenv("VOXERA_PHONE_PORT", "8200"))
    print(f"\nVoxera phone backend  ws://0.0.0.0:{port}/exotel/stream   (health: /health)\n", flush=True)
    uvicorn.run(create_app(), host="0.0.0.0", port=port, log_level="warning")


if __name__ == "__main__":
    main()
