import threading
import time

import action
import push_to_talk
import speak
import whisper_handler


class ConversationManager:
    def __init__(self):
        self._conversation_stop = threading.Event()
        self._speak_stop = threading.Event()
        self._manual_turn_active = threading.Event()
        self._space_state_lock = threading.Lock()
        self._space_pressed = False
        self._last_retry_prompt_at = 0.0

    def set_space_pressed(self, pressed):
        with self._space_state_lock:
            self._space_pressed = bool(pressed)

    def is_space_pressed(self):
        with self._space_state_lock:
            return self._space_pressed

    def stop(self):
        self._conversation_stop.set()
        self._speak_stop.set()
        self._manual_turn_active.clear()

    def begin_manual_turn(self):
        self._manual_turn_active.set()
        self._speak_stop.set()

    def end_manual_turn(self):
        self._manual_turn_active.clear()

    def is_manual_turn_active(self):
        return self._manual_turn_active.is_set()

    def start_conversation(self, on_user_text=None, on_bot_text=None, on_status=None):
        self._conversation_stop.clear()
        turn = 0
        self._notify(on_status, "Conversation mode active. Hold SPACE to talk.")

        while not self._conversation_stop.is_set():
            if self.is_manual_turn_active():
                self._notify(on_status, "Manual input active...")
                while self.is_manual_turn_active() and not self._conversation_stop.is_set():
                    time.sleep(0.05)
                if self._conversation_stop.is_set():
                    break

            turn += 1
            self._notify(on_status, f"[Turn {turn}] Waiting for SPACE...")
            audio = push_to_talk.listen_while_spacebar_held(
                is_pressed_fn=self.is_space_pressed,
            )
            if audio is None:
                if self._conversation_stop.is_set():
                    break
                continue

            self._notify(on_status, "Transcribing with Whisper...")
            transcription = whisper_handler.transcribe_with_translation(audio)
            if not transcription.get("success"):
                error = str(transcription.get("error", "")).strip()
                if error:
                    self._notify(on_status, error)
                else:
                    self._notify(on_status, "I could not understand that. Try again.")

                should_speak_retry = bool(transcription.get("retry_voice_prompt", False))
                now = time.time()
                if should_speak_retry and now - self._last_retry_prompt_at > 4.0:
                    self._last_retry_prompt_at = now
                    speak.speak("Sorry, I did not catch that. Please try again.")
                continue

            user_text = transcription.get("text", "").strip()
            if not user_text:
                continue
            self._notify(
                on_status,
                "Detected "
                f"{transcription.get('language_name', 'Unknown')} "
                f"({transcription.get('confidence', 0.0):.0%}).",
            )
            self._notify(on_user_text, user_text)

            self._notify(on_status, "Generating response...")
            bot_text = action.Action(user_text, speak_response=False)
            if bot_text is None:
                continue
            bot_text = str(bot_text)
            self._notify(on_bot_text, bot_text)

            if bot_text.strip().lower() == "ok sir":
                self.stop()
                break

            interrupted = threading.Event()
            self._speak_stop.clear()

            speak_thread = threading.Thread(
                target=self._speak_worker,
                args=(bot_text,),
                daemon=True,
            )
            interrupt_thread = threading.Thread(
                target=self._listen_for_interruption,
                args=(interrupted,),
                daemon=True,
            )

            self._notify(on_status, "Speaking... press SPACE to interrupt.")
            speak_thread.start()
            interrupt_thread.start()
            speak_thread.join()
            self._speak_stop.set()
            interrupt_thread.join()

            if interrupted.is_set():
                self._notify(on_status, "Interrupted. Listening for next input...")
            else:
                self._notify(on_status, "Response finished.")

        self._notify(on_status, "Conversation mode stopped.")

    def _speak_worker(self, text):
        try:
            speak.speak(text, stop_event=self._speak_stop)
        finally:
            self._speak_stop.set()

    def _listen_for_interruption(self, interrupted):
        while not self._speak_stop.is_set() and not self._conversation_stop.is_set():
            if self.is_space_pressed() or self.is_manual_turn_active():
                interrupted.set()
                self._speak_stop.set()
                break
            time.sleep(0.03)

    @staticmethod
    def _notify(callback, message):
        if callback is None:
            print(f"[FLOW] {message}")
            return
        try:
            callback(message)
        except Exception:
            pass


_CONVERSATION_MANAGER = None
_MANAGER_LOCK = threading.Lock()


def get_conversation_manager():
    global _CONVERSATION_MANAGER
    if _CONVERSATION_MANAGER is None:
        with _MANAGER_LOCK:
            if _CONVERSATION_MANAGER is None:
                _CONVERSATION_MANAGER = ConversationManager()
    return _CONVERSATION_MANAGER


def start_conversation(on_user_text=None, on_bot_text=None, on_status=None):
    manager = get_conversation_manager()
    manager.start_conversation(
        on_user_text=on_user_text,
        on_bot_text=on_bot_text,
        on_status=on_status,
    )
