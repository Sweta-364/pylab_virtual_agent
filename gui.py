import os
import threading
import tkinter as tk

from PIL import Image, ImageTk

import action
import conversation_manager
import conversation_flow


BG = "#050505"
PANEL = "#101010"
PANEL_ALT = "#161616"
ENTRY_BG = "#0D0D0D"
OUTPUT_BG = "#000000"
BORDER = "#2A2A2A"
ACCENT = "#FFFFFF"
TEXT_PRIMARY = "#FFFFFF"
TEXT_MUTED = "#A8A8A8"
SUCCESS = "#1E7A4F"
DANGER = "#7A2C2C"
BUTTON_BG = "#1B1B1B"
BUTTON_ACTIVE = "#262626"

WINDOW_TITLE = "JARVIS Assistant"
WINDOW_SIZE = "1180x820"
WINDOW_MIN_SIZE = (920, 640)


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

    entry1.delete(0, tk.END)
    _append_text("Me --> " + send)
    _start_manual_request(send)


def _append_text(line):
    text.insert(tk.END, line + "\n")
    text.see(tk.END)
    _refresh_confirmation_controls()


def _append_text_from_thread(line):
    try:
        root.after(0, _append_text, line)
    except Exception:
        pass


def _set_status(message):
    try:
        status_var.set(str(message))
    except Exception:
        pass


def _conversation_status(message):
    _set_status(message)
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
    state = tk.DISABLED if is_busy else tk.NORMAL
    try:
        entry1.config(state=state)
        send_button.config(state=state)
        confirm_yes_button.config(state=state if conversation_manager.has_pending_file_operation() else tk.DISABLED)
        confirm_no_button.config(state=state if conversation_manager.has_pending_file_operation() else tk.DISABLED)
    except Exception:
        pass


def _finish_manual_request():
    _set_manual_controls_busy(False)
    _refresh_confirmation_controls()
    _set_status("Ready for voice or text input.")
    try:
        entry1.focus_set()
    except Exception:
        pass


def _resolve_pending_action_text(bot):
    operation_id = getattr(bot, "operation_id", None)
    if not operation_id:
        return str(bot)

    record = conversation_manager.wait_for_pending_operation(operation_id, timeout_seconds=2.0, poll_interval=0.1)
    if not record:
        return str(bot)

    if record.get("type") == "image" and record.get("status") == "success":
        display_path = record.get("display_path") or record.get("result")
        return f"{str(bot)}\nImage saved to `{display_path}`"

    if record.get("type") == "image" and record.get("status") == "failed":
        error = record.get("error") or "Unknown handwriting error."
        return f"{str(bot)}\nHandwriting generation failed gracefully: {error}"

    return str(bot)


def _manual_request_worker(user_text):
    manager = conversation_flow.get_conversation_manager()
    manager.begin_manual_turn()
    try:
        _conversation_status("Processing typed input...")
        bot = action.Action(user_text, status_callback=_set_status)
        if bot is not None:
            resolved_text = _resolve_pending_action_text(bot)
            _append_text_from_thread("Bot <-- " + resolved_text)
            if bool(getattr(bot, "no_speech", False)):
                _conversation_status("Text-only response ready.")
        if bot == "ok sir":
            root.after(0, _shutdown_and_close)
    finally:
        manager.end_manual_turn()
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
        _set_status("Conversation mode active. Hold SPACE to talk.")
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
    text.delete("1.0", tk.END)
    _set_status("Output cleared.")


def _submit_quick_text(value):
    if _is_manual_request_running():
        return
    _append_text("Me --> " + value)
    _start_manual_request(value)


def _confirm_delete():
    _submit_quick_text("yes")


def _cancel_delete():
    _submit_quick_text("no")


def _refresh_confirmation_controls():
    has_pending = conversation_manager.has_pending_file_operation()
    state = tk.NORMAL if has_pending and not _is_manual_request_running() else tk.DISABLED
    banner_text = "Delete confirmation pending" if has_pending else "Text Output"
    banner_fg = "#FFDADA" if has_pending else TEXT_MUTED
    try:
        confirm_yes_button.config(state=state)
        confirm_no_button.config(state=state)
        output_banner.config(text=banner_text, fg=banner_fg)
    except Exception:
        pass


def _make_button(parent, text_value, command, bg=BUTTON_BG, fg=TEXT_PRIMARY, width=None):
    return tk.Button(
        parent,
        text=text_value,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=BUTTON_ACTIVE,
        activeforeground=TEXT_PRIMARY,
        borderwidth=1,
        relief=tk.SOLID,
        highlightthickness=0,
        padx=18,
        pady=12,
        font=("Helvetica", 11, "bold"),
        width=width,
        cursor="hand2",
    )


