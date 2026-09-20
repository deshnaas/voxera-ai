"""Build a fast int8 multilingual Whisper (CTranslate2) from the OpenAI checkpoint already on disk.

    .venv312\\Scripts\\python.exe -m voxera_multilang.build_fast_stt            # small (default)
    .venv312\\Scripts\\python.exe -m voxera_multilang.build_fast_stt base

Why: openai-whisper 'small' needs 4-8 s per turn on this CPU (Hindi/Marathi are the slowest because Devanagari
uses many tokens). The same weights as an int8 CTranslate2 model run ~4x faster. The public conversion of these
weights is hosted on a CDN that is blocked here, so we convert locally: OpenAI .pt -> HF Whisper -> CTranslate2.
Only two small tokenizer files are downloaded (they are not blocked).
"""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(HERE, ".model_cache")

RENAME = [
    ("blocks", "layers"), ("mlp.0", "fc1"), ("mlp.2", "fc2"), ("mlp_ln", "final_layer_norm"),
    (".attn.query", ".self_attn.q_proj"), (".attn.key", ".self_attn.k_proj"), (".attn.value", ".self_attn.v_proj"),
    (".attn_ln", ".self_attn_layer_norm"), (".attn.out", ".self_attn.out_proj"),
    (".cross_attn.query", ".encoder_attn.q_proj"), (".cross_attn.key", ".encoder_attn.k_proj"),
    (".cross_attn.value", ".encoder_attn.v_proj"), (".cross_attn_ln", ".encoder_attn_layer_norm"),
    (".cross_attn.out", ".encoder_attn.out_proj"), ("decoder.ln.", "decoder.layer_norm."),
    ("encoder.ln.", "encoder.layer_norm."), ("token_embedding", "embed_tokens"),
    ("encoder.positional_embedding", "encoder.embed_positions.weight"),
    ("decoder.positional_embedding", "decoder.embed_positions.weight"), ("ln_post", "layer_norm"),
]


def _fix_config(out: str) -> None:
    """The HF->CT2 step leaves lang_ids empty (no generation config), which breaks language detection.
    Take them from the original OpenAI tokenizer."""
    from whisper.tokenizer import get_tokenizer
    tok = get_tokenizer(multilingual=True)
    path = os.path.join(out, "config.json")
    cfg = json.load(open(path))
    cfg["lang_ids"] = list(tok.all_language_tokens)
    json.dump(cfg, open(path, "w"), indent=2)


def convert(size: str = "small") -> str:
    import torch
    import whisper
    from huggingface_hub import hf_hub_download
    from transformers import WhisperConfig, WhisperForConditionalGeneration
    import ctranslate2

    out = os.path.join(OUT_ROOT, f"whisper-{size}-int8")
    if os.path.exists(os.path.join(out, "model.bin")):
        _fix_config(out)
        return out
    os.makedirs(OUT_ROOT, exist_ok=True)
    print(f"[build] loading OpenAI '{size}' checkpoint ...")
    src = whisper.load_model(size, device="cpu")
    d = src.dims
    cfg = WhisperConfig(
        vocab_size=d.n_vocab, encoder_layers=d.n_audio_layer, encoder_attention_heads=d.n_audio_head,
        decoder_layers=d.n_text_layer, decoder_attention_heads=d.n_text_head, max_source_positions=d.n_audio_ctx,
        max_target_positions=d.n_text_ctx, d_model=d.n_audio_state, encoder_ffn_dim=d.n_audio_state * 4,
        decoder_ffn_dim=d.n_text_state * 4, num_mel_bins=d.n_mels, activation_function="gelu",
        decoder_start_token_id=50258, bos_token_id=50257, eos_token_id=50257, pad_token_id=50257,
        suppress_tokens=[], begin_suppress_tokens=[220, 50257])
    sd = {}
    for k, v in src.state_dict().items():
        if k == "encoder.conv1.bias" or k.startswith("encoder.conv") or True:
            nk = k
            for a, b in RENAME:
                nk = nk.replace(a, b)
            sd["model." + nk] = v
    sd["proj_out.weight"] = sd["model.decoder.embed_tokens.weight"]
    sd = {k: v for k, v in sd.items() if "encoder.embed_positions" not in k or True}
    hf = WhisperForConditionalGeneration(cfg)
    missing, unexpected = hf.load_state_dict(sd, strict=False)
    real_missing = [m for m in missing if "embed_positions" not in m and m != "proj_out.weight"]
    if real_missing or unexpected:
        raise SystemExit(f"weight mapping incomplete: missing={real_missing[:5]} unexpected={list(unexpected)[:5]}")

    with tempfile.TemporaryDirectory() as tmp:
        hf.save_pretrained(tmp)
        for fn in ("tokenizer.json", "preprocessor_config.json", "vocabulary.json"):
            try:
                hf_hub_download(f"Systran/faster-whisper-{size}", fn if fn != "vocabulary.json" else "vocabulary.txt", local_dir=tmp)
            except Exception:                                    # noqa: BLE001
                pass
        print("[build] converting to CTranslate2 int8 ...")
        conv = ctranslate2.converters.TransformersConverter(
            tmp, copy_files=[f for f in ("tokenizer.json", "preprocessor_config.json") if os.path.exists(os.path.join(tmp, f))])
        conv.convert(out, quantization="int8", force=True)
    _fix_config(out)
    print(f"[build] done -> {out}")
    return out


if __name__ == "__main__":
    convert(sys.argv[1] if len(sys.argv) > 1 else "small")
