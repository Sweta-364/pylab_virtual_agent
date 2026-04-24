import threading
import time

import action
import conversation_manager
import push_to_talk
import speak
import whisper_handler

try:
    import keyboard
except Exception:
    keyboard = None


class ConversationManager:
    def __init__(self):
        self._conversation_stop = threading.Event()
        self._speak_stop = threading.Event()
        self._speech_interrupted = threading.Event()
        self._manual_turn_active = threading.Event()
        self._space_state_lock = threading.Lock()
        self._space_pressed = False
        self._last_retry_prompt_at = 0.0
        self._keyboard_hooks = []
        self._space_listener_available = False

    def set_space_pressed(self, pressed):
        with self._space_state_lock:
            self._space_pressed = bool(pressed)

    def is_space_pressed(self):
        with self._space_state_lock:
            return self._space_pressed

    def stop(self):
        self._conversation_stop.set()
        self._speak_stop.set()
        self._speech_interrupted.set()
        self._manual_turn_active.clear()
        self._teardown_space_listener()

    def begin_manual_turn(self):
        self._manual_turn_active.set()
        self._speech_interrupted.set()
        self._speak_stop.set()

    def end_manual_turn(self):
        self._manual_turn_active.clear()

    def is_manual_turn_active(self):
        return self._manual_turn_active.is_set()

    def _handle_space_press(self, _event=None):
        self.set_space_pressed(True)
        if not self._speak_stop.is_set():
            self._speech_interrupted.set()
            self._speak_stop.set()

    def _handle_space_release(self, _event=None):
        self.set_space_pressed(False)

    def _ensure_space_listener(self):
        if self._space_listener_available or keyboard is None:
            return self._space_listener_available
        try:
            self._keyboard_hooks = [
                keyboard.on_press_key("space", self._handle_space_press),
                keyboard.on_release_key("space", self._handle_space_release),
            ]
            self._space_listener_available = True
        except Exception:
            self._keyboard_hooks = []
            self._space_listener_available = False
        return self._space_listener_available

    def _teardown_space_listener(self):
        if not self._keyboard_hooks or keyboard is None:
            return
        for hook in self._keyboard_hooks:
            try:
                keyboard.unhook(hook)
            except Exception:
                pass
        self._keyboard_hooks = []
        self._space_listener_available = False

    @staticmethod
    def _resolve_pending_action_text(bot):
        operation_id = getattr(bot, "operation_id", None)
        if not operation_id:
            return str(bot)

        record = conversation_manager.wait_for_pending_operation(
            operation_id,
            timeout_seconds=2.0,
            poll_interval=0.1,
        )
        if not record:
            return str(bot)

        if record.get("type") == "image" and record.get("status") == "success":
            display_path = record.get("display_path") or record.get("result")
            return f"{str(bot)}\nImage saved to `{display_path}`"

        if record.get("type") == "image" and record.get("status") == "failed":
            error = record.get("error") or "Unknown handwriting error."
            return f"{str(bot)}\nHandwriting generation failed gracefully: {error}"

        return str(bot)

    def start_conversation(self, on_user_text=None, on_bot_text=None, on_status=None):
        self._conversation_stop.clear()
        self._speech_interrupted.clear()
        self._ensure_space_listener()
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

            audio_quality = push_to_talk.get_audio_quality(audio)
            if not audio_quality.get("is_valid", False):
                self._notify(
                    on_status,
                    audio_quality.get("message", "Audio too quiet or too short. Please try again."),
                )
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
                    speak.speak("Sorry, I did not catch that. Please try again.", fade_out_duration=0.5)
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
            bot_text = action.Action(
                user_text,
                speak_response=False,
                status_callback=lambda message: self._notify(on_status, message),
            )
            if bot_text is None:
                continue
            no_speech_output = bool(getattr(bot_text, "no_speech", False))
            bot_text = self._resolve_pending_action_text(bot_text)
            self._notify(on_bot_text, bot_text)

            if bot_text.strip().lower() == "ok sir":
                self.stop()
                break

            if no_speech_output:
                self._notify(on_status, "Text-only response ready.")
                continue

            interrupted = threading.Event()
            self._speak_stop.clear()
            self._speech_interrupted.clear()

            speak_thread = threading.Thread(
                target=self._speak_worker,
                args=(bot_text,),
                daemon=True,
            )
            interrupt_thread = None
            if not self._space_listener_available:
                interrupt_thread = threading.Thread(
                    target=self._listen_for_interruption,
                    args=(interrupted,),
                    daemon=True,
                )

            self._notify(on_status, "Speaking... press SPACE to interrupt.")
            speak_thread.start()
            if interrupt_thread is not None:
                interrupt_thread.start()
            speak_thread.join()
            self._speak_stop.set()
            if interrupt_thread is not None:
                interrupt_thread.join()
            if self._speech_interrupted.is_set():
                interrupted.set()

            if interrupted.is_set():
                self._notify(on_status, "Interrupted")
                time.sleep(1.0)
                self._notify(on_status, "Listening for next input...")
            else:
                self._notify(on_status, "Response finished.")

        self._notify(on_status, "Conversation mode stopped.")

    def _speak_worker(self, text):
        try:
            speak.speak(text, stop_event=self._speak_stop, fade_out_duration=0.5)
        finally:
            self._speak_stop.set()

    def _listen_for_interruption(self, interrupted):
        while not self._speak_stop.is_set() and not self._conversation_stop.is_set():
            if self.is_space_pressed() or self.is_manual_turn_active():
                interrupted.set()
                self._speech_interrupted.set()
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
