"""Riconoscimento vocale (faster-whisper) e sintesi vocale (Piper), entrambi locali."""
from __future__ import annotations

import io
import logging
import os
import re
import subprocess
import tempfile
import threading
import wave
from pathlib import Path

log = logging.getLogger(__name__)

# Aiuta Whisper con il lessico del dominio.
STT_PROMPT = "Prenotazione di un tavolo al ristorante. Codice prenotazione, persone, pranzo, cena, sabato, domenica."


def _register_pip_cuda_libs() -> None:
    """Rende visibili le DLL CUDA installate via pip (nvidia-cublas-cu12, nvidia-cudnn-cu12)."""
    try:
        import nvidia
    except ImportError:
        return
    for base in nvidia.__path__:
        for bin_dir in Path(base).glob("*/bin"):
            os.environ["PATH"] = str(bin_dir) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(bin_dir))


class SpeechToText:
    """device: 'cuda', 'cpu' oppure 'auto' (GPU se disponibile, altrimenti CPU)."""

    def __init__(self, model_size: str = "small", device: str = "auto", compute_type: str = "auto"):
        from faster_whisper import WhisperModel

        if device in ("cuda", "auto"):
            _register_pip_cuda_libs()
            try:
                self._model = WhisperModel(model_size, device="cuda",
                                           compute_type="float16" if compute_type == "auto" else compute_type)
                self._warm_up()  # le librerie CUDA mancanti emergono solo alla prima trascrizione
                log.info("Whisper '%s' su GPU", model_size)
                return
            except Exception as exc:
                if device == "cuda":
                    raise
                log.warning("GPU non utilizzabile per Whisper (%s): uso la CPU", exc)
        self._model = WhisperModel(model_size, device="cpu",
                                   compute_type="int8" if compute_type == "auto" else compute_type)
        self._warm_up()
        log.info("Whisper '%s' su CPU", model_size)

    def _warm_up(self) -> None:
        import numpy as np

        segments, _ = self._model.transcribe(np.zeros(16000, dtype=np.float32), language="it")
        list(segments)

    def transcribe(self, audio: bytes) -> str:
        """Accetta audio in qualsiasi formato decodificabile (webm/ogg dal browser, wav...)."""
        samples = decode_audio(audio)
        if samples.size == 0:
            return ""
        segments, _ = self._model.transcribe(
            samples, language="it", beam_size=5, vad_filter=True, initial_prompt=STT_PROMPT
        )
        return " ".join(s.text.strip() for s in segments).strip()


def decode_audio(audio: bytes, rate: int = 16000):
    """Decodifica in mono float32 a 16 kHz, il formato atteso da Whisper.

    Fatto qui (e non da faster-whisper) perché il suo decoder non è compatibile con PyAV >= 16.
    """
    import av
    import numpy as np

    resampler = av.AudioResampler(format="s16", layout="mono", rate=rate)
    chunks = []
    try:
        with av.open(io.BytesIO(audio), mode="r") as container:
            for frame in container.decode(audio=0):
                chunks.extend(f.to_ndarray() for f in resampler.resample(frame))
            chunks.extend(f.to_ndarray() for f in resampler.resample(None))
    except (av.error.FFmpegError, ValueError):
        log.warning("Audio non decodificabile (%d byte)", len(audio))
        return np.zeros(0, dtype=np.float32)
    if not chunks:
        return np.zeros(0, dtype=np.float32)
    return np.concatenate(chunks, axis=1).reshape(-1).astype(np.float32) / 32768.0


def clean_for_speech(text: str) -> str:
    """Rende il testo adatto alla lettura ad alta voce."""
    text = re.sub(r"[*_#`>\[\]]", "", text)
    text = text.replace("€", " euro").replace("&", " e ")
    # I codici prenotazione vanno letti cifra per cifra: "4 8 2 1 5 7", non "quattrocentottantamila...".
    text = re.sub(r"\b\d{5,}\b", lambda m: " ".join(m.group(0)), text)
    return re.sub(r"\s+", " ", text).strip()


class PiperTTS:
    def __init__(self, model_path: Path):
        from piper import PiperVoice

        log.info("Carico la voce Piper %s", model_path.name)
        self._voice = PiperVoice.load(str(model_path))
        self._lock = threading.Lock()

    def synthesize(self, text: str) -> bytes:
        buf = io.BytesIO()
        with self._lock, wave.open(buf, "wb") as wav:
            self._voice.synthesize_wav(clean_for_speech(text), wav)
        return buf.getvalue()


class WindowsTTS:
    """Ripiego: sintesi vocale di Windows (System.Speech), anch'essa locale e senza download."""

    _SCRIPT = (
        "Add-Type -AssemblyName System.Speech;"
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer;"
        "$v = $s.GetInstalledVoices() | Where-Object { $_.VoiceInfo.Culture.Name -eq 'it-IT' } | Select-Object -First 1;"
        "if ($v) { $s.SelectVoice($v.VoiceInfo.Name) };"
        "$s.SetOutputToWaveFile($env:BACULA_TTS_OUT);"
        "$s.Speak($env:BACULA_TTS_TEXT);"
        "$s.Dispose()"
    )

    def synthesize(self, text: str) -> bytes:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "out.wav"
            # Testo passato via variabile d'ambiente: nessun problema di escaping.
            env = {**os.environ, "BACULA_TTS_TEXT": clean_for_speech(text), "BACULA_TTS_OUT": str(out)}
            subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", self._SCRIPT],
                           env=env, check=True, capture_output=True, timeout=60)
            return out.read_bytes()


def make_tts(engine: str, piper_model: Path):
    """engine: 'piper', 'windows' oppure 'auto' (Piper se la voce è scaricata, altrimenti Windows)."""
    if engine in ("piper", "auto"):
        if piper_model.exists():
            try:
                return PiperTTS(piper_model)
            except Exception:
                if engine == "piper":
                    raise
                log.exception("Piper non disponibile, uso la voce di Windows")
        elif engine == "piper":
            raise FileNotFoundError(f"Voce Piper non trovata: {piper_model}. Esegui setup.ps1.")
    log.info("Sintesi vocale: voce di Windows")
    return WindowsTTS()
