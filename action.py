import datetime
import os
import platform
import subprocess
import threading
import webbrowser

import conversation_manager
import file_handler
import image_handler
import ollama_handler
import speak
import weather


class ActionResult(str):
    def __new__(cls, value, no_speech=False):
        obj = str.__new__(cls, value)
        obj.no_speech = bool(no_speech)
        return obj


def _speak_and_return(
    message,
    should_speak=True,
    stop_event=None,
    record_history=True,
    no_speech_output=False,
):
    response = str(message)
    if record_history:
        conversation_manager.add_assistant_message(response)
    if should_speak:
        speak.speak(response, stop_event=stop_event)
    return ActionResult(response, no_speech=no_speech_output)


def _generate_handwritten_image_async(response_text):
    def _worker():
        try:
            image_path = image_handler.convert_text_to_handwritten_image(response_text)
            if ollama_handler.should_log_failures():
                print(f"[HANDWRITING] Saved: {image_path}")
        except Exception as exc:
            if ollama_handler.should_log_failures():
                print(f"[HANDWRITING] {exc}")

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()


def _query_ollama_and_handle(user_text, history_before_message, is_createfile_command):
    ollama_response = ollama_handler.query_ollama(
        user_text,
        history_before_message,
    )

    if is_createfile_command:
        _generate_handwritten_image_async(ollama_response)

    return ollama_response


def _handle_file_operations(user_text, stop_event=None):
    confirmation_response = file_handler.handle_pending_confirmation(user_text)
    if confirmation_response is not None:
        return _speak_and_return(
            confirmation_response,
            should_speak=False,
            stop_event=stop_event,
            record_history=False,
            no_speech_output=True,
        )

    parsed_command = file_handler.parse_natural_language_command(user_text)
    if not parsed_command:
        return None

    try:
        result = file_handler.execute_parsed_command(parsed_command)
    except Exception as exc:
        result = str(exc)

    return _speak_and_return(
        result,
        should_speak=False,
        stop_event=stop_event,
        record_history=False,
        no_speech_output=True,
    )


def Action(send, speak_response=True, stop_event=None):
    user_text = str(send or "").strip()
    if not user_text:
        return _speak_and_return(
            "Please say something so I can help you.",
            should_speak=speak_response,
            stop_event=stop_event,
        )

    data_btn = user_text.lower()
    is_createfile_command = "createfile" in data_btn
    file_response = _handle_file_operations(user_text, stop_event=stop_event)
    if file_response is not None:
        return file_response

    history_before_message = conversation_manager.get_history()
    conversation_manager.add_user_message(user_text)

    if "what is your name" in data_btn:
        return _speak_and_return(
            "my name is virtual Assistant",
            should_speak=speak_response,
            stop_event=stop_event,
        )

    elif data_btn in {"hello", "hi", "hye", "hay", "hey"}:
        return _speak_and_return(
            "Hey sir, How i can  help you !",
            should_speak=speak_response,
            stop_event=stop_event,
        )

    elif "how are you" in data_btn:
        return _speak_and_return(
            "I am doing great these days sir",
            should_speak=speak_response,
            stop_event=stop_event,
        )

    elif "thanku" in data_btn or "thank" in data_btn:
        return _speak_and_return(
            "its my pleasure sir to stay with you",
            should_speak=speak_response,
            stop_event=stop_event,
        )

    elif "good morning" in data_btn:
        return _speak_and_return(
            "Good morning sir, i think you might need some help",
            should_speak=speak_response,
            stop_event=stop_event,
        )

    elif "time now" in data_btn:
        current_time = datetime.datetime.now()
        time_text = f"{current_time.hour} Hour : {current_time.minute} Minute"
        return _speak_and_return(
            time_text,
            should_speak=speak_response,
            stop_event=stop_event,
        )

    elif "shutdown" in data_btn or "quit" in data_btn:
        if speak_response:
            speak.speak("ok sir", stop_event=stop_event)
        conversation_manager.reset_history()
        return "ok sir"

    elif "play music" in data_btn or "song" in data_btn:
        webbrowser.open("https://gaana.com/")
        return _speak_and_return(
            "gaana.com is now ready for you, enjoy your music",
            should_speak=speak_response,
            stop_event=stop_event,
        )

    elif "open google" in data_btn or "google" in data_btn:
        url = "https://google.com/"
        webbrowser.get().open(url)
        return _speak_and_return(
            "google open",
            should_speak=speak_response,
            stop_event=stop_event,
        )

    elif "youtube" in data_btn or "open youtube" in data_btn:
        url = "https://youtube.com/"
        webbrowser.get().open(url)
        return _speak_and_return(
            "YouTube open",
            should_speak=speak_response,
            stop_event=stop_event,
        )

    elif "weather" in data_btn:
        return _speak_and_return(
            weather.Weather(),
            should_speak=speak_response,
            stop_event=stop_event,
        )

    elif "music from my laptop" in data_btn:
        url = os.path.expanduser("~/Music")
        if not os.path.isdir(url):
            return _speak_and_return(
                "Music folder not found",
                should_speak=speak_response,
                stop_event=stop_event,
            )

        songs = [f for f in os.listdir(url) if os.path.isfile(os.path.join(url, f))]
        if not songs:
            return _speak_and_return(
                "No songs found in your Music folder",
                should_speak=speak_response,
                stop_event=stop_event,
            )

        song_path = os.path.join(url, songs[0])
        try:
            if platform.system() == "Windows":
                os.startfile(song_path)
            elif platform.system() == "Darwin":
                subprocess.run(["open", song_path], check=False)
            else:
                subprocess.run(["xdg-open", song_path], check=False)
            return _speak_and_return(
                "songs playing...",
                should_speak=speak_response,
                stop_event=stop_event,
            )
        except Exception:
            return _speak_and_return(
                "Unable to open music file",
                should_speak=speak_response,
                stop_event=stop_event,
            )

    else:
        try:
            ollama_response = _query_ollama_and_handle(
                user_text,
                history_before_message,
                is_createfile_command,
            )
            return _speak_and_return(
                ollama_response,
                should_speak=speak_response,
                stop_event=stop_event,
            )
        except Exception as exc:
            if ollama_handler.should_log_failures():
                print(f"[OLLAMA] {exc}")

        return _speak_and_return(
            "I'm not able to understand that. Please try a specific command or rephrase.",
            should_speak=speak_response,
            stop_event=stop_event,
        )

