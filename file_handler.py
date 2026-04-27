import datetime
import logging
import mimetypes
import os
import platform
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, Iterator, List, Optional

import conversation_manager


_BASE_DIR = Path(__file__).resolve().parent
_LOG_PATH = _BASE_DIR / "file_operations.log"
_LOGGER = logging.getLogger("jarvis_file_operations")
if not _LOGGER.handlers:
    _LOGGER.setLevel(logging.INFO)
    _HANDLER = logging.FileHandler(_LOG_PATH, encoding="utf-8")
    _HANDLER.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    _LOGGER.addHandler(_HANDLER)
    _LOGGER.propagate = False

_MAX_TREE_NODES = 300
_MAX_PREVIEW_CHARS = 50000
_DEFAULT_READ_MAX_SIZE_MB = 100
_STREAM_CHUNK_BYTES = 1024 * 1024
_OPENABLE_EXTENSIONS = {
    ".pdf",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".bmp",
    ".svg",
    ".mp4",
    ".mp3",
    ".wav",
    ".mkv",
}
_TEXT_EXTENSIONS = {
    ".txt",
    ".md",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".csv",
    ".tsv",
    ".log",
    ".html",
    ".css",
    ".js",
    ".ts",
    ".jsx",
    ".tsx",
    ".xml",
    ".sh",
    ".bat",
    ".ps1",
    ".java",
    ".c",
    ".cpp",
    ".h",
    ".hpp",
    ".go",
    ".rs",
}
_LOCATION_ALIASES = {
    "desktop": Path.home() / "Desktop",
    "downloads": Path.home() / "Downloads",
    "documents": Path.home() / "Documents",
    "pictures": Path.home() / "Pictures",
    "music": Path.home() / "Music",
    "videos": Path.home() / "Videos",
    "home": Path.home(),
    "root": Path("/"),
    "current directory": Path.cwd(),
    "current folder": Path.cwd(),
    "cwd": Path.cwd(),
}
_YES_WORDS = {"yes", "y", "confirm", "confirmed", "ok", "okay", "proceed", "delete it"}
_NO_WORDS = {"no", "n", "cancel", "stop", "abort", "don't", "do not"}
_LOCATION_FILLER_PHRASES = (
    "of this pc",
    "of this computer",
    "on this pc",
    "on this computer",
    "in this pc",
    "in this computer",
    "from this pc",
    "from this computer",
)


def _log_operation(action: str, path: str, status: str, details: str = "") -> None:
    message = f"action={action} path={path!r} status={status}"
    if details:
        message += f" details={details!r}"
    _LOGGER.info(message)


def _log_operation_start(action: str, path: str, details: str = "") -> None:
    _log_operation(action, path, "started", details)


def _log_operation_error(action: str, path: str, details: str = "") -> None:
    _log_operation(action, path, "error", details)


def _ensure_not_symlink(path: Path, action: str) -> None:
    if os.path.islink(path):
        raise PermissionError(f"Refusing to {action} symlink target: {path}")


def _ensure_delete_permissions(path: Path) -> None:
    parent = path.parent if path.parent != path else path
    if not os.access(path, os.W_OK):
        raise PermissionError(f"Write permission is required for {path}")
    if not os.access(parent, os.W_OK):
        raise PermissionError(f"Delete permission is required in {parent}")


def _fsync_file(handle) -> None:
    handle.flush()
    os.fsync(handle.fileno())


def _write_temp_file(parent: Path, suffix: str = ".tmp") -> Path:
    parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(dir=parent, prefix=".va_tmp_", suffix=suffix)
    os.close(fd)
    return Path(temp_path)


