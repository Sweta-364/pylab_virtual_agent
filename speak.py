import base64
import io
import os
import platform
import shutil
import subprocess
import tempfile
import time
import wave

import numpy as np
import requests

try:
    import pyaudio
except Exception:
    pyaudio = None


SARVAM_TTS_URL = "https://api.sarvam.ai/text-to-speech"


def _load_local_env():
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.isfile(env_path):
        return

    try:
        with open(env_path, "r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        return


def _get_api_key():
    return os.getenv("SARVAM_API_KEY", "").strip()


def _build_payload(text):
    # Sarvam bulbul:v3 currently supports up to 2500 chars per request.
    trimmed_text = str(text)[:2500]
    return {
        "text": trimmed_text,
        "target_language_code": os.getenv("SARVAM_TTS_LANGUAGE", "en-IN"),
        "speaker": os.getenv("SARVAM_TTS_SPEAKER", "shubh"),
        "model": os.getenv("SARVAM_TTS_MODEL", "bulbul:v3"),
        "pace": float(os.getenv("SARVAM_TTS_PACE", "1.0")),
        "output_audio_codec": "wav",
    }


def _generate_audio_bytes(text):
    api_key = _get_api_key()
    if not api_key:
        return None

    headers = {
        "api-subscription-key": api_key,
        "Content-Type": "application/json",
    }

    response = requests.post(
        SARVAM_TTS_URL,
        headers=headers,
        json=_build_payload(text),
        timeout=30,
    )
    response.raise_for_status()

    data = response.json()
    audios = data.get("audios") or []
    if not audios:
        return None

    return base64.b64decode(audios[0])


def _start_audio_process(file_path):
    system = platform.system()

    if system == "Windows":
        # Use built-in Windows SoundPlayer.
        return subprocess.Popen(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                f"(New-Object Media.SoundPlayer '{file_path}').PlaySync();",
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    if system == "Darwin" and shutil.which("afplay"):
        return subprocess.Popen(
            ["afplay", file_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    # Linux and other POSIX fallbacks.
    for player_cmd in (("aplay",), ("paplay",), ("ffplay", "-nodisp", "-autoexit", "-loglevel", "quiet")):
        if shutil.which(player_cmd[0]):
            return subprocess.Popen(
                [*player_cmd, file_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    return None


def _play_wav_file(file_path, stop_event=None):
    process = _start_audio_process(file_path)
    if process is None:
        return False

    while process.poll() is None:
        if stop_event is not None and stop_event.is_set():
            process.terminate()
            time.sleep(0.1)
            if process.poll() is None:
                process.kill()
            return False
        time.sleep(0.05)

    return True


def _play_wav_bytes_with_pyaudio(audio_bytes, stop_event=None, fade_out_duration=0.5):
    if pyaudio is None:
        return False

    player = pyaudio.PyAudio()
    stream = None
    wav_file = None
    try:
        wav_file = wave.open(io.BytesIO(audio_bytes), "rb")
        sample_width = wav_file.getsampwidth()
        channels = wav_file.getnchannels()
        frame_rate = wav_file.getframerate()

        stream = player.open(
            format=player.get_format_from_width(sample_width),
            channels=channels,
            rate=frame_rate,
            output=True,
        )

        chunk_frames = 1024
        fade_frames_remaining = 0
        stop_requested = False
        if stop_event is not None and stop_event.is_set():
            return False

        while True:
            frames = wav_file.readframes(chunk_frames)
            if not frames:
                break

            if stop_event is not None and stop_event.is_set() and not stop_requested:
                stop_requested = True
                fade_frames_remaining = max(1, int(frame_rate * max(0.0, float(fade_out_duration))))

            if stop_requested:
                if sample_width == 2:
                    samples = np.frombuffer(frames, dtype=np.int16).astype(np.float32)
                    if samples.size:
                        usable = min(samples.size, fade_frames_remaining * max(1, channels))
                        ramp = np.linspace(1.0, 0.0, num=usable, endpoint=True, dtype=np.float32)
                        samples[:usable] *= ramp
                        if usable < samples.size:
                            samples[usable:] = 0.0
                        frames = np.clip(samples, -32768, 32767).astype(np.int16).tobytes()
                stream.write(frames)
                fade_frames_remaining -= chunk_frames
                if fade_frames_remaining <= 0:
                    return False
                continue

            stream.write(frames)
        return True
    except Exception:
        return False
    finally:
        try:
            if stream is not None:
                stream.stop_stream()
                stream.close()
        except Exception:
            pass
        try:
            if wav_file is not None:
                wav_file.close()
        except Exception:
            pass
        try:
            player.terminate()
        except Exception:
            pass


def _play_audio_bytes(audio_bytes, stop_event=None, fade_out_duration=0.5):
    if _play_wav_bytes_with_pyaudio(
        audio_bytes,
        stop_event=stop_event,
        fade_out_duration=fade_out_duration,
    ):
        return True

    with tempfile.NamedTemporaryFile(delete=False, suffix=".wav") as temp_audio:
        temp_audio.write(audio_bytes)
        temp_path = temp_audio.name

    try:
        return _play_wav_file(temp_path, stop_event=stop_event)
    finally:
        try:
            os.remove(temp_path)
        except OSError:
            pass


def speak(text, stop_event=None, fade_out_duration=0.5):
    if not text:
        return False

    if stop_event is not None and stop_event.is_set():
        return False

    try:
        _load_local_env()
        audio_bytes = _generate_audio_bytes(text)
        if audio_bytes:
            return _play_audio_bytes(
                audio_bytes,
                stop_event=stop_event,
                fade_out_duration=fade_out_duration,
            )
    except Exception:
        # Keep assistant functional even when TTS backend/network is unavailable.
        return False

    return False
