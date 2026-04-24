import datetime
import hashlib
import json
import logging
import os
import platform
import re
import subprocess
import threading
import time
import webbrowser
from typing import Callable, Dict, List, Optional, Tuple

import conversation_manager
import intent_detector
import ollama_handler


_speak_module = None
_weather_module = None
_file_handler_module = None
_image_handler_module = None
_LOGGER = logging.getLogger(__name__)
_CREATEFILE_PATTERN = re.compile(r"\bcreatefile\b", re.IGNORECASE)

_RESPONSE_CACHE: Dict[str, Tuple[str, float]] = {}
_RESPONSE_CACHE_TTL_SECONDS = 600


class ActionResult(str):
    def __new__(cls, value, no_speech=False, operation_id=None):
        obj = str.__new__(cls, value)
        obj.no_speech = bool(no_speech)
        obj.operation_id = operation_id
        return obj


def _get_speak():
    global _speak_module
    if _speak_module is None:
        import speak

        _speak_module = speak
    return _speak_module


def _get_weather():
    global _weather_module
    if _weather_module is None:
        import weather

        _weather_module = weather
    return _weather_module


def _get_file_handler():
    global _file_handler_module
    if _file_handler_module is None:
        import file_handler

        _file_handler_module = file_handler
    return _file_handler_module


def _get_image_handler():
    global _image_handler_module
    if _image_handler_module is None:
        import image_handler

        _image_handler_module = image_handler
    return _image_handler_module