def _load_display_image():
    image_candidates = ["image/assistant.jpg", "image/assitant.png"]
    for image_path in image_candidates:
        if os.path.exists(image_path):
            image = Image.open(image_path).resize((260, 260), Image.LANCZOS)
            return ImageTk.PhotoImage(image)
    return None


root = tk.Tk()
root.title(WINDOW_TITLE)
root.geometry(WINDOW_SIZE)
root.minsize(*WINDOW_MIN_SIZE)
root.resizable(True, True)
root.configure(bg=BG)

root.grid_columnconfigure(0, weight=1)
root.grid_rowconfigure(0, weight=1)

shell = tk.Frame(root, bg=BG)
shell.grid(row=0, column=0, sticky="nsew")
shell.grid_columnconfigure(0, weight=1)
shell.grid_rowconfigure(1, weight=1)

header = tk.Frame(shell, bg=BG, padx=24, pady=20)
header.grid(row=0, column=0, sticky="ew")
header.grid_columnconfigure(0, weight=1)

title_label = tk.Label(
    header,
    text="JARVIS",
    bg=BG,
    fg=TEXT_PRIMARY,
    font=("Helvetica", 24, "bold"),
)
title_label.grid(row=0, column=0, sticky="w")

subtitle_label = tk.Label(
    header,
    text="Responsive voice + text assistant with filesystem output support",
    bg=BG,
    fg=TEXT_MUTED,
    font=("Helvetica", 11),
)
subtitle_label.grid(row=1, column=0, sticky="w", pady=(4, 0))

content = tk.Frame(shell, bg=BG, padx=24, pady=8)
content.grid(row=1, column=0, sticky="nsew")
content.grid_columnconfigure(0, weight=2)
content.grid_columnconfigure(1, weight=3)
content.grid_rowconfigure(0, weight=1)

left_panel = tk.Frame(content, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
left_panel.grid(row=0, column=0, sticky="nsew", padx=(0, 16))
left_panel.grid_columnconfigure(0, weight=1)

hero_label = tk.Label(
    left_panel,
    text="AI Assistant",
    bg=PANEL,
    fg=TEXT_PRIMARY,
    font=("Helvetica", 18, "bold"),
)
hero_label.grid(row=0, column=0, sticky="ew", padx=24, pady=(28, 10))

hero_subtitle = tk.Label(
    left_panel,
    text="Use voice or text naturally. Filesystem results stay text-only for readability.",
    bg=PANEL,
    fg=TEXT_MUTED,
    wraplength=300,
    justify="center",
    font=("Helvetica", 10),
)
hero_subtitle.grid(row=1, column=0, sticky="ew", padx=24)

display_image = _load_display_image()
if display_image is not None:
    image_label = tk.Label(left_panel, image=display_image, bg=PANEL, borderwidth=0, highlightthickness=0)
else:
    image_label = tk.Label(
        left_panel,
        text="JARVIS",
        bg=PANEL_ALT,
        fg=TEXT_PRIMARY,
        font=("Helvetica", 30, "bold"),
        width=12,
        height=6,
    )
image_label.grid(row=2, column=0, pady=26, padx=24)

tips_frame = tk.Frame(left_panel, bg=PANEL_ALT, highlightbackground=BORDER, highlightthickness=1, padx=18, pady=16)
tips_frame.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 24))
tips_frame.grid_columnconfigure(0, weight=1)

tips_title = tk.Label(
    tips_frame,
    text="Quick Tips",
    bg=PANEL_ALT,
    fg=TEXT_PRIMARY,
    font=("Helvetica", 12, "bold"),
)
tips_title.grid(row=0, column=0, sticky="w")

tips_text = tk.Label(
    tips_frame,
    text="Hold SPACE to talk.\nPress Enter to send typed input.\nUse the larger output panel for tree listings and file previews.",
    bg=PANEL_ALT,
    fg=TEXT_MUTED,
    justify="left",
    anchor="w",
    font=("Helvetica", 10),
)
tips_text.grid(row=1, column=0, sticky="ew", pady=(8, 0))

right_panel = tk.Frame(content, bg=PANEL, highlightbackground=BORDER, highlightthickness=1)
right_panel.grid(row=0, column=1, sticky="nsew")
right_panel.grid_columnconfigure(0, weight=1)
right_panel.grid_rowconfigure(1, weight=1)

