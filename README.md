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
`pip install -r requirements.txt`

VIRTUAL ARDINO MODE: .\start.bat mock

Every time: plug in the Uno, then run `start.bat` (Windows) or `bash start.sh` (Mac/Linux).
It finds the Arduino's port by itself. To force one: `set PUMP_SERIAL_PORT=COM5`
(Windows) or `export PUMP_SERIAL_PORT=/dev/ttyACM0`.
Cheap Uno clones on Windows may need the **CH340 driver**.

Want to open the kiosk on a different device (e.g. the TV) while the laptop runs the
backend? Start with `PUMP_HOST=0.0.0.0` set, then browse to `http://<laptop-ip>:5005/`.

## D. AI drink generation (Groq or OmniRoute + ElevenLabs)

Create API keys in the [Groq console](https://console.groq.com/keys), your OmniRoute dashboard, and [ElevenLabs](https://elevenlabs.io/app/settings/api-keys).
Start the pump backend with `start.bat mock`, then choose a provider for chat and drink recipes separately in `Settings > System`.
Enter the relevant API key there and press `Save providers`. Keys are sent to the backend, never stored in browser storage, and are never returned to the page. The OmniRoute key and provider choices persist in `%LOCALAPPDATA%\PumpKiosk\ai_settings.json` on Windows (or `~/.config/PumpKiosk/ai_settings.json` elsewhere); the Groq and ElevenLabs keys remain in backend memory and clear when it restarts.

OmniRoute must be running locally at `http://localhost:20128`. The backend calls its OpenAI-compatible API at `http://localhost:20128/v1`; use the OmniRoute combo/model IDs shown in its dashboard. Each task can use a different provider and model.

Groq's `openai/gpt-oss-120b` model handles recipe reasoning; `openai/gpt-oss-20b` handles Coda's conversational text.
ElevenLabs `eleven_flash_v2_5` generates streamed audio and character timings for captions synchronized to the voice.
The default voice ID can be changed in `Settings > System`. Groq quotas vary by account; check the Groq limits page.
ElevenLabs currently lists 10,000 free credits per month, with text-to-speech charged per character.

Groq requests use system proxy settings by default. If a proxy is blocking them, set `GROQ_PROXY=direct` before
starting the backend to bypass environment proxies, or set `GROQ_PROXY=http://host:port` to use a specific proxy.
On Windows, run `set GROQ_PROXY=direct` in the same Command Prompt before `start.bat mock`.

`TALK` uses browser speech recognition for input. Replies use ElevenLabs audio, with captions following its timing data.
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