def _normalize_text(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def _update_status(status_callback, message: str) -> None:
    if status_callback is None:
        return
    try:
        status_callback(str(message))
    except Exception:
        pass


def _prune_response_cache() -> None:
    now = time.time()
    expired_keys = [
        key for key, (_, timestamp) in _RESPONSE_CACHE.items()
        if now - timestamp >= _RESPONSE_CACHE_TTL_SECONDS
    ]
    for key in expired_keys:
        del _RESPONSE_CACHE[key]


def _history_signature(conversation_history: List[Dict[str, str]]) -> str:
    relevant_history = conversation_history[-6:]
    payload = json.dumps(relevant_history, ensure_ascii=False, sort_keys=True)
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _get_response_cache_key(user_input: str, conversation_history: List[Dict[str, str]]) -> str:
    normalized = _normalize_text(user_input).lower()
    signature = _history_signature(conversation_history)
    return hashlib.md5(f"{normalized}|{signature}".encode("utf-8")).hexdigest()


def _get_cached_response(user_input: str, conversation_history: List[Dict[str, str]]) -> Optional[str]:
    _prune_response_cache()
    key = _get_response_cache_key(user_input, conversation_history)
    cached = _RESPONSE_CACHE.get(key)
    if not cached:
        return None
    return cached[0]


def _cache_response(user_input: str, conversation_history: List[Dict[str, str]], response: str) -> None:
    _prune_response_cache()
    key = _get_response_cache_key(user_input, conversation_history)
    _RESPONSE_CACHE[key] = (str(response), time.time())


def _speak_and_return(
    message,
    should_speak=True,
    stop_event=None,
    record_history=True,
    no_speech_output=False,
    operation_id=None,
    status_callback=None,
):
    response = str(message)
    if record_history:
        conversation_manager.add_assistant_message(response)
    if should_speak:
        _update_status(status_callback, "Speaking...")
        _get_speak().speak(response, stop_event=stop_event)
    return ActionResult(response, no_speech=no_speech_output, operation_id=operation_id)


def _generate_handwritten_image_async(response_text):
    operation_id = conversation_manager.create_pending_operation(
        "image",
        source="createfile",
        requested_text=str(response_text or ""),
    )

    def _worker():
        try:
            image_handler = _get_image_handler()
            image_path = image_handler.convert_text_to_handwritten_image(response_text)
            validation = conversation_manager.validate_generated_image(image_path)
            if not validation.get("is_valid"):
                raise RuntimeError(validation.get("error") or "Generated image failed validation.")
            relative_path = os.path.relpath(image_path, os.path.dirname(__file__))
            conversation_manager.update_pending_operation(
                operation_id,
                status="success",
                result=image_path,
                display_path=f"./{relative_path}",
            )
            _LOGGER.info("Handwriting image saved to %s", image_path)
        except Exception as exc:
            conversation_manager.update_pending_operation(
                operation_id,
                status="failed",
                error=str(exc),
            )
            _LOGGER.warning("Handwriting generation failed: %s", exc)

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    return operation_id


def _query_ollama_and_handle(user_text, history_before_message, is_createfile_command):
    cached_response = _get_cached_response(user_text, history_before_message)
    if cached_response is not None:
        operation_id = None
        if is_createfile_command:
            operation_id = _generate_handwritten_image_async(cached_response)
        return cached_response, operation_id

    ollama_response = ollama_handler.query_ollama(
        user_text,
        history_before_message,
    )
    _cache_response(user_text, history_before_message, ollama_response)

    operation_id = None
    if is_createfile_command:
        operation_id = _generate_handwritten_image_async(ollama_response)

    return ollama_response, operation_id


def _text_only_response(message, stop_event=None, operation_id=None):
    return _speak_and_return(
        message,
        should_speak=False,
        stop_event=stop_event,
        record_history=False,
        no_speech_output=True,
        operation_id=operation_id,
    )


def _execute_parsed_file_command(parsed_command, stop_event=None, status_callback=None):
    file_handler = _get_file_handler()
    operation_id = conversation_manager.create_pending_operation(
        "file",
        command=str(parsed_command.get("operation", "")),
        path=str(parsed_command.get("path", "")),
    )
    try:
        _update_status(status_callback, "Executing file operation...")
        result = file_handler.execute_parsed_command(parsed_command)
        status = "pending" if str(result).startswith("Confirm delete") else "success"
        conversation_manager.update_pending_operation(
            operation_id,
            status=status,
            result=result,
        )
    except Exception as exc:
        result = str(exc)
        conversation_manager.update_pending_operation(
            operation_id,
            status="failed",
            error=result,
        )
    return _text_only_response(result, stop_event=stop_event, operation_id=operation_id)


def _handle_file_operations(user_text, stop_event=None, allow_intent_fallback=False, status_callback=None):
    file_handler = _get_file_handler()

    confirmation_response = file_handler.handle_pending_confirmation(user_text)
    if confirmation_response is not None:
        return _text_only_response(confirmation_response, stop_event=stop_event)

    parsed_command = file_handler.parse_natural_language_command(user_text)
    if parsed_command:
        return _execute_parsed_file_command(
            parsed_command,
            stop_event=stop_event,
            status_callback=status_callback,
        )

    if not allow_intent_fallback:
        return None

    intent_result = intent_detector.detect_os_intent(user_text)
    _LOGGER.info(
        "Intent detection result: is_os_operation=%s confidence=%.2f source=%s error=%s",
        intent_result.get("is_os_operation"),
        float(intent_result.get("confidence", 0.0)),
        intent_result.get("source", "unknown"),
        intent_result.get("error"),
    )
    if not intent_result.get("is_os_operation"):
        return None
    _update_status(status_callback, "Detected OS operation...")

    interpreted_command = intent_detector.interpret_os_command(user_text)
    if interpreted_command:
        return _execute_parsed_file_command(
            interpreted_command,
            stop_event=stop_event,
            status_callback=status_callback,
        )

    return _text_only_response(
        "I recognized that as a filesystem request, but I could not interpret it safely. "
        "Please rephrase it with a clearer action and path.",
        stop_event=stop_event,
    )


def _handle_name_query():
    return "my name is virtual Assistant"


def _handle_greeting():
    return "Hey sir, How i can help you !"


def _handle_how_are_you():
    return "I am doing great these days sir"


def _handle_gratitude():
    return "its my pleasure sir to stay with you"


def _handle_good_morning():
    return "Good morning sir, i think you might need some help"


def _handle_time_query():
    current_time = datetime.datetime.now()
    return f"{current_time.hour} Hour : {current_time.minute} Minute"


def _handle_play_music():
    webbrowser.open("https://gaana.com/")
    return "gaana.com is now ready for you, enjoy your music"


def _handle_open_google():
    webbrowser.get().open("https://google.com/")
    return "google open"


def _handle_open_youtube():
    webbrowser.get().open("https://youtube.com/")
    return "YouTube open"


def _handle_weather():
    weather_module = _get_weather()
    return weather_module.Weather()


def _handle_local_music():
    music_dir = os.path.expanduser("~/Music")
    if not os.path.isdir(music_dir):
        return "Music folder not found"

    songs = [entry for entry in os.listdir(music_dir) if os.path.isfile(os.path.join(music_dir, entry))]
    if not songs:
        return "No songs found in your Music folder"

    song_path = os.path.join(music_dir, songs[0])
    try:
        if platform.system() == "Windows":
            os.startfile(song_path)
        elif platform.system() == "Darwin":
            subprocess.run(["open", song_path], check=False)
        else:
            subprocess.run(["xdg-open", song_path], check=False)
        return "songs playing..."
    except Exception:
        return "Unable to open music file"


COMMAND_REGISTRY = [
    {
        "name": "shutdown",
        "patterns": [r"\b(shutdown|quit|exit|bye)\b"],
        "priority": 100,
        "handler": None,
    },
    {
        "name": "name_query",
        "patterns": [r"what.*your.*name", r"who\s+are\s+you", r"your\s+name"],
        "priority": 95,
        "handler": _handle_name_query,
    },
    {
        "name": "greeting",
        "patterns": [r"^(hello|hi|hye|hay|hey|namaste)\b"],
        "priority": 90,
        "handler": _handle_greeting,
    },
    {
        "name": "how_are_you",
        "patterns": [r"how\s+are\s+you", r"how\s+are\s+you\s+doing"],
        "priority": 88,
        "handler": _handle_how_are_you,
    },
    {
        "name": "gratitude",
        "patterns": [r"\b(thank|thanku|thanks)\b", r"\bappreciate\b"],
        "priority": 86,
        "handler": _handle_gratitude,
    },
    {
        "name": "good_morning",
        "patterns": [r"good\s+morning"],
        "priority": 84,
        "handler": _handle_good_morning,
    },
    {
        "name": "time_query",
        "patterns": [r"time\s+now", r"current\s+time", r"what.*time"],
        "priority": 82,
        "handler": _handle_time_query,
    },
    {
        "name": "weather",
        "patterns": [r"\bweather\b", r"\bclimate\b", r"\btemperature\b"],
        "priority": 78,
        "handler": _handle_weather,
    },
    {
        "name": "music_local",
        "patterns": [r"music.*(laptop|computer|system)", r"play.*music.*(laptop|computer|system)"],
        "priority": 74,
        "handler": _handle_local_music,
    },
    {
        "name": "youtube",
        "patterns": [r"\b(open|launch)\s+youtube\b", r"\byoutube\b"],
        "priority": 70,
        "handler": _handle_open_youtube,
    },
    {
        "name": "google",
        "patterns": [r"\b(open|launch)\s+google\b", r"\bgoogle\b"],
        "priority": 68,
        "handler": _handle_open_google,
    },
    {
        "name": "play_music",
        "patterns": [r"\bplay\s+music\b", r"\bplay\s+song\b", r"\bstart\s+music\b"],
        "priority": 64,
        "handler": _handle_play_music,
    },
]


def _find_command_match(user_text: str) -> Tuple[Optional[str], Optional[Dict[str, object]]]:
    normalized = _normalize_text(user_text).lower()
    matches = []

    for command_meta in COMMAND_REGISTRY:
        for pattern in command_meta["patterns"]:
            if re.search(pattern, normalized, re.IGNORECASE):
                matches.append(
                    (
                        command_meta["name"],
                        command_meta,
                        command_meta["priority"],
                        len(pattern),
                    )
                )
                break

    if not matches:
        return None, None

    best_match = max(matches, key=lambda item: (item[2], item[3]))
    return best_match[0], best_match[1]


def _handle_registry_command(
    command_name,
    command_meta,
    user_text,
    speak_response=True,
    stop_event=None,
    status_callback=None,
):
    conversation_manager.add_user_message(user_text)

    if command_name == "shutdown":
        if speak_response:
            _get_speak().speak("ok sir", stop_event=stop_event)
        conversation_manager.reset_history()
        return ActionResult("ok sir")

    handler: Callable[[], str] = command_meta["handler"]
    response = handler() if callable(handler) else str(handler)
    return _speak_and_return(
        response,
        should_speak=speak_response,
        stop_event=stop_event,
        status_callback=status_callback,
    )


def Action(send, speak_response=True, stop_event=None, status_callback=None):
    user_text = _normalize_text(send)
    if not user_text:
        return _speak_and_return(
            "Please say something so I can help you.",
            should_speak=speak_response,
            stop_event=stop_event,
            status_callback=status_callback,
        )

    data_btn = user_text.lower()
    is_createfile_command = bool(_CREATEFILE_PATTERN.search(data_btn))
    _LOGGER.info(
        "createfile keyword match=%s input=%r",
        is_createfile_command,
        user_text,
    )

    direct_file_response = _handle_file_operations(
        user_text,
        stop_event=stop_event,
        allow_intent_fallback=False,
        status_callback=status_callback,
    )
    if direct_file_response is not None:
        return direct_file_response

    command_name, command_meta = _find_command_match(user_text)
    if command_meta is not None:
        return _handle_registry_command(
            command_name,
            command_meta,
            user_text,
            speak_response=speak_response,
            stop_event=stop_event,
            status_callback=status_callback,
        )

    semantic_file_response = _handle_file_operations(
        user_text,
        stop_event=stop_event,
        allow_intent_fallback=True,
        status_callback=status_callback,
    )
    if semantic_file_response is not None:
        return semantic_file_response

    history_before_message = conversation_manager.get_history()
    conversation_manager.add_user_message(user_text)

    try:
        _update_status(status_callback, "Generating response...")
        ollama_response, operation_id = _query_ollama_and_handle(
            user_text,
            history_before_message,
            is_createfile_command,
        )
        return _speak_and_return(
            ollama_response,
            should_speak=speak_response,
            stop_event=stop_event,
            operation_id=operation_id,
            status_callback=status_callback,
        )
    except Exception as exc:
        if ollama_handler.should_log_failures():
            print(f"[OLLAMA] {exc}")

    return _speak_and_return(
        "I'm not able to understand that. Please try a specific command or rephrase.",
        should_speak=speak_response,
        stop_event=stop_event,
        status_callback=status_callback,
    )
