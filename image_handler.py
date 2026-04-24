import logging
import os
import re
import tempfile
import threading
import textwrap
from PIL import Image, ImageDraw, ImageFont


_BASE_DIR = os.path.dirname(__file__)
_IMAGE_DIR = os.path.join(_BASE_DIR, "image")
_HANDWRITTEN_PATTERN = re.compile(r"^handwriting_(\d+)\.jpg$")
_LONG_TEXT_WARN_LIMIT = 500
_WRITE_LOCK = threading.Lock()
_LOGGER = logging.getLogger(__name__)
_PYWHATKIT_TIMEOUT_SECONDS = 15
_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/freefont/FreeSerifItalic.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSerif-Italic.ttf",
    "/usr/share/fonts/truetype/liberation2/LiberationSerif-Italic.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSerif-Italic.ttf",
    "/usr/share/fonts/truetype/noto/NotoSerif-Italic.ttf",
]
_IMAGE_WIDTH = 1500
_MARGIN_X = 72
_MARGIN_Y = 64
_LINE_HEIGHT = 52
_MAX_LINES = 140


def _render_with_pywhatkit(text: str, save_to: str) -> None:
    import importlib

    pywhatkit = importlib.import_module("pywhatkit")
    failure = {}

    def _worker():
        try:
            # This is the third-party PyWhatKit API, not a local helper.
            pywhatkit.text_to_handwriting(text, save_to=save_to)
        except Exception as exc:
            failure["error"] = exc

    thread = threading.Thread(target=_worker, daemon=True)
    thread.start()
    thread.join(timeout=_PYWHATKIT_TIMEOUT_SECONDS)

    if thread.is_alive():
        raise TimeoutError(
            f"PyWhatKit handwriting generation exceeded {_PYWHATKIT_TIMEOUT_SECONDS} seconds."
        )
    if "error" in failure:
        raise failure["error"]


def _load_font(size: int = 40):
    for font_path in _FONT_CANDIDATES:
        if os.path.isfile(font_path):
            try:
                return ImageFont.truetype(font_path, size=size)
            except OSError:
                continue
    return ImageFont.load_default()


def _to_wrapped_lines(text: str, width: int = 58):
    raw_paragraphs = str(text or "").replace("\r\n", "\n").split("\n")
    lines = []

    for paragraph in raw_paragraphs:
        line = paragraph.strip()
        if not line:
            lines.append("")
            continue
        lines.extend(textwrap.wrap(line, width=width, break_long_words=True))

    if len(lines) > _MAX_LINES:
        lines = lines[:_MAX_LINES]
        if lines:
            lines[-1] = f"{lines[-1]} ... [truncated]"
    return lines


def _render_with_local_pillow(text: str, save_to: str) -> None:
    font = _load_font()
    lines = _to_wrapped_lines(text)
    if not lines:
        lines = [""]

    line_count = max(1, len(lines))
    canvas_height = int((_MARGIN_Y * 2) + (line_count * _LINE_HEIGHT) + 24)
    image = Image.new("RGB", (_IMAGE_WIDTH, canvas_height), color=(255, 252, 245))
    draw = ImageDraw.Draw(image)

    # Add subtle ruled lines for readability.
    for i in range(line_count + 2):
        y = int(_MARGIN_Y + (i * _LINE_HEIGHT) + (_LINE_HEIGHT * 0.7))
        draw.line((40, y, _IMAGE_WIDTH - 40, y), fill=(234, 230, 220), width=1)

    for idx, line in enumerate(lines):
        jitter_x = ((idx * 7) % 3) - 1
        jitter_y = (idx % 3) - 1
        x = _MARGIN_X + jitter_x
        y = _MARGIN_Y + (idx * _LINE_HEIGHT) + jitter_y
        # Double-pass ink for a less "typed" look.
        draw.text((x + 1, y + 1), line, fill=(48, 48, 48), font=font)
        draw.text((x, y), line, fill=(20, 20, 20), font=font)

    image.save(save_to, format="JPEG", quality=95)


def _render_text_handwriting(text: str, save_to: str) -> None:
    pywhatkit_path = f"{save_to}.pywhatkit"
    try:
        _render_with_pywhatkit(text, pywhatkit_path)
        if not os.path.isfile(pywhatkit_path) or os.path.getsize(pywhatkit_path) == 0:
            raise RuntimeError("PyWhatKit did not produce a valid image file.")
        os.replace(pywhatkit_path, save_to)
    except Exception as exc:
        _LOGGER.warning(
            "PyWhatKit handwriting path failed (%s). Falling back to local Pillow renderer.",
            exc,
        )
        try:
            if os.path.exists(pywhatkit_path):
                os.remove(pywhatkit_path)
        except OSError:
            pass
        _render_with_local_pillow(text, save_to)


def _next_handwritten_index() -> int:
    max_index = 0

    for filename in os.listdir(_IMAGE_DIR):
        match = _HANDWRITTEN_PATTERN.match(filename)
        if not match:
            continue
        index = int(match.group(1))
        if index > max_index:
            max_index = index

    return max_index + 1


def convert_text_to_handwritten_image(text: str) -> str:
    content = str(text or "").strip()
    if not content:
        raise ValueError("No text provided for handwritten image generation.")

    if len(content) > _LONG_TEXT_WARN_LIMIT:
        _LOGGER.warning(
            "Handwritten image text is long (%s chars). PyWhatKit may fail on very long content.",
            len(content),
        )

    os.makedirs(_IMAGE_DIR, exist_ok=True)

    with _WRITE_LOCK:
        index = _next_handwritten_index()
        final_path = os.path.join(_IMAGE_DIR, f"handwriting_{index}.jpg")
        fd, tmp_path = tempfile.mkstemp(
            dir=_IMAGE_DIR,
            prefix=f".handwriting_{index}_",
            suffix=".jpg",
        )
        os.close(fd)

        try:
            _render_text_handwriting(content, tmp_path)
            if not os.path.isfile(tmp_path) or os.path.getsize(tmp_path) == 0:
                raise RuntimeError("PyWhatKit did not produce a valid image file.")
            os.replace(tmp_path, final_path)
            return os.path.abspath(final_path)
        except Exception as exc:
            _LOGGER.warning("Failed to convert text into handwritten image: %s", exc)
            try:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)
            except OSError:
                pass
            raise
