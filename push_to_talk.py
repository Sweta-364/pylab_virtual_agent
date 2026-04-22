import os
import shutil
import subprocess
import sys
import tempfile
import time
import wave

import numpy as np
import pyaudio

try:
    import keyboard
except Exception:
    keyboard = None


class PushToTalkRecorder:
    CHUNK = 1024
    FORMAT = pyaudio.paInt16
    CHANNELS = 1
    TARGET_RATE = 16000
    MAX_WAIT_SECONDS = 120
    MAX_RECORD_SECONDS = 60
    RETRY_DELAY_SECONDS = 0.5
    MIN_RMS = 0.003
    ZERO_RMS_EPSILON = 1e-7
    MAX_ZERO_RMS_RETRIES = 3
    RELEASE_GRACE_SECONDS = 0.45
    MIN_RECORD_SECONDS = 0.6
    LEADING_SILENCE_THRESHOLD = 0.008
    TRAILING_SILENCE_THRESHOLD = 0.006
    KEEP_SILENCE_SECONDS = 0.12

    def __init__(self):
        self._pw_record_path = shutil.which("pw-record")
        self._alsa_error_handler = None
        self._suppress_alsa_warnings()
        self.audio_interface = pyaudio.PyAudio()
        self.debug_mic = os.getenv("VA_MIC_DEBUG", "").strip().lower() in {"1", "true", "yes", "on"}
        self.capture_backend = self._resolve_capture_backend()
        self._input_candidates = self._build_input_candidates()
        self._current_candidate_pos = 0
        self._zero_rms_streak = 0
        self.input_device_index = self._resolve_input_device_index()
        self.input_rate = self._resolve_input_rate(self.input_device_index)
        self._log_input_candidates_if_debug()
        self._log_backend()

    def _resolve_capture_backend(self):
        env_backend = os.getenv("VA_CAPTURE_BACKEND", "").strip().lower()
        if env_backend in {"pipewire", "pyaudio"}:
            return env_backend
        if sys.platform.startswith("linux") and self._pw_record_path:
            return "pipewire"
        return "pyaudio"

    def _log_backend(self):
        if self.capture_backend == "pipewire":
            print("[MIC] Capture backend: PipeWire (`pw-record`).")
            return
        self._log_input_device()

    def _suppress_alsa_warnings(self):
        if not sys.platform.startswith("linux"):
            return
        try:
            import ctypes

            error_handler_type = ctypes.CFUNCTYPE(
                None,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
                ctypes.c_int,
                ctypes.c_char_p,
            )

            def _handler(_filename, _line, _function, _err, _fmt):
                return

            self._alsa_error_handler = error_handler_type(_handler)
            asound = ctypes.cdll.LoadLibrary("libasound.so")
            asound.snd_lib_error_set_handler(self._alsa_error_handler)
        except Exception:
            self._alsa_error_handler = None

    @staticmethod
    def _is_generic_or_virtual_name(name):
        lowered = str(name).strip().lower()
        generic_tokens = (
            "default",
            "sysdefault",
            "dmix",
            "dsnoop",
            "surround",
            "front",
            "rear",
            "center_lfe",
            "side",
            "null",
            "jack",
        )
        return any(token in lowered for token in generic_tokens)

    def _build_input_candidates(self):
        candidates = []
        try:
            for device_index in range(self.audio_interface.get_device_count()):
                try:
                    info = self.audio_interface.get_device_info_by_index(device_index)
                except Exception:
                    continue
                if int(info.get("maxInputChannels", 0)) <= 0:
                    continue

                host_api_name = ""
                try:
                    host_api_info = self.audio_interface.get_host_api_info_by_index(
                        int(info.get("hostApi", 0)),
                    )
                    host_api_name = str(host_api_info.get("name", "")).lower()
                except Exception:
                    pass

                dev_name = str(info.get("name", ""))
                priority = 4
                if "pipewire" in host_api_name or "pulse" in host_api_name:
                    priority = 0
                elif "alsa" in host_api_name:
                    priority = 1
                elif "wasapi" in host_api_name or "core audio" in host_api_name:
                    priority = 2
                elif "oss" in host_api_name:
                    priority = 3

                if self._is_generic_or_virtual_name(dev_name):
                    priority += 3
                if "monitor" in dev_name.lower():
                    priority += 2
                if "mic" in dev_name.lower() or "input" in dev_name.lower():
                    priority -= 1

                candidates.append((priority, int(device_index)))
        except Exception:
            return []

        candidates.sort(key=lambda item: item[0])
        return [idx for _, idx in candidates]

    def _log_input_candidates_if_debug(self):
        if not self.debug_mic:
            return
        print("[MIC DEBUG] Candidate input devices (best first):")
        if not self._input_candidates:
            print("[MIC DEBUG] No input devices detected by PortAudio.")
            return

        for idx in self._input_candidates:
            try:
                info = self.audio_interface.get_device_info_by_index(idx)
                host_api_name = ""
                try:
                    host_api_info = self.audio_interface.get_host_api_info_by_index(
                        int(info.get("hostApi", 0)),
                    )
                    host_api_name = str(host_api_info.get("name", "unknown"))
                except Exception:
                    host_api_name = "unknown"

                name = str(info.get("name", f"#{idx}"))
                rate = int(round(float(info.get("defaultSampleRate", self.TARGET_RATE))))
                channels = int(info.get("maxInputChannels", 0))
                print(
                    f"[MIC DEBUG] index={idx} name='{name}' host_api='{host_api_name}' "
                    f"channels={channels} rate={rate}",
                )
            except Exception:
                continue

    def _resolve_input_device_index(self):
        env_index = os.getenv("VA_MIC_DEVICE_INDEX", "").strip()
        if env_index:
            try:
                idx = int(env_index)
                info = self.audio_interface.get_device_info_by_index(idx)
                if int(info.get("maxInputChannels", 0)) > 0:
                    if idx in self._input_candidates:
                        self._current_candidate_pos = self._input_candidates.index(idx)
                    return idx
                print(
                    f"[MIC] VA_MIC_DEVICE_INDEX={idx} is not an input device. "
                    "Falling back to auto-detect.",
                )
            except Exception:
                print(
                    f"[MIC] VA_MIC_DEVICE_INDEX={env_index!r} is invalid. "
                    "Falling back to auto-detect.",
                )

        try:
            default_info = self.audio_interface.get_default_input_device_info()
            if int(default_info.get("maxInputChannels", 0)) > 0:
                default_index = int(default_info.get("index", 0))
                default_name = str(default_info.get("name", ""))
                if not self._is_generic_or_virtual_name(default_name):
                    if default_index in self._input_candidates:
                        self._current_candidate_pos = self._input_candidates.index(default_index)
                    return default_index
        except Exception:
            pass

        if not self._input_candidates:
            return None
        self._current_candidate_pos = 0
        return int(self._input_candidates[self._current_candidate_pos])

    def _switch_to_next_candidate(self):
        if not self._input_candidates:
            return False
        if len(self._input_candidates) == 1:
            return False

        self._current_candidate_pos = (self._current_candidate_pos + 1) % len(self._input_candidates)
        new_index = int(self._input_candidates[self._current_candidate_pos])
        if new_index == self.input_device_index:
            return False

        self.input_device_index = new_index
        self.input_rate = self._resolve_input_rate(new_index)
        self._log_input_device()
        return True

    def _resolve_input_rate(self, device_index):
        env_rate = os.getenv("VA_MIC_RATE", "").strip()
        if env_rate:
            try:
                rate = int(float(env_rate))
                if rate > 0:
                    return rate
            except Exception:
                print(
                    f"[MIC] VA_MIC_RATE={env_rate!r} is invalid. "
                    "Falling back to device default sample rate.",
                )

        try:
            if device_index is None:
                info = self.audio_interface.get_default_input_device_info()
            else:
                info = self.audio_interface.get_device_info_by_index(device_index)
            default_rate = int(round(float(info.get("defaultSampleRate", self.TARGET_RATE))))
            if default_rate > 0:
                return default_rate
        except Exception:
            pass
        return self.TARGET_RATE

    def _log_input_device(self):
        try:
            if self.input_device_index is None:
                print(
                    f"[MIC] Using default input device at {self.input_rate} Hz. "
                    f"(Whisper target {self.TARGET_RATE} Hz)",
                )
                return

            info = self.audio_interface.get_device_info_by_index(self.input_device_index)
            name = str(info.get("name", f"#{self.input_device_index}"))
            print(
                f"[MIC] Using '{name}' (index {self.input_device_index}) at "
                f"{self.input_rate} Hz. (Whisper target {self.TARGET_RATE} Hz)",
            )
        except Exception:
            pass

    @staticmethod
    def _resample_audio(audio, input_rate, target_rate):
        if input_rate == target_rate or audio.size == 0:
            return audio
        target_len = int(round(audio.size * (float(target_rate) / float(input_rate))))
        if target_len <= 1:
            return audio
        old_positions = np.linspace(0.0, 1.0, num=audio.size, dtype=np.float32)
        new_positions = np.linspace(0.0, 1.0, num=target_len, dtype=np.float32)
        return np.interp(new_positions, old_positions, audio).astype(np.float32)

    def _wait_for_release_with_grace(self, checker, record_start):
        release_started_at = None
        while True:
            elapsed = time.time() - record_start
            if elapsed >= self.MAX_RECORD_SECONDS:
                break

            pressed = checker()
            if pressed:
                release_started_at = None
            else:
                if elapsed < self.MIN_RECORD_SECONDS:
                    time.sleep(0.01)
                    continue
                if release_started_at is None:
                    release_started_at = time.time()
                elif time.time() - release_started_at >= self.RELEASE_GRACE_SECONDS:
                    break
            time.sleep(0.01)

    def _trim_silence(self, audio):
        if audio.size == 0:
            return audio

        abs_audio = np.abs(audio)
        keep_padding = int(self.KEEP_SILENCE_SECONDS * self.TARGET_RATE)

        leading_indices = np.where(abs_audio >= self.LEADING_SILENCE_THRESHOLD)[0]
        if leading_indices.size:
            start = max(0, int(leading_indices[0]) - keep_padding)
        else:
            start = 0

        trailing_indices = np.where(abs_audio >= self.TRAILING_SILENCE_THRESHOLD)[0]
        if trailing_indices.size:
            end = min(audio.size, int(trailing_indices[-1]) + keep_padding)
        else:
            end = audio.size

        if end <= start:
            return audio
        return audio[start:end]

    def _finalize_audio(self, audio):
        if audio.size == 0:
            return None

        audio = self._trim_silence(audio)
        duration_seconds = float(audio.size) / float(self.TARGET_RATE)
        rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float32))) if audio.size else 0.0

        if rms <= self.ZERO_RMS_EPSILON:
            self._zero_rms_streak += 1
        else:
            self._zero_rms_streak = 0

        if self._zero_rms_streak >= self.MAX_ZERO_RMS_RETRIES:
            switched = self._switch_to_next_candidate()
            self._zero_rms_streak = 0
            if switched:
                print("[MIC] Switched input device after repeated zero-audio captures.")
                time.sleep(self.RETRY_DELAY_SECONDS)
                return None

        if duration_seconds < self.MIN_RECORD_SECONDS:
            print(f"[MIC] Recording was too short ({duration_seconds:.2f}s). Hold SPACE a bit longer.")
            time.sleep(0.1)
            return None

        if rms < self.MIN_RMS:
            print(
                f"[MIC] Input was too quiet (RMS={rms:.5f}). "
                "Move closer to the mic or increase input volume.",
            )
            time.sleep(0.1)
            return None
        return audio

    def _default_space_pressed(self):
        if keyboard is None:
            return False
        try:
            return keyboard.is_pressed("space")
        except Exception:
            return False

    def listen_while_spacebar_held(self, is_pressed_fn=None):
        if self.capture_backend == "pipewire":
            return self._listen_with_pipewire(is_pressed_fn=is_pressed_fn)
        return self._listen_with_pyaudio(is_pressed_fn=is_pressed_fn)

    def _listen_with_pipewire(self, is_pressed_fn=None):
        checker = is_pressed_fn or self._default_space_pressed
        wait_start = time.time()
        while not checker():
            if time.time() - wait_start > self.MAX_WAIT_SECONDS:
                return None
            time.sleep(0.01)

        wav_path = None
        process = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as temp_wav:
                wav_path = temp_wav.name

            process = subprocess.Popen(
                [
                    self._pw_record_path,
                    "--rate",
                    str(self.TARGET_RATE),
                    "--channels",
                    str(self.CHANNELS),
                    "--format",
                    "s16",
                    wav_path,
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )

            record_start = time.time()
            self._wait_for_release_with_grace(checker, record_start)
        except Exception as exc:
            print(f"[MIC ERROR] {exc}")
            return None
        finally:
            if process is not None:
                try:
                    process.terminate()
                    process.wait(timeout=2)
                except Exception:
                    try:
                        process.kill()
                    except Exception:
                        pass

        try:
            if not wav_path or not os.path.exists(wav_path) or os.path.getsize(wav_path) <= 44:
                return None
            return self._load_wav_audio(wav_path)
        except Exception as exc:
            print(f"[MIC ERROR] {exc}")
            return None
        finally:
            if wav_path and os.path.exists(wav_path):
                try:
                    os.remove(wav_path)
                except Exception:
                    pass

    def _load_wav_audio(self, wav_path):
        with wave.open(wav_path, "rb") as wav_file:
            sample_width = wav_file.getsampwidth()
            channels = wav_file.getnchannels()
            sample_rate = wav_file.getframerate()
            frame_count = wav_file.getnframes()
            raw_audio = wav_file.readframes(frame_count)

        if sample_width != 2:
            raise ValueError(f"Unsupported sample width from PipeWire capture: {sample_width}")

        audio = np.frombuffer(raw_audio, dtype=np.int16).astype(np.float32) / 32768.0
        if channels > 1:
            audio = audio.reshape(-1, channels).mean(axis=1)
        audio = self._resample_audio(audio, sample_rate, self.TARGET_RATE)
        return self._finalize_audio(audio)

    def _listen_with_pyaudio(self, is_pressed_fn=None):
        checker = is_pressed_fn or self._default_space_pressed
        frames = []
        supports_overflow_kwarg = True

        stream = None
        try:
            open_kwargs = dict(
                format=self.FORMAT,
                channels=self.CHANNELS,
                rate=self.input_rate,
                input=True,
                frames_per_buffer=self.CHUNK,
            )
            if self.input_device_index is not None:
                open_kwargs["input_device_index"] = self.input_device_index
            stream = self.audio_interface.open(**open_kwargs)
        except Exception as exc:
            # Fallback: let PortAudio pick the default input device automatically.
            if self.input_device_index is not None:
                try:
                    stream = self.audio_interface.open(
                        format=self.FORMAT,
                        channels=self.CHANNELS,
                        rate=self.input_rate,
                        input=True,
                        frames_per_buffer=self.CHUNK,
                    )
                except Exception:
                    print(f"[MIC ERROR] {exc}")
                    time.sleep(self.RETRY_DELAY_SECONDS)
                    return None
            else:
                print(f"[MIC ERROR] {exc}")
                time.sleep(self.RETRY_DELAY_SECONDS)
                return None

        try:
            wait_start = time.time()
            while not checker():
                if time.time() - wait_start > self.MAX_WAIT_SECONDS:
                    return None
                time.sleep(0.01)

            record_start = time.time()
            while True:
                if supports_overflow_kwarg:
                    try:
                        data = stream.read(self.CHUNK, exception_on_overflow=False)
                    except TypeError:
                        supports_overflow_kwarg = False
                        data = stream.read(self.CHUNK)
                else:
                    data = stream.read(self.CHUNK)
                frames.append(np.frombuffer(data, dtype=np.int16))
                if time.time() - record_start >= self.MAX_RECORD_SECONDS:
                    break
                if not checker() and time.time() - record_start >= self.MIN_RECORD_SECONDS:
                    release_check_started = time.time()
                    while time.time() - release_check_started < self.RELEASE_GRACE_SECONDS:
                        if checker():
                            break
                        time.sleep(0.01)
                    else:
                        break

            if not frames:
                return None

            audio = np.concatenate(frames).astype(np.float32) / 32768.0
            audio = self._resample_audio(audio, self.input_rate, self.TARGET_RATE)
            return self._finalize_audio(audio)
        except Exception as exc:
            print(f"[MIC ERROR] {exc}")
            return None
        finally:
            try:
                stream.stop_stream()
                stream.close()
            except Exception:
                pass

    def close(self):
        try:
            self.audio_interface.terminate()
        except Exception:
            pass
        if self._alsa_error_handler is not None:
            try:
                import ctypes

                asound = ctypes.cdll.LoadLibrary("libasound.so")
                asound.snd_lib_error_set_handler(None)
            except Exception:
                pass


_RECORDER = None


def get_recorder():
    global _RECORDER
    if _RECORDER is None:
        _RECORDER = PushToTalkRecorder()
    return _RECORDER


def listen_while_spacebar_held(is_pressed_fn=None):
    recorder = get_recorder()
    return recorder.listen_while_spacebar_held(is_pressed_fn=is_pressed_fn)
