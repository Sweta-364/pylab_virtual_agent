import datetime
import os
import platform
import subprocess
import webbrowser

import conversation_manager
import ollama_handler
import speak
import weather


def _speak_and_return(message):
    response = str(message)
    conversation_manager.add_assistant_message(response)
    speak.speak(response)
    return response


def Action(send):
    user_text = str(send or "").strip()
    if not user_text:
        return _speak_and_return("Please say something so I can help you.")

    history_before_message = conversation_manager.get_history()
    conversation_manager.add_user_message(user_text)
    data_btn = user_text.lower()

    if "what is your name" in data_btn:
        return _speak_and_return("my name is virtual Assistant")

    elif "hello" in data_btn or "hye" in data_btn or "hay" in data_btn:
        return _speak_and_return("Hey sir, How i can  help you !")

    elif "how are you" in data_btn:
        return _speak_and_return("I am doing great these days sir")

    elif "thanku" in data_btn or "thank" in data_btn:
        return _speak_and_return("its my pleasure sir to stay with you")

    elif "good morning" in data_btn:
        return _speak_and_return("Good morning sir, i think you might need some help")

    elif "time now" in data_btn:
        current_time = datetime.datetime.now()
        time_text = f"{current_time.hour} Hour : {current_time.minute} Minute"
        return _speak_and_return(time_text)

    elif "shutdown" in data_btn or "quit" in data_btn:
        speak.speak("ok sir")
        conversation_manager.reset_history()
        return "ok sir"

    elif "play music" in data_btn or "song" in data_btn:
        webbrowser.open("https://gaana.com/")
        return _speak_and_return("gaana.com is now ready for you, enjoy your music")

    elif "open google" in data_btn or "google" in data_btn:
        url = "https://google.com/"
        webbrowser.get().open(url)
        return _speak_and_return("google open")

    elif "youtube" in data_btn or "open youtube" in data_btn:
        url = "https://youtube.com/"
        webbrowser.get().open(url)
        return _speak_and_return("YouTube open")

    elif "weather" in data_btn:
        return _speak_and_return(weather.Weather())

    elif "music from my laptop" in data_btn:
        url = os.path.expanduser("~/Music")
        if not os.path.isdir(url):
            return _speak_and_return("Music folder not found")

        songs = [f for f in os.listdir(url) if os.path.isfile(os.path.join(url, f))]
        if not songs:
            return _speak_and_return("No songs found in your Music folder")

        song_path = os.path.join(url, songs[0])
        try:
            if platform.system() == "Windows":
                os.startfile(song_path)
            elif platform.system() == "Darwin":
                subprocess.run(["open", song_path], check=False)
            else:
                subprocess.run(["xdg-open", song_path], check=False)
            return _speak_and_return("songs playing...")
        except Exception:
            return _speak_and_return("Unable to open music file")

    else:
        try:
            ollama_response = ollama_handler.query_ollama(
                user_text,
                history_before_message,
            )
            return _speak_and_return(ollama_response)
        except Exception as exc:
            if ollama_handler.should_log_failures():
                print(f"[OLLAMA] {exc}")

        return _speak_and_return("I'm not able to understand that. Please try a specific command or rephrase.")

