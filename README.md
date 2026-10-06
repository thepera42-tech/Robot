# AI Soda Dispenser — setup for the TV *and* the laptop

One project, three ways to talk to the Arduino. Pick with a word:

| Command | What it does | Use it on |
|---|---|---|
| `bash start.sh` | Simulated Arduino (default on the TV) | TV while developing |
| `bash start.sh termux` | REAL Arduino over USB through Termux | TV, with the Uno plugged in |
| `start.bat` (or `bash start.sh serial`) | REAL Arduino via a normal serial port, auto-detected | Friend's laptop |
| `bash start.sh probe` | Lists what USB devices the TV can see | TV, when debugging |

The badge in the top-left of the kiosk (and the pill on `/test`) always says which one you're in:
**SIMULATED**, **ARDUINO CONNECTED (termux/serial)**, or **ARDUINO NOT FOUND — reason**.

---

## A. One-time: put the sketch on the Uno

For the Uno with the 16x2 LCD, open and upload `pump_controller_uno_lcd/pump_controller_uno_lcd.ino`
in Arduino IDE (board: Arduino Uno). This updated pump sketch adds stop, heartbeat, LCD status, and
buzzer support. Use `pump_controller_uno.ino` only without the LCD or the new stop/buzzer features;
the original `pump_controller.ino` is for a Mega.
Test from Serial Monitor at 9600 baud: send `PING` and confirm `PONG ESTOP=0`, then send
`FIRE 1 500 80` and confirm `OK` followed by `DONE 1`.

Pins on the Uno: pumps 1-6 = pins 3,5,6,9,10,11 (speed control works),
pumps 7-9 = pins 2,4,7 (on/off only). Pumps go through MOSFET drivers with their own power
supply — never straight to an Arduino pin.

### Safety switch, buzzer, and flow-sensor expansion

The updated LCD sketch uses D8 for the emergency-stop switch's isolated normally-closed auxiliary contact and D12 for an active buzzer driver. The switch's other normally-closed pole must interrupt the pump-driver supply directly; D8 is only the reporting/lockout input. Wire the auxiliary contact between D8 and GND. With `INPUT_PULLUP`, an open contact (pressed switch or broken wire) trips and latches the stop. Reset only after releasing the switch and checking the hardware.

Do not connect a high-current buzzer directly to D12. Use a transistor/MOSFET driver sized for the buzzer, with a shared ground and any suppression component required by its datasheet. The stop switch and power-cut device must be rated for the pump supply and current.

The buzzer gives two short beeps when the backend first connects, one short beep when the flow-sensor Uno is detected, and one long beep on a controller/backend or sensor-board disconnect. Pressing the stop switch gives three fast beeps followed by one long beep. If no backend connects within five seconds of boot, the LCD changes from waiting to `BACKEND OFFLINE` and the buzzer sounds once.

For liquid-flow detection, upload `flow_sensor_uno.ino` to a second Uno R3 connected to the computer by its own USB cable. Connect up to nine digital pulse-output flow sensors to D2-D10, with sensor ground shared with the sensor Uno. Keep D0/D1 free for USB serial. Use sensors certified food-safe and compatible with your drinks, tubing, and pressure. Sensor pulse counts indicate liquid movement, not exact volume; calibrate each sensor before using volume estimates. The System diagnostics flag an active pump with no recent pulses after a short startup grace period.

The pump Uno uses 9600 baud; the flow-sensor Uno uses 115200 baud. The backend opens each with its matching rate.

On Windows, assign the two board COM ports explicitly in the same PowerShell window before launching:

```powershell
$env:PUMP_SERIAL_PORT = 'COM4'
$env:FLOW_SENSOR_PORT = 'COM5'
.\start.bat serial
```

Replace `COM4` and `COM5` with the ports shown in Device Manager. The pump controller and sensor Uno must use different ports. If you do not have the second Uno or flow sensors connected, leave `FLOW_SENSOR_PORT` unset; the dispenser still works, but cannot verify liquid flow.

## B. TV (Termux)

One-time setup:
1. Install the **Termux:API** app (from the same place you got Termux, e.g. F-Droid).
2. In Termux: `pkg install python libusb termux-api`
3. In Termux: `pip install flask flask-cors`

Every time:
1. Close ArduinoDroid (only one app can hold the Uno at a time).
2. Plug in the Uno, then run `bash start.sh termux`.
3. When Android shows a **USB permission popup, tap OK**.
4. Check the badge: it should say ARDUINO CONNECTED (termux).

Developing without the Uno? Just `bash start.sh` (simulated).

