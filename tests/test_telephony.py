# Phone-line layer: endpointing, barge-in, "one moment" filler, silence handling, Exotel protocol, auth.
# No models, no network: synthetic audio and a fake call. Run: python -m pytest -q tests/test_telephony.py
import base64
import json
import threading
import time

import numpy as np
import pytest

from voxera_telephony.audio import exotel_chunks, float_to_pcm16, pcm16_to_float, resample
from voxera_telephony.channel import PhoneChannel
from voxera_telephony.errors import CallEnded
from voxera_telephony.server import Runtime, check_auth, create_app

SR = 8000
RNG = np.random.default_rng(1)


def speech(sec, amp=0.25):
    t = np.arange(int(SR * sec)) / SR
    return (amp * np.sin(2 * np.pi * 220 * t) * (0.6 + 0.4 * np.sin(2 * np.pi * 3 * t))).astype(np.float32)


def quiet(sec):
    return (RNG.normal(0, 0.002, int(SR * sec))).astype(np.float32)


def stream_in(chan, audio, gap=0.0005):
    pcm = float_to_pcm16(audio)
    for i in range(0, len(pcm), 3200):
        chan.feed(pcm[i:i + 3200])
        time.sleep(gap)


def make(**kw):
    sent = []
    ch = PhoneChannel(send=sent.append, stream_sid="S1", sample_rate=SR, **kw)
    ch.end_silence_s, ch.idle_s, ch.filler_after_s = 0.4, 30, 0
    return ch, sent


def run_capture(ch):
    box = {}

    def go():
        try:
            box["audio"] = ch.capture_utterance()
        except Exception as e:                                   # noqa: BLE001
            box["err"] = e
    t = threading.Thread(target=go)
    t.start()
    return t, box


