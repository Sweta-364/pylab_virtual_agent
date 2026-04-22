import os
import threading
from tkinter import *

from PIL import Image, ImageTk

import action
import conversation_flow


manual_request_lock = threading.Lock()
manual_request_thread = None
conversation_thread = None
conversation_thread_lock = threading.Lock()


def User_send():
    send = entry1.get() or ""
    send = send.strip()
    if not send:
        _append_text("[Flow] Please type something first.")
        return

    if _is_manual_request_running():
        _append_text("[Flow] Please wait for the current text request to finish.")
        return

    entry1.delete(0, END)
    _append_text("Me --> " + send)
    _start_manual_request(send)


def _append_text(line):
    text.insert(END, line + "\n")
    text.see(END)


def _append_text_from_thread(line):
    try:
        root.after(0, _append_text, line)
    except Exception:
        pass


def _conversation_status(message):
    _append_text_from_thread("[Flow] " + str(message))


def _conversation_user(message):
    _append_text_from_thread("Me --> " + str(message))


def _conversation_bot(message):
    _append_text_from_thread("Bot <-- " + str(message))
    if str(message).strip().lower() == "ok sir":
        root.after(0, _shutdown_and_close)


def _is_conversation_running():
    global conversation_thread
    return conversation_thread is not None and conversation_thread.is_alive()


def _is_manual_request_running():
    global manual_request_thread
    return manual_request_thread is not None and manual_request_thread.is_alive()


def _set_manual_controls_busy(is_busy):
    state = DISABLED if is_busy else NORMAL
    try:
        entry1.config(state=state)
        button2.config(state=state)
    except Exception:
        pass


def _finish_manual_request():
    _set_manual_controls_busy(False)
    try:
        entry1.focus_set()
    except Exception:
        pass


def _manual_request_worker(user_text):
    manager = conversation_flow.get_conversation_manager()
    manager.begin_manual_turn()
    try:
        _conversation_status("Processing typed input...")
        bot = action.Action(user_text)
        if bot is not None:
            _append_text_from_thread("Bot <-- " + str(bot))
        if bot == "ok sir":
            root.after(0, _shutdown_and_close)
    finally:
        manager.end_manual_turn()
        _conversation_status("Ready for voice or text input.")
        root.after(0, _finish_manual_request)


def _start_manual_request(user_text):
    global manual_request_thread
    with manual_request_lock:
        _set_manual_controls_busy(True)
        manual_request_thread = threading.Thread(
            target=_manual_request_worker,
            args=(user_text,),
            daemon=True,
        )
        manual_request_thread.start()


def ask_with_speech():
    global conversation_thread
    with conversation_thread_lock:
        if _is_manual_request_running():
            _append_text("[Flow] Finishing the current text request first.")
            return
        if _is_conversation_running():
            _append_text("[Flow] Conversation mode is already running.")
            return

        manager = conversation_flow.get_conversation_manager()
        manager.set_space_pressed(False)
        conversation_thread = threading.Thread(
            target=conversation_flow.start_conversation,
            kwargs={
                "on_user_text": _conversation_user,
                "on_bot_text": _conversation_bot,
                "on_status": _conversation_status,
            },
            daemon=True,
        )
        conversation_thread.start()


def delete_text():
    text.delete("1.0", "end")


root = Tk()
root.geometry("550x675")
root.title("AI Assistant")
root.resizable(False, False)
root.config(bg="#6F8FAF")


# Main Frame
Main_frame = LabelFrame(root, padx=100, pady=7, borderwidth=3, relief="raised")
Main_frame.config(bg="#6F8FAF")
Main_frame.grid(row=0, column=1, padx=55, pady=10)

# Text Label
Text_lable = Label(Main_frame, text="AI Assistant", font=("comic Sans ms", 14, "bold"), bg="#356696")
Text_lable.grid(row=0, column=0, padx=20, pady=10)

# Image
image_candidates = ["image/assistant.jpg", "image/assitant.png"]
display_image = None
for image_path in image_candidates:
    if os.path.exists(image_path):
        display_image = ImageTk.PhotoImage(Image.open(image_path).resize((200, 200)))
        break

if display_image is not None:
    Image_Lable = Label(Main_frame, image=display_image)
else:
    Image_Lable = Label(Main_frame, text="Assistant", font=("comic Sans ms", 18, "bold"), bg="#6F8FAF")
Image_Lable.grid(row=1, column=0, pady=20)

# Add a text widget
text = Text(root, font=("Courier 10 bold"), bg="#356696")
text.grid(row=2, column=0)
text.place(x=100, y=375, width=375, height=100)

# Add an entry widget
entry1 = Entry(root, justify=CENTER)
entry1.place(x=100, y=500, width=350, height=30)
entry1.bind("<Return>", lambda _event: User_send())

# Add buttons
button1 = Button(
    root,
    text="ASK (Spacebar)",
    bg="#356696",
    pady=16,
    padx=40,
    borderwidth=3,
    relief=SOLID,
    command=ask_with_speech,
)
button1.place(x=70, y=575)

button2 = Button(root, text="Send", bg="#356696", pady=16, padx=40, borderwidth=3, relief=SOLID, command=User_send)
button2.place(x=400, y=575)

button3 = Button(root, text="Delete", bg="#356696", pady=16, padx=40, borderwidth=3, relief=SOLID, command=delete_text)
button3.place(x=225, y=575)


def _on_space_press(_event):
    if _is_conversation_running():
        conversation_flow.get_conversation_manager().set_space_pressed(True)
        return "break"
    return None


def _on_space_release(_event):
    if _is_conversation_running():
        conversation_flow.get_conversation_manager().set_space_pressed(False)
        return "break"
    return None


def _shutdown_and_close():
    try:
        conversation_flow.get_conversation_manager().stop()
    except Exception:
        pass
    root.destroy()


root.bind("<KeyPress-space>", _on_space_press)
root.bind("<KeyRelease-space>", _on_space_release)
root.protocol("WM_DELETE_WINDOW", _shutdown_and_close)

root.mainloop()