def _atomic_create_file(path: Path, content: str) -> None:
    temp_path = _write_temp_file(path.parent, suffix=path.suffix or ".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write(str(content))
            _fsync_file(handle)
        os.link(temp_path, path)
    except FileExistsError:
        raise FileExistsError(f"File already exists at {path}")
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _atomic_replace_file(path: Path, content: str) -> None:
    temp_path = _write_temp_file(path.parent, suffix=path.suffix or ".tmp")
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            handle.write(str(content))
            _fsync_file(handle)
        os.replace(temp_path, path)
    finally:
        try:
            if temp_path.exists():
                temp_path.unlink()
        except OSError:
            pass


def _rollback_on_failure(records: List[Dict[str, str]], failure_reason: str) -> List[str]:
    rollback_messages: List[str] = []
    for record in reversed(records):
        path = Path(record["path"])
        record_type = record.get("type", "file")
        try:
            if not path.exists():
                rollback_messages.append(f"Skipped rollback for missing {record_type}: {path}")
                continue
            if record_type == "directory":
                path.rmdir()
            else:
                path.unlink()
            rollback_messages.append(f"Rolled back {record_type}: {path}")
        except Exception as exc:
            rollback_messages.append(f"Failed to roll back {record_type} {path}: {exc}")
            _log_operation_error("rollback", str(path), f"{failure_reason}; rollback_error={exc}")
    return rollback_messages


def _stream_file_chunks(path: Path, chunk_size: int = _STREAM_CHUNK_BYTES) -> Iterator[str]:
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            yield chunk


def _strip_quotes(value: str) -> str:
    text = str(value or "").strip().strip(",")
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {'"', "'"}:
        return text[1:-1].strip()
    return text


def _sanitize_path_text(path_text: str) -> str:
    text = _strip_quotes(path_text)
    if not text:
        raise ValueError("Path is required.")
    if "\x00" in text:
        raise ValueError("Path contains invalid null bytes.")
    if any(ord(char) < 32 for char in text if char not in "\t\n\r"):
        raise ValueError("Path contains control characters.")
    pure = Path(text)
    if not pure.is_absolute() and ".." in pure.parts:
        raise ValueError("Relative paths cannot use parent-directory traversal.")
    return text


def _normalize_phrase(text: str) -> str:
    return " ".join(str(text or "").strip().split())


def _normalize_command_text(text: str) -> str:
    normalized = _normalize_phrase(text)
    normalized = re.sub(r"^operating system operations?\.?\s*", "", normalized, flags=re.IGNORECASE)
    normalized = re.sub(
        r"^(?:hey\s+)?(?:jarvis|assistant)[, ]+",
        "",
        normalized,
        flags=re.IGNORECASE,
    ).strip()
    normalized = re.sub(
        r"^(?:please\s+)?(?:can|could|would)\s+you\s+",
        "",
        normalized,
        flags=re.IGNORECASE,
    ).strip()
    normalized = re.sub(r"^please\s+", "", normalized, flags=re.IGNORECASE).strip()
    normalized = re.sub(r"\bcreatefile\b", "create file", normalized, flags=re.IGNORECASE)
    return normalized


def _normalize_location_text(text: str) -> str:
    normalized = _normalize_phrase(text)
    lowered = normalized.lower()
    for filler in _LOCATION_FILLER_PHRASES:
        lowered = re.sub(rf"\b{re.escape(filler)}\b", "", lowered, flags=re.IGNORECASE)
    lowered = re.sub(r"\s+", " ", lowered).strip(" ,.")
    return lowered


def _looks_like_path_reference(text: str) -> bool:
    lowered = _normalize_location_text(text)
    if not lowered:
        return False
    if lowered in _LOCATION_ALIASES:
        return True
    return any(
        marker in lowered
        for marker in ("/", "~", ".", "desktop", "downloads", "documents", "pictures", "music", "videos", "home", "root")
    )


def _resolve_location_alias(location_text: str) -> Optional[Path]:
    lowered = _normalize_location_text(location_text)
    return _LOCATION_ALIASES.get(lowered)


def _coerce_foreign_home_path(path: Path) -> Path:
    current_home = Path.home()
    parts = path.parts
    if len(parts) < 2:
        return path

    if path == Path("/Users") or path == Path("/home"):
        return current_home

    foreign_home_root = None
    if len(parts) >= 3 and parts[0] == "/" and parts[1] in {"Users", "home"}:
        foreign_home_root = Path(parts[0], parts[1], parts[2])

    if foreign_home_root is None or foreign_home_root == current_home:
        return path

    remainder = parts[3:]
    alias_candidate = _LOCATION_ALIASES.get(parts[2].lower())
    if alias_candidate is not None:
        return alias_candidate.joinpath(*remainder)
    return current_home.joinpath(*remainder)


def _finalize_path(path: Path, follow_symlinks: bool = True) -> Path:
    if follow_symlinks:
        return path.resolve()
    return Path(os.path.abspath(path))


def resolve_user_path(path_text: str, base_path: Optional[str] = None, follow_symlinks: bool = True) -> str:
    raw_text = _sanitize_path_text(path_text)
    alias_path = _resolve_location_alias(raw_text)
    if alias_path is not None:
        return str(_finalize_path(alias_path, follow_symlinks=follow_symlinks))

    lowered_raw = raw_text.lower()
    for alias_name, alias_root in _LOCATION_ALIASES.items():
        prefix = alias_name.lower() + "/"
        if lowered_raw.startswith(prefix):
            suffix = raw_text[len(alias_name):].lstrip("/\\")
            return str(_finalize_path(alias_root / suffix, follow_symlinks=follow_symlinks))

    base_resolved = None
    if base_path:
        base_resolved = Path(resolve_user_path(base_path, follow_symlinks=follow_symlinks))

    expanded = Path(os.path.expanduser(raw_text))
    if expanded.is_absolute():
        expanded = _coerce_foreign_home_path(expanded)
        return str(_finalize_path(expanded, follow_symlinks=follow_symlinks))
    if base_resolved is not None:
        return str(_finalize_path(base_resolved / expanded, follow_symlinks=follow_symlinks))
    return str(_finalize_path(Path.cwd() / expanded, follow_symlinks=follow_symlinks))


def _human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(size)
    for unit in units:
        if value < 1024.0 or unit == units[-1]:
            return f"{value:.1f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024.0
    return f"{int(size)} B"


def _mode_to_permissions(mode: int) -> str:
    flags = (
        (0o400, "r"),
        (0o200, "w"),
        (0o100, "x"),
        (0o040, "r"),
        (0o020, "w"),
        (0o010, "x"),
        (0o004, "r"),
        (0o002, "w"),
        (0o001, "x"),
    )
    return "".join(letter if mode & mask else "-" for mask, letter in flags)


def _detect_text_file(path: Path) -> bool:
    mime_type, _ = mimetypes.guess_type(str(path))
    if path.suffix.lower() in _TEXT_EXTENSIONS:
        return True
    return bool(mime_type and (mime_type.startswith("text/") or "json" in mime_type or "xml" in mime_type))


def get_file_info(file_path) -> Dict[str, str]:
    resolved = Path(resolve_user_path(file_path))
    if not resolved.exists():
        raise FileNotFoundError(f"File not found at {resolved}")

    stat_info = resolved.stat()
    mime_type, _ = mimetypes.guess_type(str(resolved))
    return {
        "path": str(resolved),
        "name": resolved.name,
        "size": stat_info.st_size,
        "size_human": _human_size(stat_info.st_size),
        "modified": datetime.datetime.fromtimestamp(stat_info.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
        "permissions": _mode_to_permissions(stat_info.st_mode),
        "is_dir": resolved.is_dir(),
        "is_file": resolved.is_file(),
        "mime_type": mime_type or "unknown",
        "type": "directory" if resolved.is_dir() else "file",
    }


def _format_entry_label(path: Path) -> str:
    info = get_file_info(str(path))
    if info["is_dir"]:
        return f"[DIR] {path.name or path}/"
    return f"[FILE] {path.name} ({info['size_human']}, {info['mime_type']})"


def list_directory(
    path,
    include_hidden: bool = False,
    show_tree: bool = True,
    directories_only: bool = False,
    files_only: bool = False,
) -> str:
    resolved = Path(resolve_user_path(path))
    if not resolved.exists():
        raise FileNotFoundError(f"File not found at {resolved}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"{resolved} is not a directory.")

    lines = [f"[DIR] {resolved}/"]
    node_count = 0
    truncated = False

    def _filtered_entries(directory: Path) -> List[Path]:
        items = []
        for entry in directory.iterdir():
            if not include_hidden and entry.name.startswith("."):
                continue
            if directories_only and not entry.is_dir():
                continue
            if files_only and not entry.is_file():
                continue
            items.append(entry)
        return sorted(items, key=lambda item: (not item.is_dir(), item.name.lower()))

    def _walk(directory: Path, prefix: str = "") -> None:
        nonlocal node_count, truncated
        try:
            entries = _filtered_entries(directory)
        except PermissionError:
            lines.append(prefix + "`-- [ACCESS DENIED]")
            return

        if not entries:
            if directory == resolved:
                lines.append("`-- [empty]")
            return

        for index, entry in enumerate(entries):
            if node_count >= _MAX_TREE_NODES:
                truncated = True
                return
            is_last = index == len(entries) - 1
            connector = "`-- " if is_last else "|-- "
            lines.append(prefix + connector + _format_entry_label(entry))
            node_count += 1
            if show_tree and entry.is_dir() and not entry.is_symlink():
                child_prefix = prefix + ("    " if is_last else "|   ")
                _walk(entry, child_prefix)
                if truncated:
                    return

    if show_tree:
        _walk(resolved)
    else:
        for entry in _filtered_entries(resolved):
            lines.append(_format_entry_label(entry))

    if truncated:
        lines.append("")
        lines.append(f"[INFO] Listing truncated after {_MAX_TREE_NODES} items.")

    _log_operation("list", str(resolved), "success", f"hidden={include_hidden} tree={show_tree}")
    return "\n".join(lines)


def open_file_with_default_app(file_path) -> str:
    resolved = Path(resolve_user_path(file_path))
    if not resolved.exists():
        raise FileNotFoundError(f"File not found at {resolved}")

    system = platform.system()
    try:
        if system == "Windows":
            os.startfile(str(resolved))
        elif system == "Darwin":
            subprocess.Popen(["open", str(resolved)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            subprocess.Popen(["xdg-open", str(resolved)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        _log_operation("open", str(resolved), "error", str(exc))
        return f"Unable to open {resolved} automatically. Location: {resolved}"

    _log_operation("open", str(resolved), "success")
    return f"Opened {resolved} with the default system app."


def read_file_content(file_path, max_size_mb: int = _DEFAULT_READ_MAX_SIZE_MB, streaming: bool = False):
    resolved = Path(resolve_user_path(file_path))
    _log_operation_start("read", str(resolved), f"streaming={streaming} max_size_mb={max_size_mb}")
    if not resolved.exists():
        raise FileNotFoundError(f"File not found at {resolved}")
    if resolved.is_dir():
        return list_directory(str(resolved), include_hidden=False, show_tree=True)

    file_size = resolved.stat().st_size
    max_bytes = max_size_mb * 1024 * 1024
    if file_size > max_bytes:
        if streaming and _detect_text_file(resolved) and resolved.suffix.lower() not in _OPENABLE_EXTENSIONS:
            _log_operation("read", str(resolved), "streaming", f"size={file_size}")
            return _stream_file_chunks(resolved)
        _log_operation("read", str(resolved), "large_file", f"size={file_size}")
        return f"File is too large to display ({_human_size(file_size)}). Location: {resolved}"

    if not _detect_text_file(resolved) or resolved.suffix.lower() in _OPENABLE_EXTENSIONS:
        return open_file_with_default_app(str(resolved))

    with open(resolved, "r", encoding="utf-8", errors="replace") as handle:
        content = handle.read()

    if len(content) > _MAX_PREVIEW_CHARS:
        content = content[:_MAX_PREVIEW_CHARS] + "\n\n[INFO] File preview truncated."

    _log_operation("read", str(resolved), "success", f"size={file_size}")
    return f"[FILE] {resolved}\n{content}"


def create_file(file_path, content: str = "") -> str:
    resolved = Path(resolve_user_path(file_path))
    _log_operation_start("create_file", str(resolved), f"bytes={len(str(content))}")
    if resolved.exists() and resolved.is_dir():
        raise IsADirectoryError(f"{resolved} is a directory.")

    resolved.parent.mkdir(parents=True, exist_ok=True)
    if resolved.exists():
        raise FileExistsError(f"File already exists at {resolved}")

    try:
        _atomic_create_file(resolved, str(content))
    except Exception as exc:
        _log_operation_error("create_file", str(resolved), str(exc))
        raise

    _log_operation("create_file", str(resolved), "success", f"bytes={len(str(content))}")
    return f"Created file: {resolved}"


def create_directory(dir_path) -> str:
    resolved = Path(resolve_user_path(dir_path))
    _log_operation_start("create_directory", str(resolved))
    if resolved.exists():
        if resolved.is_dir():
            return f"Directory already exists: {resolved}"
        raise FileExistsError(f"A file already exists at {resolved}")

    resolved.mkdir(parents=True, exist_ok=True)
    _log_operation("create_directory", str(resolved), "success")
    return f"Created directory: {resolved}"


def delete_file(file_path, require_confirm: bool = True) -> str:
    resolved = Path(resolve_user_path(file_path, follow_symlinks=False))
    _log_operation_start("delete_file", str(resolved), f"require_confirm={require_confirm}")
    if not resolved.exists():
        raise FileNotFoundError(f"File not found at {resolved}")
    if resolved.is_dir():
        raise IsADirectoryError(f"{resolved} is a directory, not a file.")
    _ensure_not_symlink(resolved, "delete")
    _ensure_delete_permissions(resolved)

    if require_confirm:
        conversation_manager.set_pending_file_operation(
            {
                "operation": "delete_file",
                "path": str(resolved),
            }
        )
        _log_operation("delete_file", str(resolved), "pending_confirmation")
        return f"Confirm delete file: {resolved}? Use Yes/No."

    try:
        if not resolved.exists():
            _log_operation("delete_file", str(resolved), "missing_before_unlink")
            conversation_manager.clear_pending_file_operation()
            return f"File already disappeared: {resolved}"
        resolved.unlink()
    except FileNotFoundError:
        _log_operation("delete_file", str(resolved), "missing_during_unlink")
        conversation_manager.clear_pending_file_operation()
        return f"File already disappeared: {resolved}"
    except Exception as exc:
        _log_operation_error("delete_file", str(resolved), str(exc))
        raise
    conversation_manager.clear_pending_file_operation()
    _log_operation("delete_file", str(resolved), "success")
    return f"Deleted file: {resolved}"


def delete_directory(dir_path, recursive: bool = True, require_confirm: bool = True) -> str:
    resolved = Path(resolve_user_path(dir_path, follow_symlinks=False))
    _log_operation_start("delete_directory", str(resolved), f"recursive={recursive} require_confirm={require_confirm}")
    if not resolved.exists():
        raise FileNotFoundError(f"Folder not found at {resolved}")
    if not resolved.is_dir():
        raise NotADirectoryError(f"{resolved} is not a directory.")
    _ensure_not_symlink(resolved, "delete")
    _ensure_delete_permissions(resolved)

    if require_confirm:
        conversation_manager.set_pending_file_operation(
            {
                "operation": "delete_directory",
                "path": str(resolved),
                "recursive": "true" if recursive else "false",
            }
        )
        _log_operation("delete_directory", str(resolved), "pending_confirmation", f"recursive={recursive}")
        return f"Confirm delete folder: {resolved}? Use Yes/No."

    try:
        if not resolved.exists():
            _log_operation("delete_directory", str(resolved), "missing_before_delete", f"recursive={recursive}")
            conversation_manager.clear_pending_file_operation()
            return f"Directory already disappeared: {resolved}"
        if recursive:
            shutil.rmtree(resolved)
        else:
            resolved.rmdir()
    except FileNotFoundError:
        _log_operation("delete_directory", str(resolved), "missing_during_delete", f"recursive={recursive}")
        conversation_manager.clear_pending_file_operation()
        return f"Directory already disappeared: {resolved}"
    except Exception as exc:
        _log_operation_error("delete_directory", str(resolved), str(exc))
        raise
    conversation_manager.clear_pending_file_operation()
    _log_operation("delete_directory", str(resolved), "success", f"recursive={recursive}")
    return f"Deleted directory: {resolved}"


def update_file(file_path, content, append: bool = False, replace_line=None) -> str:
    resolved = Path(resolve_user_path(file_path))
    _log_operation_start(
        "update_file",
        str(resolved),
        f"append={append} replace_line={replace_line}",
    )
    if not resolved.exists():
        raise FileNotFoundError(f"File not found at {resolved}")
    if resolved.is_dir():
        raise IsADirectoryError(f"{resolved} is a directory.")

    try:
        if replace_line is not None:
            with open(resolved, "r", encoding="utf-8", errors="replace") as handle:
                lines = handle.readlines()
            line_number = int(replace_line)
            if line_number < 1 or line_number > len(lines):
                raise ValueError(f"Line {line_number} is out of range for {resolved}")
            lines[line_number - 1] = str(content) + "\n"
            _atomic_replace_file(resolved, "".join(lines))
            _log_operation("update_file", str(resolved), "success", f"replace_line={line_number}")
            return f"Updated line {line_number} in {resolved}"

        if append:
            with open(resolved, "r", encoding="utf-8", errors="replace") as handle:
                existing_content = handle.read()
            new_content = existing_content + str(content)
            if str(content) and not str(content).endswith("\n"):
                new_content += "\n"
            _atomic_replace_file(resolved, new_content)
        else:
            _atomic_replace_file(resolved, str(content))
    except Exception as exc:
        _log_operation_error("update_file", str(resolved), str(exc))
        raise

    _log_operation("update_file", str(resolved), "success", f"append={append}")
    return f"{'Appended to' if append else 'Updated'} file: {resolved}"


def _extract_base_location(command_text: str) -> Optional[str]:
    match = re.search(r"\b(?:on|in|at|under)\s+(.+)$", command_text, flags=re.IGNORECASE)
    if not match:
        return None
    return _strip_quotes(match.group(1))


def _split_target_and_base(command_text: str) -> tuple[str, Optional[str]]:
    match = re.search(r"^(.*?)(?:\s+\b(?:on|in|at|under)\b\s+(.+))?$", command_text, flags=re.IGNORECASE)
    if not match:
        return command_text.strip(), None
    target = _strip_quotes(match.group(1))
    base = _strip_quotes(match.group(2) or "")
    return target, base or None


def _parse_list_command(command_text: str) -> Dict[str, object]:
    lowered = command_text.lower()
    include_hidden = "hidden" in lowered
    show_tree = "flat" not in lowered
    directories_only = any(token in lowered for token in ("directories only", "only directories", "folders only", "only folders"))
    files_only = any(token in lowered for token in ("files only", "only files"))
    cleaned = re.sub(r"\bincluding hidden\b", "", command_text, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bwith hidden\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bshow hidden\b", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bflat list\b", "", cleaned, flags=re.IGNORECASE)
    location = _extract_base_location(cleaned)
    target_path = resolve_user_path(location or ".")
    return {
        "operation": "list",
        "path": target_path,
        "include_hidden": include_hidden,
        "show_tree": show_tree,
        "directories_only": directories_only,
        "files_only": files_only,
    }


def _parse_read_command(command_text: str) -> Dict[str, object]:
    stripped = re.sub(r"^(read|show content of|show content|get content of|get content|open file)\s+", "", command_text, flags=re.IGNORECASE).strip()
    return {
        "operation": "read",
        "path": resolve_user_path(stripped),
    }


def _parse_open_command(command_text: str) -> Dict[str, object]:
    stripped = re.sub(
        r"^(open|launch)\s+(?:the\s+)?(?:file|folder|directory)?\s*",
        "",
        command_text,
        flags=re.IGNORECASE,
    ).strip()
    stripped = re.sub(r"\s+(?:file|folder|directory)$", "", stripped, flags=re.IGNORECASE).strip()
    return {
        "operation": "open",
        "path": resolve_user_path(stripped),
    }


def _parse_info_command(command_text: str) -> Dict[str, object]:
    stripped = re.sub(r"^(info|details|show info for|show details for)\s+", "", command_text, flags=re.IGNORECASE).strip()
    return {
        "operation": "info",
        "path": resolve_user_path(stripped),
    }


def _parse_delete_command(command_text: str) -> Dict[str, object]:
    stripped = re.sub(r"^(delete|remove|rm)\s+", "", command_text, flags=re.IGNORECASE).strip()
    target_kind = "auto"
    recursive = True
    if re.match(r"^(folder|directory|dir)\b", stripped, flags=re.IGNORECASE):
        stripped = re.sub(r"^(folder|directory|dir)\s+", "", stripped, flags=re.IGNORECASE)
        target_kind = "directory"
    elif re.match(r"^file\b", stripped, flags=re.IGNORECASE):
        stripped = re.sub(r"^file\s+", "", stripped, flags=re.IGNORECASE)
        target_kind = "file"
    return {
        "operation": "delete",
        "path": resolve_user_path(stripped),
        "target_kind": target_kind,
        "recursive": recursive,
        "confirm_needed": True,
    }


def _parse_create_batch(command_text: str) -> Optional[Dict[str, object]]:
    match = re.search(
        r"(?:create|make|new)\s+folder\s+(.+?)\s+with\s+file\s+(.+?)(?:\s+inside)?(?:\s+(?:on|in|at)\s+(.+))?$",
        command_text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    folder_name = _strip_quotes(match.group(1))
    file_name = _strip_quotes(match.group(2))
    base_location = _strip_quotes(match.group(3) or "")
    folder_path = resolve_user_path(folder_name, base_location or None)
    file_path = resolve_user_path(file_name, folder_path)
    return {
        "operation": "batch_create",
        "actions": [
            {"create": "directory", "path": folder_path},
            {"create": "file", "path": file_path, "content": ""},
        ],
    }


def _parse_nested_create_command(command_text: str) -> Optional[Dict[str, object]]:
    normalized = _normalize_phrase(command_text)
    lowered = normalized.lower()
    if "create" not in lowered or "folder" not in lowered or "file" not in lowered:
        return None
    if "inside" not in lowered:
        return None
    if "another folder" not in lowered and lowered.count("folder") < 2:
        return None

    base_location = None
    base_match = re.search(r"\b(?:in|on|at)\s+([a-zA-Z0-9_./~ -]+?)\s+create\b", normalized, flags=re.IGNORECASE)
    if base_match:
        base_location = _strip_quotes(base_match.group(1))
        normalized = normalized[base_match.end() - len("create") :]
        lowered = normalized.lower()

    content = ""
    content_match = re.search(
        r"\b(?:which has text|with text|with content|containing text|contains text)\s+(.+)$",
        normalized,
        flags=re.IGNORECASE,
    )
    if content_match:
        content = _strip_quotes(content_match.group(1))
        normalized = normalized[: content_match.start()].strip(" ,.")
        lowered = normalized.lower()

    folder_names = [
        _strip_quotes(name)
        for name in re.findall(r"(?:another\s+)?folder\s+([^,]+?)(?=,\s*inside|\s+inside|$)", normalized, flags=re.IGNORECASE)
    ]
    file_match = re.search(r"\bfile\s+([^,]+?)(?:$|\s+which|\s+with|\s+inside)", normalized, flags=re.IGNORECASE)

    if not folder_names or not file_match:
        return None

    current_path = resolve_user_path(folder_names[0], base_location)
    actions: List[Dict[str, object]] = [{"create": "directory", "path": current_path}]

    for folder_name in folder_names[1:]:
        current_path = resolve_user_path(folder_name, current_path)
        actions.append({"create": "directory", "path": current_path})

    file_name = _strip_quotes(file_match.group(1))
    file_path = resolve_user_path(file_name, current_path)
    actions.append({"create": "file", "path": file_path, "content": content})
    return {
        "operation": "batch_create",
        "actions": actions,
    }


def _parse_descriptive_create_command(command_text: str) -> Optional[Dict[str, object]]:
    normalized = _normalize_phrase(command_text)
    match = re.match(
        r'^create\s+(?:in|on|at|under)\s+(.+?),\s*(?:a|an)?\s*folder\s+(?:called|named)\s+(.+?)\s+'
        r'in which there is\s+(?:a|an)?\s*file\s+(.+?)\s+in which there is\s+text\s+(.+)$',
        normalized,
        flags=re.IGNORECASE,
    )
    if not match:
        return None

    base_location = _strip_quotes(match.group(1))
    folder_name = _strip_quotes(match.group(2))
    file_name = _strip_quotes(match.group(3))
    content = _strip_quotes(match.group(4))

    folder_path = resolve_user_path(folder_name, base_location)
    file_path = resolve_user_path(file_name, folder_path)
    return {
        "operation": "batch_create",
        "actions": [
            {"create": "directory", "path": folder_path},
            {"create": "file", "path": file_path, "content": content},
        ],
    }


def _parse_create_file(command_text: str) -> Optional[Dict[str, object]]:
    stripped = re.sub(
        r"^(create|make|new)\s+(?:a|an)?\s*file\s+",
        "",
        command_text,
        flags=re.IGNORECASE,
    ).strip()
    stripped = re.sub(r"^(?:called|named)\s+", "", stripped, flags=re.IGNORECASE).strip()
    content = ""

    content_match = re.search(
        r"\s+(?:with\s+(?:content|text)|containing(?:\s+text)?|that\s+says)\s+(.+)$",
        stripped,
        flags=re.IGNORECASE,
    )
    if content_match:
        file_part = stripped[: content_match.start()].strip()
        content_part = content_match.group(1).strip()
        trailing_location = re.match(
            r"(.+?)(?:\s+(?:on|in|at|under)\s+(.+))?$",
            content_part,
            flags=re.IGNORECASE,
        )
        content = _strip_quotes(trailing_location.group(1) if trailing_location else content_part)
        base_location = _strip_quotes(trailing_location.group(2) if trailing_location else "")
        file_name, inline_location = _split_target_and_base(file_part)
        return {
            "operation": "create_file",
            "path": resolve_user_path(file_name, base_location or inline_location),
            "content": content,
        }

    file_name, base_location = _split_target_and_base(stripped)
    if file_name.lower().startswith(("called ", "named ")):
        file_name = _strip_quotes(re.sub(r"^(called|named)\s+", "", file_name, flags=re.IGNORECASE))
    return {
        "operation": "create_file",
        "path": resolve_user_path(file_name, base_location),
        "content": "",
    }


def _parse_create_directory(command_text: str) -> Optional[Dict[str, object]]:
    stripped = re.sub(
        r"^(create|make|new)\s+(?:a|an)?\s*(folder|directory|dir)\s+",
        "",
        command_text,
        flags=re.IGNORECASE,
    ).strip()

    named_match = re.match(
        r"^(?:named\s+)?(.+?)\s+(?:on|in|at|under)\s+(.+)$",
        stripped,
        flags=re.IGNORECASE,
    )
    if named_match:
        dir_name = _strip_quotes(named_match.group(1))
        base_location = _strip_quotes(named_match.group(2))
        if dir_name.lower().startswith("named "):
            dir_name = _strip_quotes(dir_name[6:])
        return {
            "operation": "create_directory",
            "path": resolve_user_path(dir_name, base_location),
        }

    reverse_named_match = re.match(
        r"^(?:on|in|at|under)\s+(.+?)\s+named\s+(.+)$",
        stripped,
        flags=re.IGNORECASE,
    )
    if reverse_named_match:
        base_location = _strip_quotes(reverse_named_match.group(1))
        dir_name = _strip_quotes(reverse_named_match.group(2))
        return {
            "operation": "create_directory",
            "path": resolve_user_path(dir_name, base_location),
        }

    dir_name, base_location = _split_target_and_base(stripped)
    if dir_name.lower().startswith("named "):
        dir_name = _strip_quotes(dir_name[6:])
    return {
        "operation": "create_directory",
        "path": resolve_user_path(dir_name, base_location),
    }


def _parse_update_command(command_text: str) -> Optional[Dict[str, object]]:
    append_match = re.search(r"^(append|add)\s+(.+?)\s+to\s+(.+)$", command_text, flags=re.IGNORECASE)
    if append_match:
        return {
            "operation": "update",
            "path": resolve_user_path(_strip_quotes(append_match.group(3))),
            "content": _strip_quotes(append_match.group(2)),
            "append": True,
        }

    update_match = re.search(
        r"^(update|modify|edit|write)\s+(.+?)\s+(?:with content|with|to)\s+(.+)$",
        command_text,
        flags=re.IGNORECASE,
    )
    if update_match:
        target_path = _strip_quotes(update_match.group(2))
        if not _looks_like_path_reference(target_path):
            return None
        return {
            "operation": "update",
            "path": resolve_user_path(target_path),
            "content": _strip_quotes(update_match.group(3)),
            "append": False,
        }
    return None


def parse_natural_language_command(command) -> Dict[str, object]:
    normalized = _normalize_command_text(command)
    lowered = normalized.lower()
    if not normalized:
        return {}

    try:
        descriptive_create_command = _parse_descriptive_create_command(normalized)
        if descriptive_create_command:
            return descriptive_create_command

        nested_create_command = _parse_nested_create_command(normalized)
        if nested_create_command:
            return nested_create_command

        batch_command = _parse_create_batch(normalized)
        if batch_command:
            return batch_command

        if re.match(r"^(list|show|display)\b", lowered):
            if any(token in lowered for token in ("file", "files", "folder", "folders", "directory", "directories", "hidden")) or _looks_like_path_reference(lowered):
                return _parse_list_command(normalized)

        if re.match(r"^(read|show content|get content|open file)\b", lowered):
            stripped_read = re.sub(r"^(read|show content of|show content|get content of|get content|open file)\s+", "", normalized, flags=re.IGNORECASE).strip()
            if lowered.startswith(("show content", "get content", "open file")) or _looks_like_path_reference(stripped_read):
                return _parse_read_command(normalized)

        if re.match(r"^(open|launch)\b", lowered):
            stripped_open = re.sub(
                r"^(open|launch)\s+(?:the\s+)?(?:file|folder|directory)?\s*",
                "",
                normalized,
                flags=re.IGNORECASE,
            ).strip()
            stripped_open = re.sub(r"\s+(?:file|folder|directory)$", "", stripped_open, flags=re.IGNORECASE).strip()
            if _looks_like_path_reference(stripped_open):
                return _parse_open_command(normalized)

        if re.match(r"^(info|details|show info for|show details for)\b", lowered):
            stripped_info = re.sub(r"^(info|details|show info for|show details for)\s+", "", normalized, flags=re.IGNORECASE).strip()
            if _looks_like_path_reference(stripped_info):
                return _parse_info_command(normalized)

        if re.match(r"^(delete|remove|rm)\b", lowered):
            stripped_delete = re.sub(r"^(delete|remove|rm)\s+", "", normalized, flags=re.IGNORECASE).strip()
            if any(token in stripped_delete.lower() for token in ("file", "folder", "directory", "dir")) or _looks_like_path_reference(stripped_delete):
                return _parse_delete_command(normalized)

        if re.match(r"^(append|add|update|modify|edit|write)\b", lowered):
            update_command = _parse_update_command(normalized)
            if update_command:
                return update_command

        if re.match(r"^(create|make|new)\s+(?:a|an)?\s*file\b", lowered):
            return _parse_create_file(normalized) or {}

        if re.match(r"^(create|make|new)\s+(?:a|an)?\s*(folder|directory|dir)\b", lowered):
            return _parse_create_directory(normalized) or {}

    except Exception as exc:
        return {
            "operation": "error",
            "error": f"Invalid path: {exc}",
        }

    return {}


def handle_pending_confirmation(user_text: str) -> Optional[str]:
    pending = conversation_manager.get_pending_file_operation()
    if not pending:
        return None

    lowered = _normalize_phrase(user_text).lower()
    if lowered in _YES_WORDS:
        operation = pending.get("operation", "")
        path = pending.get("path", "")
        if operation == "delete_file":
            return delete_file(path, require_confirm=False)
        if operation == "delete_directory":
            recursive = pending.get("recursive", "true").lower() == "true"
            return delete_directory(path, recursive=recursive, require_confirm=False)
        conversation_manager.clear_pending_file_operation()
        return "Pending file action cleared."

    if lowered in _NO_WORDS:
        conversation_manager.clear_pending_file_operation()
        return "Cancelled the pending file operation."

    return None


def _execute_batch_create(actions: List[Dict[str, object]]) -> str:
    created_records: List[Dict[str, str]] = []
    attempted_paths: List[str] = []
    _log_operation_start("batch_create", "", f"actions={len(actions)}")

    try:
        for action in actions:
            create_kind = str(action.get("create", "")).strip().lower()
            path = Path(resolve_user_path(str(action["path"])))
            attempted_paths.append(str(path))

            if create_kind == "directory":
                if path.exists():
                    raise FileExistsError(f"Directory already exists at {path}")
                path.mkdir(parents=True, exist_ok=False)
                created_records.append({"path": str(path), "type": "directory"})
                _log_operation("batch_create", str(path), "success", "created=directory")
                continue

            if create_kind == "file":
                path.parent.mkdir(parents=True, exist_ok=True)
                _atomic_create_file(path, str(action.get("content", "")))
                created_records.append({"path": str(path), "type": "file"})
                _log_operation("batch_create", str(path), "success", "created=file")
                continue

            raise ValueError(f"Unsupported batch action: {create_kind}")

        summary = "\n".join(f"- {record['path']}" for record in created_records)
        return "Created filesystem items:\n" + summary
    except Exception as exc:
        rollback_messages = _rollback_on_failure(created_records, str(exc))
        _log_operation_error("batch_create", "", f"{exc}; attempted={attempted_paths}")
        success_lines = [f"- {record['path']}" for record in created_records]
        failure_lines = [
            "Batch create failed.",
            f"Reason: {exc}",
        ]
        if success_lines:
            failure_lines.append("Created before rollback:")
            failure_lines.extend(success_lines)
        if rollback_messages:
            failure_lines.append("Rollback:")
            failure_lines.extend(f"- {message}" for message in rollback_messages)
        failure_lines.append("Attempted paths:")
        failure_lines.extend(f"- {item}" for item in attempted_paths)
        return "\n".join(failure_lines)


def execute_parsed_command(parsed_command: Dict[str, object]) -> str:
    operation = parsed_command.get("operation")
    if not operation:
        return "I could not match that to a filesystem command."

    if operation == "error":
        return str(parsed_command.get("error", "Invalid filesystem command."))

    if operation == "list":
        return list_directory(
            parsed_command["path"],
            include_hidden=bool(parsed_command.get("include_hidden", False)),
            show_tree=bool(parsed_command.get("show_tree", True)),
            directories_only=bool(parsed_command.get("directories_only", False)),
            files_only=bool(parsed_command.get("files_only", False)),
        )
    if operation == "read":
        return read_file_content(parsed_command["path"])
    if operation == "open":
        return open_file_with_default_app(parsed_command["path"])
    if operation == "info":
        info = get_file_info(parsed_command["path"])
        return "\n".join(
            [
                f"Path: {info['path']}",
                f"Type: {info['type']}",
                f"Size: {info['size_human']}",
                f"Modified: {info['modified']}",
                f"Permissions: {info['permissions']}",
                f"MIME: {info['mime_type']}",
            ]
        )
    if operation == "create_file":
        return create_file(parsed_command["path"], content=str(parsed_command.get("content", "")))
    if operation == "create_directory":
        return create_directory(parsed_command["path"])
    if operation == "delete":
        target_kind = parsed_command.get("target_kind", "auto")
        path = parsed_command["path"]
        if target_kind == "directory":
            return delete_directory(path, recursive=bool(parsed_command.get("recursive", True)), require_confirm=True)
        if target_kind == "file":
            return delete_file(path, require_confirm=True)
        resolved = Path(path)
        if resolved.is_dir():
            return delete_directory(path, recursive=bool(parsed_command.get("recursive", True)), require_confirm=True)
        return delete_file(path, require_confirm=True)
    if operation == "update":
        return update_file(
            parsed_command["path"],
            parsed_command.get("content", ""),
            append=bool(parsed_command.get("append", False)),
            replace_line=parsed_command.get("replace_line"),
        )
    if operation == "batch_create":
        return _execute_batch_create(parsed_command.get("actions", []))

    return "That filesystem operation is not implemented yet."
