# FireRiskMap

Automatically downloads the daily fire-risk forecast map from the Greek Civil Protection website, classifies 9 key locations by risk level (1–5), annotates the map image, and sends a Windows notification with sound.

---

## What it does

1. **At launch** — runs the pipeline immediately
2. **If that fails** — retries every hour for up to 5 more attempts
3. **Every day at 12:00** — runs the pipeline again
4. **If the 12:00 attempt fails** — retries every 10 minutes until success
5. **On success** — saves the annotated map, plays a sound, shows a Windows balloon notification

---

## Output

| What | Location |
|---|---|
| Annotated maps (`.jpg`) | `C:\FireRiskMaps\maps\` |
| Daily log files | `C:\FireRiskMaps\logs\` |
| Notification sound | `C:\FireRiskMaps\notification.mp3` |

Map files are named by date: `YYMMDD.jpg` (e.g. `260513.jpg`).
Logs rotate daily and are kept for 30 days.

---

## Setup

### Requirements
- Windows 10/11
- Python 3.10+ (only needed to build the exe)

### 1. Install dependencies and build the exe
```cmd
cd C:\FireRiskMaps
build.bat
```
Produces `dist\FireRiskMap.exe` — a self-contained executable, no Python needed on the target machine.

### 2. Deploy to a machine
1. Create `C:\FireRiskMaps\`
2. Copy `notification.mp3` → `C:\FireRiskMaps\notification.mp3`
3. Copy `dist\FireRiskMap.exe` → anywhere (e.g. `C:\FireRiskMaps\FireRiskMap.exe`)
4. Copy `install_startup.ps1` to the same machine

### 3. Register as a startup task (run once, as Administrator)
```powershell
powershell -ExecutionPolicy Bypass -File install_startup.ps1
```
The exe starts automatically at every logon and runs silently in the background.

---

## Managing the scheduled task

```powershell
# Start manually
Start-ScheduledTask -TaskName "FireRiskMap"

# Stop
Stop-ScheduledTask -TaskName "FireRiskMap"

# Remove
Unregister-ScheduledTask -TaskName "FireRiskMap" -Confirm:$false
```

---

## Configuration

All settings are in the `Settings` dataclass at the top of `FireRiskMaps.py`:

| Setting | Default | Description |
|---|---|---|
| `run_at` | `"12:00"` | Daily trigger time (HH:MM, 24-hour) |
| `days_ahead` | `1` | Forecast day offset (1 = tomorrow) |
| `retry_interval_min` | `10` | Minutes between retries after a failed daily attempt |
| `max_retries` | `10` | Inner HTTP retry attempts per pipeline run |
| `font_size` | `20` | Size of risk-level labels drawn on the map |

After changing any setting, rebuild with `build.bat`.

---

## Project files

| File | Purpose |
|---|---|
| `FireRiskMaps.py` | Main application source |
| `requirements.txt` | Python dependencies |
| `build.bat` | Builds `FireRiskMap.exe` using PyInstaller |
| `install_startup.ps1` | Registers the exe with Windows Task Scheduler |
