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
- Filesystem commands and handwriting generation fail gracefully instead of crashing the UI flow.

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
OLLAMA_PREWARM=false
LOG_OLLAMA_FAILURES=true
```

`run.sh` validates Ollama at startup and prints a warning if it is unavailable, but the app will still run.
Set `OLLAMA_PREWARM=true` if you want the configured model warmed up during startup to reduce the first-response delay.

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
- If Sarvam is unavailable, JARVIS tries the local OS speech engine (`say`, `spd-say`, `espeak`, `festival`, or Windows SAPI) before giving up silently.
- If Ollama is unavailable, the assistant falls back gracefully with a friendly message.

## Push-To-Talk (Whisper)

- Click `ASK (Spacebar)` once to enter conversation mode.
- Hold `SPACE` to record, release to transcribe with Whisper.
- Whisper auto-detects language and translates to English before querying Ollama.
- Audio validation now checks duration, RMS, and simple voice activity so silence/background noise are less likely to trigger false positives.
- While the assistant is speaking, press `SPACE` to interrupt speech with a short fade-out when direct audio playback is available.
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
  - `createfile notes.txt with content hello world on desktop`
  - `Jarvis, please create a file named notes.txt in documents`
  - `create folder hello with file new.txt inside on desktop`
  - `open downloads folder`
  - `read ~/Desktop/notes.txt`
  - `update ~/Desktop/notes.txt with content updated text`
  - `append another line to ~/Desktop/notes.txt`
  - `delete file ~/Desktop/notes.txt`

- Notes:
  - Delete operations always require confirmation.
  - GUI `Yes` and `No` buttons appear for pending deletes.
  - Directory listings are shown as trees by default.
  - File writes now use atomic temp-file replacement to reduce partial updates.
  - Deletes reject symlink targets and re-check the path immediately before deletion.
  - Large files use a 100 MB preview cap. Bigger text files can be streamed internally instead of being loaded all at once.
  - All filesystem operations are logged to `file_operations.log`.

## Handwriting Generation

- Use an explicit handwriting phrase to trigger handwriting image generation.
- `create a handwriting poem` triggers handwriting generation.
- `createfile notes.txt` creates a real file; it does not trigger handwriting generation.
- Generated images are saved to `./image/handwriting_<n>.jpg`.
- The assistant tries the real third-party `pywhatkit.text_to_handwriting(...)` API first.
- If PyWhatKit is unavailable, slow, or times out after 15 seconds, the assistant falls back to a local Pillow renderer.
- When handwriting finishes quickly enough, the response includes `Image saved to ./image/...`.

## Intent Routing

- Semantic filesystem routing now expects classifier confidence of `0.7` or higher before treating a request as an OS/filesystem operation.
- If Ollama is unavailable, strong local heuristics still route clear PC work like file creation/listing/deletion to the filesystem handler.
- Lower-confidence or ambiguous requests fall back to the normal assistant response path.
- Exact/direct filesystem commands still work without going through the confidence gate.

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
  - Direct PyAudio playback is preferred for smoother interruption; if that fails, install at least one local audio player on Linux: `aplay`, `paplay`, or `ffplay`
- **GUI does not open**:
  - Run from a desktop session with display access (Tkinter requires a working display)
- **Filesystem commands do not speak**:
  - This is intentional so file operations do not consume Sarvam TTS credits
- **Handwriting output is missing**:
  - Check the `image/` folder for `handwriting_<n>.jpg`
  - If PyWhatKit import or generation fails, the assistant should fall back locally instead of crashing
  - Very long text can make handwriting generation slower and may trigger the fallback path
