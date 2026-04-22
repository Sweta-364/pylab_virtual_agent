import threading

import numpy as np

try:
    import whisper
except Exception:
    whisper = None


_LANGUAGE_NAMES = {
    "en": "English",
    "hi": "Hindi",
    "es": "Spanish",
    "fr": "French",
    "de": "German",
    "it": "Italian",
    "pt": "Portuguese",
    "ru": "Russian",
    "ja": "Japanese",
    "zh": "Chinese",
    "ar": "Arabic",
    "ta": "Tamil",
    "te": "Telugu",
    "ml": "Malayalam",
    "bn": "Bengali",
    "mr": "Marathi",
    "gu": "Gujarati",
    "kn": "Kannada",
    "pa": "Punjabi",
}

_MODEL_LOCK = threading.Lock()
_TRANSCRIBER = None


class WhisperTranscriber:
    MIN_AUDIO_SECONDS = 0.45
    MIN_RMS = 0.002

    def __init__(self, model_size="base"):
        self.model_size = model_size
        self.model = self._load_model()

    def _load_model(self):
        if whisper is None:
            raise RuntimeError("openai-whisper is not installed.")
        return whisper.load_model(self.model_size)

    def _normalize_audio(self, audio_data):
        if isinstance(audio_data, np.ndarray):
            audio = audio_data.astype(np.float32)
            if audio.size == 0:
                raise ValueError("No audio samples received.")
            peak = float(np.max(np.abs(audio))) if audio.size else 0.0
            if peak > 1.0:
                audio = audio / 32768.0
            return audio

        return whisper.load_audio(audio_data)

    def _detect_language(self, audio):
        try:
            mel = whisper.log_mel_spectrogram(whisper.pad_or_trim(audio)).to(self.model.device)
            _, probs = self.model.detect_language(mel)
            lang_code = max(probs, key=probs.get)
            confidence = float(probs.get(lang_code, 0.0))
            return lang_code, confidence
        except Exception:
            return "unknown", 0.0

    def transcribe_with_translation(self, audio_data):
        try:
            audio = self._normalize_audio(audio_data)
            duration_seconds = float(audio.size) / 16000.0
            if duration_seconds < self.MIN_AUDIO_SECONDS:
                raise ValueError(
                    f"Recorded audio is too short ({duration_seconds:.2f}s). "
                    "Hold SPACE a bit longer while speaking.",
                )

            rms = float(np.sqrt(np.mean(np.square(audio), dtype=np.float32)))
            if rms < self.MIN_RMS:
                raise ValueError(
                    f"Recorded audio level is too low (RMS={rms:.5f}). "
                    "Increase mic input volume or speak closer to the mic.",
                )

            lang_code, lang_confidence = self._detect_language(audio)

            result = self.model.transcribe(
                audio,
                task="translate",
                language=None,
                fp16=False,
                temperature=0.0,
                condition_on_previous_text=False,
                no_speech_threshold=0.6,
            )

            text = str(result.get("text", "")).strip()
            if not text:
                # Fallback to standard transcription when translation yields empty output.
                result = self.model.transcribe(
                    audio,
                    task="transcribe",
                    language=None,
                    fp16=False,
                    temperature=0.0,
                    condition_on_previous_text=False,
                    no_speech_threshold=0.6,
                )
                text = str(result.get("text", "")).strip()
                if not text:
                    raise ValueError("Whisper returned empty text.")

            language_name = _LANGUAGE_NAMES.get(lang_code, "Unknown Language")
            return {
                "text": text,
                "language": lang_code,
                "language_name": language_name,
                "confidence": lang_confidence,
                "success": True,
                "retry_voice_prompt": False,
            }
        except Exception as exc:
            message = str(exc)
            lower_message = message.lower()
            retry_voice_prompt = not any(
                marker in lower_message
                for marker in (
                    "too short",
                    "too low",
                    "no audio samples",
                    "empty text",
                )
            )
            print(f"[WHISPER ERROR] {exc}")
            return {
                "text": "",
                "language": "unknown",
                "language_name": "Unknown",
                "confidence": 0.0,
                "success": False,
                "error": message,
                "retry_voice_prompt": retry_voice_prompt,
            }


def get_transcriber():
    global _TRANSCRIBER
    if _TRANSCRIBER is None:
        with _MODEL_LOCK:
            if _TRANSCRIBER is None:
                _TRANSCRIBER = WhisperTranscriber(model_size="base")
    return _TRANSCRIBER


def transcribe_with_translation(audio_data):
    transcriber = get_transcriber()
    return transcriber.transcribe_with_translation(audio_data)
