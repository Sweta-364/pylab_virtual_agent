import os
from typing import Dict, List, Tuple

try:
    from ollama import Client
except Exception:
    Client = None


_BASE_DIR = os.path.dirname(__file__)
_ENV_LOADED = False
_SYSTEM_PROMPT = (
    "You are a helpful virtual assistant. Answer questions concisely and naturally. "
    "Do not attempt to execute system commands. If asked to do something that "
    "requires system access, politely explain that you cannot do it."
)


class OllamaError(Exception):
    pass


def _load_local_env() -> None:
    global _ENV_LOADED

    if _ENV_LOADED:
        return

    env_path = os.path.join(_BASE_DIR, ".env")
    if not os.path.isfile(env_path):
        _ENV_LOADED = True
        return

    try:
        with open(env_path, "r", encoding="utf-8") as file:
            for raw_line in file:
                line = raw_line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key and key not in os.environ:
                    os.environ[key] = value
    except OSError:
        pass

    _ENV_LOADED = True


def _to_bool(value: str, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _ollama_enabled() -> bool:
    _load_local_env()
    return _to_bool(os.getenv("OLLAMA_ENABLE", "true"), default=True)


def should_log_failures() -> bool:
    _load_local_env()
    return _to_bool(os.getenv("LOG_OLLAMA_FAILURES", "true"), default=True)


def _ollama_host() -> str:
    _load_local_env()
    return os.getenv("OLLAMA_HOST", "http://localhost:11434").strip() or "http://localhost:11434"


def _ollama_model() -> str:
    _load_local_env()
    return os.getenv("OLLAMA_MODEL", "mistral").strip() or "mistral"


def _ollama_timeout() -> float:
    _load_local_env()
    try:
        value = float(os.getenv("OLLAMA_TIMEOUT", "30"))
        return value if value > 0 else 30.0
    except ValueError:
        return 30.0


def _get_client():
    if Client is None:
        raise OllamaError("Python package 'ollama' is not installed.")

    host = _ollama_host()
    timeout = _ollama_timeout()

    try:
        return Client(host=host, timeout=timeout)
    except TypeError:
        # Backward compatibility for older client signatures.
        return Client(host=host)


def _extract_model_name(model_item) -> str:
    if isinstance(model_item, str):
        return model_item

    if isinstance(model_item, dict):
        return (
            model_item.get("model")
            or model_item.get("name")
            or ""
        )

    model_attr = getattr(model_item, "model", "")
    if model_attr:
        return str(model_attr)

    name_attr = getattr(model_item, "name", "")
    if name_attr:
        return str(name_attr)

    return ""


def _model_available(client, target_model: str) -> bool:
    response = client.list()

    if isinstance(response, dict):
        models = response.get("models") or []
    else:
        models = getattr(response, "models", []) or []

    names = []
    for item in models:
        name = _extract_model_name(item)
        if name:
            names.append(name)

    for name in names:
        if name == target_model or name.startswith(f"{target_model}:"):
            return True

    return False


def validate_ollama_startup() -> Tuple[bool, str]:
    if not _ollama_enabled():
        return False, "Ollama is disabled (OLLAMA_ENABLE=false)."

    try:
        client = _get_client()
        target_model = _ollama_model()

        if not _model_available(client, target_model):
            return False, f"Ollama is reachable, but model '{target_model}' is not available locally."

        return True, f"Connected to {_ollama_host()} with model '{target_model}'."
    except Exception as exc:
        return False, f"Ollama check failed: {exc}"


def get_ollama_setup_instructions() -> str:
    target_model = _ollama_model()
    return (
        "To enable Ollama fallback: install Ollama, run 'ollama serve', "
        f"and download the model with 'ollama pull {target_model}'."
    )


def _build_messages(user_input: str, conversation_history: List[Dict[str, str]]):
    messages = [{"role": "system", "content": _SYSTEM_PROMPT}]

    for item in conversation_history or []:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = str(item.get("content", "")).strip()
        if role in {"user", "assistant"} and content:
            messages.append({"role": role, "content": content})

    messages.append({"role": "user", "content": str(user_input).strip()})
    return messages


def _extract_chat_content(response) -> str:
    if isinstance(response, dict):
        message = response.get("message")
        if isinstance(message, dict):
            content = message.get("content")
            if content:
                return str(content).strip()

        content = response.get("response")
        if content:
            return str(content).strip()

    message = getattr(response, "message", None)
    if isinstance(message, dict):
        content = message.get("content")
        if content:
            return str(content).strip()
    elif message is not None:
        content = getattr(message, "content", None)
        if content:
            return str(content).strip()

    fallback = getattr(response, "response", None)
    if fallback:
        return str(fallback).strip()

    return ""


def query_ollama(user_input: str, conversation_history: List[Dict[str, str]]) -> str:
    if not _ollama_enabled():
        raise OllamaError("Ollama is disabled by environment configuration.")

    text = str(user_input).strip()
    if not text:
        raise OllamaError("User input is empty.")

    try:
        client = _get_client()
        response = client.chat(
            model=_ollama_model(),
            messages=_build_messages(text, conversation_history),
        )
    except Exception as exc:
        raise OllamaError(f"Ollama query failed: {exc}") from exc

    content = _extract_chat_content(response)
    if not content:
        raise OllamaError("Ollama returned an empty response.")

    return content