output_header = tk.Frame(right_panel, bg=PANEL, padx=18, pady=16)
output_header.grid(row=0, column=0, sticky="ew")
output_header.grid_columnconfigure(0, weight=1)

output_title = tk.Label(
    output_header,
    text="Conversation + Filesystem Output",
    bg=PANEL,
    fg=TEXT_PRIMARY,
    font=("Helvetica", 14, "bold"),
)
output_title.grid(row=0, column=0, sticky="w")

output_banner = tk.Label(
    output_header,
    text="Text Output",
    bg=PANEL,
    fg=TEXT_MUTED,
    font=("Courier", 10, "bold"),
)
output_banner.grid(row=1, column=0, sticky="w", pady=(4, 0))

output_frame = tk.Frame(right_panel, bg=OUTPUT_BG, highlightbackground=BORDER, highlightthickness=1)
output_frame.grid(row=1, column=0, sticky="nsew", padx=18, pady=(0, 14))
output_frame.grid_columnconfigure(0, weight=1)
output_frame.grid_rowconfigure(0, weight=1)

text = tk.Text(
    output_frame,
    bg=OUTPUT_BG,
    fg=TEXT_PRIMARY,
    insertbackground=TEXT_PRIMARY,
    selectbackground="#2F2F2F",
    wrap=tk.NONE,
    relief=tk.FLAT,
    borderwidth=0,
    padx=14,
    pady=14,
    font=("Courier New", 11),
)
text.grid(row=0, column=0, sticky="nsew")

y_scrollbar = tk.Scrollbar(output_frame, orient=tk.VERTICAL, command=text.yview)
y_scrollbar.grid(row=0, column=1, sticky="ns")
text.config(yscrollcommand=y_scrollbar.set)

x_scrollbar = tk.Scrollbar(output_frame, orient=tk.HORIZONTAL, command=text.xview)
x_scrollbar.grid(row=1, column=0, sticky="ew")
text.config(xscrollcommand=x_scrollbar.set)

input_frame = tk.Frame(right_panel, bg=PANEL, padx=18, pady=18)
input_frame.grid(row=2, column=0, sticky="ew", pady=(0, 18))
input_frame.grid_columnconfigure(0, weight=1)

status_var = tk.StringVar(value="Ready for voice or text input.")
status_label = tk.Label(
    input_frame,
    textvariable=status_var,
    bg=PANEL,
    fg=TEXT_MUTED,
    anchor="w",
    font=("Helvetica", 10),
)
status_label.grid(row=0, column=0, sticky="ew", pady=(0, 10))

entry_row = tk.Frame(input_frame, bg=PANEL)
entry_row.grid(row=1, column=0, sticky="ew")
entry_row.grid_columnconfigure(0, weight=1)

entry1 = tk.Entry(
    entry_row,
    justify=tk.LEFT,
    bg=ENTRY_BG,
    fg=TEXT_PRIMARY,
    insertbackground=TEXT_PRIMARY,
    relief=tk.FLAT,
    highlightthickness=1,
    highlightbackground=BORDER,
    highlightcolor=ACCENT,
    font=("Helvetica", 12),
)
entry1.grid(row=0, column=0, sticky="ew", ipady=12)
entry1.bind("<Return>", lambda _event: User_send())

send_button = _make_button(entry_row, "Send", User_send, width=10)
send_button.grid(row=0, column=1, padx=(12, 0))

confirm_row = tk.Frame(input_frame, bg=PANEL)
confirm_row.grid(row=2, column=0, sticky="w", pady=(14, 0))

confirm_yes_button = _make_button(confirm_row, "Yes", _confirm_delete, bg=SUCCESS, width=8)
confirm_yes_button.grid(row=0, column=0, padx=(0, 10))

confirm_no_button = _make_button(confirm_row, "No", _cancel_delete, bg=DANGER, width=8)
confirm_no_button.grid(row=0, column=1)

actions_row = tk.Frame(input_frame, bg=PANEL)
actions_row.grid(row=3, column=0, sticky="ew", pady=(18, 0))
actions_row.grid_columnconfigure((0, 1, 2), weight=1)

ask_button = _make_button(actions_row, "Start Voice Mode", ask_with_speech)
ask_button.grid(row=0, column=0, sticky="ew", padx=(0, 10))

clear_button = _make_button(actions_row, "Clear Output", delete_text)
clear_button.grid(row=0, column=1, sticky="ew", padx=5)

focus_button = _make_button(actions_row, "Focus Input", lambda: entry1.focus_set())
focus_button.grid(row=0, column=2, sticky="ew", padx=(10, 0))


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
_refresh_confirmation_controls()
entry1.focus_set()

root.mainloop()