# ----------------------------------------------------------------------------------------------- audio
def test_chunks_are_exotel_legal_multiples_of_320():
    for n in (1, 3199, 3200, 3201, 10000, 96001):
        for c in exotel_chunks(b"\x01\x00" * (n // 2 + 1), 8000):
            assert len(c) % 320 == 0
    assert len(exotel_chunks(b"\x00" * 6400, 8000)[0]) == 3200          # 100 ms


def test_pcm_roundtrip_and_resample_lengths():
    a = speech(0.5)
    assert np.allclose(pcm16_to_float(float_to_pcm16(a)), a, atol=1e-3)
    assert abs(len(resample(a, 8000, 16000)) - 2 * len(a)) <= 2
    assert abs(len(resample(np.zeros(24000, dtype=np.float32), 24000, 8000)) - 8000) <= 2


# ----------------------------------------------------------------------------------------------- hearing
def test_endpointing_returns_the_sentence_not_the_silence():
    ch, _ = make()
    t, box = run_capture(ch)
    stream_in(ch, np.concatenate([quiet(0.6), speech(1.2), quiet(1.0)]))
    t.join(5)
    a = box["audio"]
    assert 1.2 <= len(a) / 16000 <= 1.9                                 # the speech plus a short tail
    assert ch.stats["utterances"] == 1


def test_a_click_is_ignored_and_the_real_sentence_still_captured():
    ch, _ = make()
    t, box = run_capture(ch)
    stream_in(ch, np.concatenate([quiet(0.4), speech(0.1), quiet(0.8), speech(1.0), quiet(1.0)]))
    t.join(5)
    assert 1.0 <= len(box["audio"]) / 16000 <= 1.7


def test_a_pause_inside_a_sentence_does_not_split_it():
    ch, _ = make()
    t, box = run_capture(ch)
    stream_in(ch, np.concatenate([quiet(0.3), speech(0.8), quiet(0.25), speech(0.8), quiet(1.0)]))
    t.join(5)
    assert len(box["audio"]) / 16000 >= 1.8


def test_hangup_unblocks_the_listener():
    ch, _ = make()
    t, box = run_capture(ch)
    time.sleep(0.2)
    ch.close()
    t.join(3)
    assert isinstance(box.get("err"), CallEnded)


def test_silent_line_gets_one_prompt_then_a_goodbye_then_the_call_ends():
    said = []
    ch, _ = make(idle_prompt=lambda: said.append("still there?"), idle_goodbye=lambda: said.append("bye"))
    ch.idle_s = 0.3
    t, box = run_capture(ch)
    t.join(4)
    assert said == ["still there?", "bye"] and isinstance(box.get("err"), CallEnded)


# ----------------------------------------------------------------------------------------------- speaking
def tone24(sec):
    t = np.arange(int(24000 * sec)) / 24000
    return (0.3 * np.sin(2 * np.pi * 300 * t)).astype(np.float32)


def test_speech_goes_out_as_paced_legal_media_then_a_mark():
    ch, sent = make()
    r = ch.play(tone24(0.6), allow_barge_in=True)
    media = [m for m in sent if m["event"] == "media"]
    assert r["interrupted"] is False and sent[-1]["event"] == "mark"
    assert all(len(base64.b64decode(m["media"]["payload"])) % 320 == 0 for m in media)
    assert abs(sum(len(base64.b64decode(m["media"]["payload"])) for m in media) / 2 / SR - 0.6) < 0.2
    assert all(m["stream_sid"] == "S1" for m in sent)


def test_caller_talking_over_voxera_stops_it_with_clear_and_keeps_their_words():
    ch, sent = make()

    def interrupt():
        time.sleep(0.5)
        stream_in(ch, np.concatenate([quiet(0.1), speech(1.5)]), gap=0.01)
    threading.Thread(target=interrupt, daemon=True).start()
    t0 = time.time()
    r = ch.play(tone24(6.0), allow_barge_in=True)
    assert r["interrupted"] and time.time() - t0 < 3.5                  # not 6 s
    assert sent[-1]["event"] == "clear"
    assert r["pending_audio"] and len(r["pending_audio"][0]) > 16000 * 0.3
    assert ch.stats["barge_ins"] == 1


def test_a_cough_does_not_interrupt():
    ch, sent = make()
    threading.Thread(target=lambda: (time.sleep(0.3), stream_in(ch, speech(0.12), gap=0.005)), daemon=True).start()
    r = ch.play(tone24(1.2), allow_barge_in=True)
    assert not r["interrupted"] and not any(m["event"] == "clear" for m in sent)


def test_emergency_speech_cannot_be_interrupted():
    ch, sent = make()
    threading.Thread(target=lambda: stream_in(ch, speech(1.5), gap=0.01), daemon=True).start()
    r = ch.play(tone24(1.0), allow_barge_in=False)
    assert not r["interrupted"] and not any(m["event"] == "clear" for m in sent)


def test_hanging_up_mid_sentence_raises_call_ended():
    ch, _ = make()
    threading.Thread(target=lambda: (time.sleep(0.3), ch.close()), daemon=True).start()
    with pytest.raises(CallEnded):
        ch.play(tone24(3.0))


def test_one_moment_filler_plays_when_the_reply_is_slow_and_not_when_it_is_quick():
    ch, sent = make(filler=lambda: tone24(0.3))
    ch.filler_after_s = 0.25
    t, box = run_capture(ch)
    stream_in(ch, np.concatenate([quiet(0.3), speech(0.8), quiet(1.0)]))
    t.join(5)
    time.sleep(0.6)                                                      # Voxera is still "thinking"
    assert ch.stats["fillers"] == 1 and any(m["event"] == "media" for m in sent)
    ch2, sent2 = make(filler=lambda: tone24(0.3))
    ch2.filler_after_s = 0.5
    t, box = run_capture(ch2)
    stream_in(ch2, np.concatenate([quiet(0.3), speech(0.8), quiet(1.0)]))
    t.join(5)
    ch2.play(tone24(0.2))                                               # the reply came before the timer
    time.sleep(0.7)
    assert ch2.stats["fillers"] == 0


# ----------------------------------------------------------------------------------------------- Exotel protocol
class FakeRuntime(Runtime):
    """Stands in for the models: greets, then answers each of two sentences with a tone, then ends the call."""

    def __init__(self):
        super().__init__(load_models=False)
        self.started = None

    def run_call(self, chan, info):
        self.started = info
        chan.synth = lambda text, tracker=None: tone24(0.5)
        chan.end_silence_s = 0.4
        chan.speak("Hello")
        for _ in range(2):
            chan.capture_utterance()
            chan.speak("Reply", allow_barge_in=True)
        return "completed"


def start_msg(sr="8000"):
    return {"event": "start", "sequence_number": 1, "stream_sid": "S9",
            "start": {"stream_sid": "S9", "call_sid": "C1", "account_sid": "A1", "from": "+919876543210",
                      "to": "+911234567890", "custom_parameters": {}, "media_format": {"encoding": "raw", "sample_rate": sr, "bit_rate": "128"}}}


def media_msg(pcm, n):
    return {"event": "media", "sequence_number": n, "stream_sid": "S9",
            "media": {"chunk": n, "timestamp": str(n * 100), "payload": base64.b64encode(pcm).decode()}}


def test_full_exotel_conversation_over_the_websocket(monkeypatch):
    from starlette.testclient import TestClient
    monkeypatch.delenv("VOXERA_STREAM_TOKEN", raising=False)
    rt = FakeRuntime()
    replies = {"media": 0, "marks": 0}
    with TestClient(create_app(rt, boot=False)) as c:
        assert c.get("/health").json()["ok"]
        with c.websocket_connect("/exotel/stream") as ws:
            ws.send_text(json.dumps({"event": "connected"}))
            ws.send_text(json.dumps(start_msg()))
            n = 2
            said = 0
            ws_open = True

            def pump(audio):
                nonlocal n
                pcm = float_to_pcm16(audio)
                for i in range(0, len(pcm), 3200):
                    ws.send_text(json.dumps(media_msg(pcm[i:i + 3200], n)))
                    n += 1

            deadline = time.time() + 25
            while ws_open and time.time() < deadline:
                # Exotel streams continuously; after the greeting has arrived, say a sentence twice
                if replies["marks"] == said + 1 and said < 2:
                    pump(np.concatenate([quiet(0.3), speech(0.9), quiet(0.9)]))
                    said += 1
                else:
                    pump(quiet(0.1))
                try:
                    ev = json.loads(ws.receive_text())
                except Exception:                                   # the server closed the line after the last reply
                    ws_open = False
                    break
                if ev["event"] == "media":
                    replies["media"] += 1
                    assert ev["stream_sid"] == "S9" and len(base64.b64decode(ev["media"]["payload"])) % 320 == 0
                elif ev["event"] == "mark":
                    replies["marks"] += 1
                time.sleep(0.02)
    assert rt.started["from"] == "+919876543210"
    assert replies["marks"] == 3 and replies["media"] >= 3            # greeting + two replies, then Voxera hung up


def test_credentials_basic_auth_or_token_and_open_when_unset(monkeypatch):
    monkeypatch.setenv("VOXERA_STREAM_TOKEN", "s3cret")
    good = "Basic " + base64.b64encode(b"key:s3cret").decode()
    bad = "Basic " + base64.b64encode(b"key:nope").decode()
    assert check_auth({"authorization": good}, {}) and check_auth({}, {"token": "s3cret"})
    assert not check_auth({"authorization": bad}, {}) and not check_auth({}, {}) and not check_auth({}, {"token": "x"})
    monkeypatch.delenv("VOXERA_STREAM_TOKEN")
    assert check_auth({}, {})


def test_bad_credentials_are_refused(monkeypatch):
    from starlette.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect
    monkeypatch.setenv("VOXERA_STREAM_TOKEN", "s3cret")
    with TestClient(create_app(FakeRuntime(), boot=False)) as c:
        with pytest.raises(WebSocketDisconnect):
            with c.websocket_connect("/exotel/stream?token=wrong") as ws:
                ws.receive_text()


def test_stream_url_helper_returns_a_wss_url():
    from starlette.testclient import TestClient
    with TestClient(create_app(FakeRuntime(), boot=False)) as c:
        r = c.get("/exotel/stream-url", headers={"host": "abc.trycloudflare.com"}).json()
    assert r["url"] == "wss://abc.trycloudflare.com/exotel/stream"


# ----------------------------------------------------------------------------------------------- streamed speech
def test_long_uncached_reply_is_spoken_sentence_by_sentence_so_the_caller_hears_it_early():
    ch, sent = make()
    ch.is_cached = lambda text: False
    stamps = []

    def slow_synth(text, tracker=None):
        time.sleep(0.5)                                               # each sentence takes 0.5 s to synthesise
        return tone24(0.4)
    ch.synth = slow_synth
    orig = ch.send
    ch.send = lambda m: (stamps.append(time.time()) if m["event"] == "media" else None, orig(m))[1]
    t0 = time.time()
    r = ch.speak("First sentence here please. Second sentence is right here. Third one comes last of all.")
    assert r["ok"] and not r["interrupted"]
    assert stamps and stamps[0] - t0 < 0.9                            # first sound after ONE sentence, not after all three
    assert sent[-1]["event"] == "mark"
    assert abs(sum(len(base64.b64decode(m["media"]["payload"])) for m in sent if m["event"] == "media") / 2 / SR - 1.2) < 0.5


def test_cached_or_single_sentence_lines_are_not_split():
    ch, sent = make()
    calls = []
    ch.synth = lambda text, tracker=None: (calls.append(text), tone24(0.3))[1]
    ch.is_cached = lambda text: True
    ch.speak("One. Two. Three sentences but pre-rendered.")
    assert len(calls) == 1


def test_barge_in_works_while_a_streamed_reply_is_playing():
    ch, sent = make()
    ch.is_cached = lambda text: False
    ch.synth = lambda text, tracker=None: tone24(2.0)
    threading.Thread(target=lambda: (time.sleep(0.6), stream_in(ch, speech(1.5), gap=0.01)), daemon=True).start()
    t0 = time.time()
    r = ch.speak("Sentence number one is here. Sentence number two is here. Sentence number three is here.", allow_barge_in=True)
    assert r["interrupted"] and time.time() - t0 < 4.0 and sent[-1]["event"] == "clear"


# ----------------------------------------------------------------------------------------------- voice polish, filler policy
def test_phone_level_evens_out_loudness_and_never_clips():
    from voxera_telephony.audio import phone_level
    quiet_v, loud_v = tone24(1.0) * 0.1, tone24(1.0) * 1.9
    a, b = phone_level(quiet_v), phone_level(loud_v)
    rms = lambda x: float(np.sqrt(np.mean(x[np.abs(x) > 0.01] ** 2)))
    assert abs(rms(a) - rms(b)) < 0.06 and rms(a) > rms(quiet_v) * 2
    assert np.abs(a).max() <= 0.85 + 1e-6 and np.abs(b).max() <= 0.85 + 1e-6
    assert np.array_equal(phone_level(np.zeros(100, dtype=np.float32)), np.zeros(100, dtype=np.float32))


def test_one_moment_is_never_said_on_two_turns_in_a_row():
    ch, sent = make(filler=lambda: tone24(0.2))
    ch.filler_after_s = 0.15
    counts = []
    for _ in range(3):
        t, box = run_capture(ch)
        stream_in(ch, np.concatenate([quiet(0.3), speech(0.8), quiet(1.0)]))
        t.join(5)
        time.sleep(0.5)
        counts.append(ch.stats["fillers"])
        ch.play(tone24(0.2))
    assert counts == [1, 1, 1]                                            # said once, then skipped the next two turns


def test_sentences_stay_separate_when_they_are_pre_rendered():
    ch, _ = make()
    ch.is_cached = lambda t: t in {"Okay."}
    assert ch._sentences("Okay. How long has this been going on?") == ["Okay.", "How long has this been going on?"]
    ch.is_cached = lambda t: False
    assert ch._sentences("Okay. How long has this been going on?") == ["Okay. How long has this been going on?"]


def test_quiet_caller_audio_is_lifted_before_recognition_and_loud_audio_is_left_alone():
    from voxera_telephony.audio import level_for_stt
    q = (speech(1.0, amp=0.02)).astype(np.float32)
    lifted = level_for_stt(q)
    rms = lambda x: float(np.sqrt(np.mean(x[np.abs(x) > 0.004] ** 2)))
    assert rms(lifted) > rms(q) * 3 and np.abs(lifted).max() <= 0.99
    loud = speech(1.0, amp=0.4)
    assert np.allclose(level_for_stt(loud), loud)
    assert np.array_equal(level_for_stt(np.zeros(50, dtype=np.float32)), np.zeros(50, dtype=np.float32))


def test_no_one_moment_by_default_on_a_live_call(monkeypatch):
    monkeypatch.delenv("VOXERA_PHONE_FILLER", raising=False)
    import inspect
    from voxera_telephony import server
    assert 'VOXERA_PHONE_FILLER", "0") == "1"' in inspect.getsource(server.Runtime._run_call)
