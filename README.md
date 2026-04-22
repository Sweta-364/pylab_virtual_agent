# Virtual-Assistant-

## Screenshots

![project](https://user-images.githubusercontent.com/51821426/208213987-b66bfc6b-4dc5-43fe-9354-c247395a850f.jpg)

## Installation

```bash
pip install -r requirement.txt
```

## Run (one command)

```bash
./run.sh
```

- `run.sh` auto-loads local `.env`, uses local `venv`, and installs missing dependencies if needed.
- Hardcoded assistant commands continue to work exactly as before.
- If a query does not match those commands, Ollama is used as a fallback.

## Sarvam AI Voice Setup

Edit `.env`:

```bash
SARVAM_API_KEY=your_sarvam_api_key
SARVAM_TTS_LANGUAGE=en-IN
SARVAM_TTS_SPEAKER=shubh
SARVAM_TTS_MODEL=bulbul:v3
SARVAM_TTS_PACE=1.0
```

Change `SARVAM_TTS_LANGUAGE` to switch spoken voice language.
Examples: `en-IN`, `hi-IN`, `ta-IN`, `te-IN`, `bn-IN`, `mr-IN`, `gu-IN`, `kn-IN`, `ml-IN`, `pa-IN`, `od-IN`.

## Ollama Setup

1. Install Ollama from [https://ollama.com/download](https://ollama.com/download)
2. Start Ollama server:

```bash
ollama serve
```

3. Pull the model configured in `.env` (default `mistral`):

```bash
ollama pull mistral
```

4. Ensure `.env` contains:

```bash
OLLAMA_ENABLE=true
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODEL=mistral
OLLAMA_TIMEOUT=30
LOG_OLLAMA_FAILURES=true
```

`run.sh` validates Ollama at startup and prints a warning if it is unavailable, but the app will still run.

## Conversation Context

Configure conversation behavior in `.env`:

```bash
CONVERSATION_HISTORY_LIMIT=10
PERSIST_CONVERSATION_HISTORY=false
CONVERSATION_HISTORY_FILE=conversation_history.json
```

- `CONVERSATION_HISTORY_LIMIT` is in exchanges (user+assistant pairs).
- If persistence is `false`, history resets on restart.
- On `shutdown`/`quit`, in-session history is reset.

## Weather Config

Set your default weather city in `.env`:

```bash
WEATHER_CITY=Patna
```

## How It Works

- Known commands (`open google`, `youtube`, `weather`, `time now`, etc.) are handled by the existing command logic.
- Unknown queries go to Ollama (`mistral`) with conversation history.
- The returned text is spoken through Sarvam TTS in the language set by `SARVAM_TTS_LANGUAGE`.
- If Ollama is unavailable, the assistant falls back gracefully with a friendly message.

## Troubleshooting

- **Ollama warning at startup**:
  - Check `ollama serve` is running
  - Check `OLLAMA_HOST` in `.env`
  - Run `ollama pull <model>` for the configured model
- **No audio output**:
  - Verify `SARVAM_API_KEY` in `.env`
  - Install at least one local audio player on Linux: `aplay`, `paplay`, or `ffplay`
- **GUI does not open**:
  - Run from a desktop session with display access (Tkinter requires a working display)
