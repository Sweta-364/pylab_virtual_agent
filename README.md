# Virtual-Assistant

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

## Push-To-Talk (Whisper)

- Click `ASK (Spacebar)` once to enter conversation mode.
- Hold `SPACE` to record, release to transcribe with Whisper.
- Whisper auto-detects language and translates to English before querying Ollama.
- While the assistant is speaking, press `SPACE` to interrupt speech immediately.
- The old text box + `Send` flow still works.

On first Whisper usage, the model may download and can take a minute depending on network speed.

## Filesystem Commands

JARVIS can now handle text-only filesystem operations without using TTS credits.

- Example commands:
  - `list files on desktop`
  - `list all files in /home including hidden`
  - `list only directories in /proc`
  - `create folder test_folder on desktop`
  - `create file notes.txt with content hello world on desktop`
  - `create folder hello with file new.txt inside on desktop`
  - `read ~/Desktop/notes.txt`
  - `update ~/Desktop/notes.txt with content updated text`
  - `append another line to ~/Desktop/notes.txt`
  - `delete file ~/Desktop/notes.txt`

- Notes:
  - Delete operations always require confirmation.
  - GUI `Yes` and `No` buttons appear for pending deletes.
  - Directory listings are shown as trees by default.
  - Large files are not loaded into memory; JARVIS shows the location or opens them with the system app when appropriate.
  - All filesystem operations are logged to `file_operations.log`.

## Security Notes

- Filesystem commands can access paths across your machine, including absolute paths from `/`.
- Relative parent traversal like `../..` is rejected in natural-language paths for safety.
- Review delete confirmations carefully before approving them.

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
- **Filesystem commands do not speak**:
  - This is intentional so file operations do not consume Sarvam TTS credits