## C. Friend's laptop (Windows / Mac / Linux)

One-time: install Python (Windows: tick "Add Python to PATH"), then in this folder:
`python -m venv .venv` and install the base packages with `.venv/bin/python -m pip install -r requirements.txt` (Windows: `.venv\Scripts\python.exe -m pip install -r requirements.txt`). The launchers automatically use `.venv` or `venv` when present; otherwise they use the system Python.

For local Kokoro speech, install its optional packages into that same environment: `.venv/bin/python -m pip install kokoro soundfile` (Windows: `.venv\Scripts\python.exe -m pip install kokoro soundfile`). On Linux, install the system `espeak-ng` package as well. Kokoro and its PyTorch dependencies are intentionally optional because they are substantially larger than the base backend dependencies.

VIRTUAL ARDINO MODE: .\start.bat mock

Every time: plug in the Uno, then run `start.bat` (Windows) or `bash start.sh` (Mac/Linux).
It finds the Arduino's port by itself. To force one: `set PUMP_SERIAL_PORT=COM5`
(Windows) or `export PUMP_SERIAL_PORT=/dev/ttyACM0`.
Cheap Uno clones on Windows may need the **CH340 driver**.

Want to open the kiosk on a different device (e.g. the TV) while the laptop runs the
backend? Start with `PUMP_HOST=0.0.0.0` set, then browse to `http://<laptop-ip>:5005/`.

## D. Local conversation, reasoning, drinks, and voice

Pull the local conversation model once with `ollama pull qwen2.5:3b` and make sure Ollama is running at `http://127.0.0.1:11434`. In `Settings > System`, use **Casual conversation model (Ollama)** to choose any model Ollama has downloaded. Greetings and everyday chat always use this local model; those messages are not sent to Groq or OmniRoute. Up to eight recent casual turns are held in browser memory for conversational context and sent only to Ollama.

Requests that need tools, code, math/workspace output, web research, or other complex reasoning use the selected **Complex request provider / Reasoning model**. Drink recipes use the independently selected recipe provider and model. The kiosk routes these categories separately; recipe generation remains validated by the backend before any pour. The local model list is loaded from Ollama when settings are opened or when **Pull available models** is pressed.

Voice output can use **Kokoro-82M (local)** with a selectable Kokoro voice, or **ElevenLabs Flash (cloud)**. Kokoro is the default when its package is installed; otherwise the previous ElevenLabs default is retained. Install Kokoro in the same Python environment that runs the backend with `pip install kokoro soundfile` (and install `espeak-ng` with your OS package manager if it is not already present). Kokoro may download its voice/model weights on the first synthesis. Select ElevenLabs and configure its API key if you prefer cloud speech.

Typed casual replies, voice requests, and spoken summaries of complex requests use the selected voice. With local Kokoro, Ollama streams the full answer while an isolated Kokoro worker loads the voice model in parallel. Kokoro receives the entire reply in one synthesis call, preserving context and intonation across sentences; playback starts when that utterance is synthesized. Kokoro's predicted word timings drive captions where available. The local Ollama model is kept warm for 15 minutes after use to avoid repeated model-load delays. Kokoro audio is piped in memory and does not create per-reply audio files. The three-second prefetch setting applies to cloud voice.

For complex requests or cloud speech, create the relevant keys in the [Groq console](https://console.groq.com/keys), your OmniRoute dashboard, or [ElevenLabs](https://elevenlabs.io/app/settings/api-keys). Enter them in `Settings > System` and press `Save providers`. Keys are sent to the backend, never stored in browser storage, and never returned to the page. The OmniRoute key, provider selections, casual Ollama model, and voice selections persist in `%LOCALAPPDATA%\PumpKiosk\ai_settings.json` on Windows (or `~/.config/PumpKiosk/ai_settings.json` elsewhere); Groq and ElevenLabs keys remain in backend memory and clear when it restarts. Selected remote chat/recipe model IDs remain in this browser's kiosk settings.

OmniRoute must be running locally at `http://localhost:20128`. The backend calls its OpenAI-compatible API at `http://localhost:20128/v1`; use the OmniRoute combo/model IDs shown in its dashboard. Each task can use a different provider and model.

Groq's `openai/gpt-oss-120b` model is the default recipe reasoner; `openai/gpt-oss-20b` is the default complex-request model.
ElevenLabs `eleven_flash_v2_5` generates streamed audio and character timings for synchronized captions. Its voice ID can be changed in `Settings > System`. Groq quotas vary by account; check the Groq limits page.
ElevenLabs currently lists 10,000 free credits per month, with text-to-speech charged per character.

Groq requests use system proxy settings by default. If a proxy is blocking them, set `GROQ_PROXY=direct` before
starting the backend to bypass environment proxies, or set `GROQ_PROXY=http://host:port` to use a specific proxy.
On Windows, run `set GROQ_PROXY=direct` in the same Command Prompt before `start.bat mock`.

`TALK` uses browser speech recognition for input. Replies use the selected local or cloud voice; Kokoro audio uses end-of-response captions, while ElevenLabs captions follow its timing data.
Voice input still requires browser support and microphone permission. Recipes use at most three ingredients, 12 seconds
per pump, and 24 total pump-seconds; the proposed recipe must be explicitly confirmed before any pour. Exact density
requires calibrated pump flow and ingredient density data, which are not currently configured.

---

## Files

- `backend.py` — server + pump logic; the Arduino link is pluggable (mock / serial / termux)
- `usb_transport.py` — the Termux USB driver (genuine Uno = CDC; CH340 clones best-effort)
- `kiosk.html`, `test_mode.html` — the two pages
- `start.sh`, `start.bat` — launchers · `requirements.txt` — laptop dependencies
- `pump_controller_uno.ino` (Uno) / `pump_controller.ino` (Mega)
- `flow_sensor_uno.ino` (optional second Uno for flow pulses)

## If something's wrong

- **ARDUINO NOT FOUND** — the badge/log says why. Logs: `logs/server.log`.
- **TV: no USB devices** — check the OTG cable/adapter; close ArduinoDroid; replug.
- **TV: "isn't a chip this backend can drive"** — run `bash start.sh probe` and share the output.
- **"Address already in use"** — an old copy is running: `pkill -9 -f backend.py`.
  Stop with **Ctrl+C**, never Ctrl+Z.

---

## E. LCD display (16x2 I2C) with cat animation and stats

1. Upload `pump_controller_uno_lcd.ino` to the Uno (it replaces `pump_controller_uno.ino`; no extra library needed).
2. Wire the LCD backpack: GND, 5V, SDA -> A4, SCL -> A5. If you only see solid blocks, turn the blue contrast trimmer.
3. Start the backend as usual and open `http://localhost:5005/display` to configure everything
   (cat mood rules, tanks, stats pages, backlight, bar style). Settings are saved in `display_config.json`.
4. Layout is 1:3: 4 columns cat, 12 columns stats. Tank levels drop from pump run time x speed x flow (ml/s), so enter each pump's real flow.

Files: `display.py` (logic + routes), `display.html` (settings page), `pump_controller_uno_lcd.ino` (Arduino).

## Robot workspace, music and agent browser

When a drink is ordered the face shrinks and a **dock** (Workspace / Music / Browser) opens under it, beside the
recipe card. The **TOOLS** button opens it any time. Everything the robot says now goes through a tool-using agent
(`/agent/chat`), so voice or typed requests like these just work:

- "Play Shape of You" -> searches YouTube, plays it in the Music tab.  "Raise the volume" / "set it to 30" / "mute".
  Music ducks automatically while the robot talks or listens.
- "Graph sin x and x squared", "simulate a ball bouncing in 3D", "draw a circuit...", "coach me through this equation",
  "make a table of..." -> rendered in the Workspace tab by the full AURA engine (`aura.html`, also at `/aura`).
- "Look up ...", "open example.com", "download <url>" -> agent browser.

### Agent browser engines
| | lite (default, runs on the TV/Termux) | full (laptop/PC) |
|---|---|---|
| search / read pages / download files | yes | yes |
| live view, click, type, scroll, tabs | no | yes (real Chromium) |

Full engine: `pip install playwright && playwright install chromium`, then it is picked automatically.
TV + laptop: on the laptop run `BROWSER_HOST=0.0.0.0 BROWSER_TOKEN=secret python agent_browser.py`; on the TV set
`BROWSER_REMOTE=http://<laptop-ip>:5006 BROWSER_TOKEN=secret` before starting the backend. Downloads then land on the laptop.
The browser refuses private/LAN addresses unless `BROWSER_ALLOW_PRIVATE=1`.

### Volume
The YouTube player volume always works. Real device volume is used when available: Termux (`pkg install termux-api`),
Linux (`pactl`/`amixer`), macOS, Windows (`pip install pycaw`).

### Kiosk tips
Start Chrome with `--autoplay-policy=no-user-gesture-required` so music can start without a touch (otherwise a
"Tap to start" button appears). The dock needs internet for YouTube and for AURA's CDN libraries (Chart.js, KaTeX, Pyodide).
