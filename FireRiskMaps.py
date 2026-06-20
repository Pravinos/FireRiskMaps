"""
fire_risk_map.py
----------------
Downloads the daily fire-risk forecast map from the Greek Civil Protection
website, classifies key locations by risk level (1-5) using perceptual CIELAB
colour distance, annotates the map with those levels, and plays a notification.

Designed to run as a silent background process on Windows. Starts at logon
via Windows Task Scheduler and fires the pipeline automatically every day
at the configured time (default 13:30).
"""

from __future__ import annotations

import os
import time
import datetime
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin

import httpx
import numpy as np
import schedule
from bs4 import BeautifulSoup
from loguru import logger
from PIL import Image, ImageDraw, ImageFont

# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------

_APPDATA = Path(os.getenv("APPDATA", "."))
_WINDIR  = Path(os.getenv("WINDIR",  r"C:\Windows"))


@dataclass
class Settings:
    # --- network ---
    base_url:       str = "https://civilprotection.gov.gr"
    maps_page_path: str = "/arxeio-imerision-xartwn"
    # Retries inside a single fetch attempt (transient HTTP errors only).
    # The outer scheduler retries the whole pipeline every 10 minutes.
    max_retries:    int = 10
    retry_delay:    int = 5    # seconds between inner retries

    # --- scheduling ---
    days_ahead:          int = 1        # 1 = tomorrow's forecast
    run_at:              str = "12:00"  # daily first attempt (HH:MM, 24-hour)
    retry_interval_min:  int = 10       # minutes between pipeline retries on failure

    # --- paths ---
    output_dir: Path = field(default_factory=lambda: Path(r"C:\FireRiskMaps\maps"))
    log_dir:    Path = field(default_factory=lambda: Path(r"C:\FireRiskMaps\logs"))
    font_path:  Path = field(default_factory=lambda: _WINDIR / "Fonts" / "arialbd.ttf")
    sound_path: Path = field(default_factory=lambda: Path(r"C:\FireRiskMaps\notification.mp3"))

    # --- image analysis ---
    font_size:     int = 20
    colour_window: int = 3     # pixel radius for robust colour sampling

    # One (x, y) pixel per risk level 1-5 on the legend colour strip
    reference_points: list = field(default_factory=lambda: [
        (137, 1296),
        (137, 1319),
        (137, 1337),
        (137, 1360),
        (137, 1385)
    ])

    # (x, y) pixels for the locations to classify
    key_points: list = field(default_factory=lambda: [
        (362, 368),
        (389, 385),
        (457, 523),
         (467, 562),
        (445, 610),
        (571, 701),
        (587, 753),
        (591, 1218),
        (501, 644),
        (628, 270),
        (543, 360)
    ])

    @property
    def maps_page_url(self) -> str:
        return self.base_url + self.maps_page_path


cfg = Settings()

# Greek month names for date formatting
GREEK_MONTHS = {
    1: "ΙΑΝΟΥΑΡΙΟΥ",
    2: "ΦΕΒΡΟΥΑΡΙΟΥ",
    3: "ΜΑΡΤΙΟΥ",
    4: "ΑΠΡΙΛΙΟΥ",
    5: "ΜΑΙΟΥ",
    6: "ΙΟΥΝΙΟΥ",
    7: "ΙΟΥΛΙΟΥ",
    8: "ΑΥΓΟΥΣΤΟΥ",
    9: "ΣΕΠΤΕΜΒΡΙΟΥ",
    10: "ΟΚΤΩΒΡΙΟΥ",
    11: "ΝΟΕΜΒΡΙΟΥ",
    12: "ΔΕΚΕΜΒΡΙΟΥ",
}

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def _setup_logging() -> None:
    cfg.log_dir.mkdir(parents=True, exist_ok=True)
    logger.add(
        cfg.log_dir / "fire_risk_{time:YYYY-MM-DD}.log",
        rotation="00:00",
        retention="30 days",
        level="INFO",
        format="{time:YYYY-MM-DD HH:mm:ss} [{level}] {message}",
    )

# ---------------------------------------------------------------------------
# Downloading
# ---------------------------------------------------------------------------

def _target_date() -> str:
    """Return YYMMDD for the configured forecast day."""
    return (datetime.date.today() + datetime.timedelta(days=cfg.days_ahead)).strftime("%y%m%d")


def _greek_date_filename(date_str: str) -> str:
    """
    Convert date string YYMMDD to "ΧΑΡΤΗΣ ΓΙΑ DD MONTH YY" format.
    Example: "260621" → "ΧΑΡΤΗΣ ΓΙΑ 21 ΙΟΥΝΙΟΥ 26"
    """
    if len(date_str) != 6:
        return date_str
    yy, mm, dd = date_str[:2], int(date_str[2:4]), date_str[4:6]
    month_name = GREEK_MONTHS.get(mm, "ΑΓΝΩΣΤΟΥ")
    return f"ΧΑΡΤΗΣ ΓΙΑ {dd} {month_name} {yy}"


def _fetch_map_url(client: httpx.Client) -> str | None:
    """Scrape the archive page; return the relative URL of the latest map."""
    try:
        resp = client.get(cfg.maps_page_url)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Failed to fetch maps page: {}", exc)
        return None

    soup = BeautifulSoup(resp.text, "lxml")
    container = soup.find("div", class_="col-6 col-md-4 col-lg-3")
    if container is None:
        logger.error("Container div not found — site layout may have changed.")
        return None

    anchor = container.find(href=True)
    if anchor is None:
        logger.error("No anchor found inside container div.")
        return None

    return anchor["href"]


def _download_map(client: httpx.Client, relative_url: str, dest: Path) -> bool:
    """Download the map image to *dest*; return True on success."""
    url = urljoin(cfg.base_url, relative_url)
    logger.info("Downloading: {}", url)

    try:
        resp = client.get(url)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.error("Download failed: {}", exc)
        return False

    content_type = resp.headers.get("content-type", "")
    if "image" not in content_type:
        logger.error("Unexpected content-type '{}' — skipping.", content_type)
        return False

    dest.write_bytes(resp.content)
    logger.info("Saved → {}", dest)
    return True


def fetch_map(date_str: str) -> Path | None:
    """
    Poll the archive page until the map for *date_str* appears, then download.
    Returns the local Path on success, or None after max retries.
    """
    cfg.output_dir.mkdir(parents=True, exist_ok=True)
    dest = cfg.output_dir / f"{_greek_date_filename(date_str)}.jpg"

    timeout = httpx.Timeout(connect=10.0, read=30.0, write=None, pool=None)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        for attempt in range(1, cfg.max_retries + 1):
            logger.info("Attempt {}/{} — looking for map {}", attempt, cfg.max_retries, date_str)
            url = _fetch_map_url(client)
            if url is None:
                # Page unreachable — transient error, short wait then retry
                if attempt < cfg.max_retries:
                    time.sleep(cfg.retry_delay)
                continue
            if date_str not in Path(url).stem:
                logger.info("Latest map ({}) does not match target date {}.",
                            Path(url).stem, date_str)
                return None   # map not published yet — let outer scheduler retry
            if _download_map(client, url, dest):
                return dest
            if attempt < cfg.max_retries:
                time.sleep(cfg.retry_delay)

    logger.error("Map could not be fetched after {} attempts.", cfg.max_retries)
    return None

# ---------------------------------------------------------------------------
# Colour analysis — CIELAB for perceptual accuracy (pure numpy, no extra deps)
# ---------------------------------------------------------------------------

def _srgb_to_lab(rgb: np.ndarray) -> np.ndarray:
    """Convert an (N, 3) uint8 RGB array to CIELAB (D65 illuminant)."""
    f = rgb.astype(np.float64) / 255.0
    # Remove sRGB gamma → linear light
    linear = np.where(f > 0.04045, ((f + 0.055) / 1.055) ** 2.4, f / 12.92)
    # Linear RGB → CIE XYZ (D65)
    M = np.array([
        [0.4124564, 0.3575761, 0.1804375],
        [0.2126729, 0.7151522, 0.0721750],
        [0.0193339, 0.1191920, 0.9503041],
    ])
    xyz = (linear @ M.T) / np.array([0.95047, 1.00000, 1.08883])
    # XYZ → L*a*b*
    fx = np.where(xyz > 0.008856, xyz ** (1.0 / 3.0), (903.3 * xyz + 16.0) / 116.0)
    L =  116.0 * fx[..., 1] - 16.0
    a =  500.0 * (fx[..., 0] - fx[..., 1])
    b =  200.0 * (fx[..., 1] - fx[..., 2])
    return np.stack([L, a, b], axis=-1)


def sample_colours(
    image_array: np.ndarray,
    points: list[tuple[int, int]],
    window: int = 3,
) -> np.ndarray:
    """
    Return the mean RGB colour in a square window around each point.
    Shape: (N, 3) float32.
    """
    h, w = image_array.shape[:2]
    half = window // 2
    colours = np.zeros((len(points), 3), dtype=np.float32)

    for i, (px, py) in enumerate(points):
        patch = image_array[
            max(0, py - half): min(py + half + 1, h),
            max(0, px - half): min(px + half + 1, w),
        ]
        colours[i] = patch.reshape(-1, 3).mean(axis=0)

    return colours


def classify_risk(key_rgb: np.ndarray, ref_rgb: np.ndarray) -> np.ndarray:
    """
    Classify each key colour to the nearest reference using CIELAB distance.
    Returns a 1-based risk-level array.
    """
    key_lab = _srgb_to_lab(key_rgb)
    ref_lab = _srgb_to_lab(ref_rgb)
    distances = np.linalg.norm(key_lab[:, np.newaxis] - ref_lab, axis=2)
    return np.argmin(distances, axis=1) + 1  # 1-based

# ---------------------------------------------------------------------------
# Annotation
# ---------------------------------------------------------------------------

def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    try:
        return ImageFont.truetype(str(path), size, encoding="unic")
    except (IOError, OSError):
        logger.warning("Font '{}' not found — using PIL default.", path)
        return ImageFont.load_default()


def annotate(
    image: Image.Image,
    points: list[tuple[int, int]],
    labels: np.ndarray,
) -> None:
    """
    Draw each risk label centred on its point over a semi-transparent white
    background patch for readability.  Mutates *image* in-place.
    """
    font = _load_font(cfg.font_path, cfg.font_size)
    draw = ImageDraw.Draw(image, "RGBA")

    for (px, py), label in zip(points, labels):
        text = str(label)
        bbox = draw.textbbox((0, 0), text, font=font)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
        draw.text((px - tw // 2, py - th // 2), text, font=font, fill=(0, 0, 0, 255))

# ---------------------------------------------------------------------------
# Notification
# ---------------------------------------------------------------------------

def _play_sound() -> None:
    """Open the configured sound file with the Windows default audio player."""
    if not cfg.sound_path.exists():
        logger.warning("Sound file not found: {}", cfg.sound_path)
        return
    try:
        os.startfile(str(cfg.sound_path))
    except Exception as exc:
        logger.warning("Could not play notification: {}", exc)


def _show_toast(risk_labels: list[int]) -> None:
    """Display a Windows balloon notification using PowerShell (no extra deps)."""
    import subprocess
    summary = ", ".join(str(r) for r in risk_labels)
    ps = (
        "Add-Type -AssemblyName System.Windows.Forms; "
        "$n = New-Object System.Windows.Forms.NotifyIcon; "
        "$n.Icon = [System.Drawing.SystemIcons]::Information; "
        "$n.Visible = $true; "
        f"$n.ShowBalloonTip(8000, 'Fire Risk Map', 'Risk levels: {summary}', "
        "[System.Windows.Forms.ToolTipIcon]::Info); "
        "Start-Sleep -Milliseconds 9000; $n.Dispose()"
    )
    try:
        subprocess.Popen(
            ["powershell", "-WindowStyle", "Hidden", "-Command", ps],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:
        logger.warning("Could not show toast notification: {}", exc)

# ---------------------------------------------------------------------------
# Daily pipeline
# ---------------------------------------------------------------------------

def run_daily_job() -> bool:
    """
    Full pipeline: download → analyse → annotate → save → notify.
    Returns True on success, False on any failure.
    """
    date_str = _target_date()
    logger.info("=== Pipeline attempt — target date {} ===", date_str)

    map_path = fetch_map(date_str)
    if map_path is None:
        logger.warning("Pipeline failed: map not yet available or download error.")
        return False

    try:
        with Image.open(map_path) as raw:
            image = raw.convert("RGB")

        arr = np.asarray(image)
        ref_colours = sample_colours(arr, cfg.reference_points, window=cfg.colour_window)
        key_colours = sample_colours(arr, cfg.key_points,       window=cfg.colour_window)
        risk_labels  = classify_risk(key_colours, ref_colours)
        logger.info("Risk levels: {}", risk_labels.tolist())

        annotate(image, cfg.key_points, risk_labels)
        image.save(map_path)
        logger.info("Annotated image saved → {}", map_path)
    except Exception as exc:
        logger.error("Pipeline failed during analysis/annotation: {}", exc)
        return False

    _play_sound()
    _show_toast(risk_labels.tolist())
    return True

# ---------------------------------------------------------------------------
# Entry point — background scheduler
# ---------------------------------------------------------------------------

def main() -> None:
    _setup_logging()
    logger.info(
        "FireRiskMap started. Daily pipeline at {}, retrying every {} min on failure.",
        cfg.run_at, cfg.retry_interval_min,
    )

    # Holds the single active retry job so it can be cancelled on success.
    _retry_job: list[schedule.Job] = []

    def _cancel_retries() -> None:
        for job in _retry_job:
            schedule.cancel_job(job)
        _retry_job.clear()

    def _retry_once() -> object:
        """Called every 10 minutes after a daily-trigger failure. Cancels on success."""
        if run_daily_job():
            logger.info("Pipeline succeeded on retry — retries cancelled until tomorrow.")
            _cancel_retries()
            return schedule.CancelJob
        return None

    def _daily_trigger() -> None:
        """Called once at 12:00 each day. Schedules 10-min retries if the first attempt fails."""
        _cancel_retries()   # clear any leftover retries from previous day
        logger.info("Daily trigger fired at {}.", cfg.run_at)
        if run_daily_job():
            logger.info("Pipeline succeeded on first attempt.")
        else:
            logger.info(
                "First attempt failed — scheduling retries every {} min.",
                cfg.retry_interval_min,
            )
            job = schedule.every(cfg.retry_interval_min).minutes.do(_retry_once)
            _retry_job.append(job)

    schedule.every().day.at(cfg.run_at).do(_daily_trigger)

    # ------------------------------------------------------------------
    # Startup attempt: try immediately on launch, then up to 5 more times
    # one hour apart.  If all fail, the normal 12:00 daily schedule takes
    # over from the next day onwards.
    # ------------------------------------------------------------------
    STARTUP_MAX_TRIES = 6        # 1 immediate + 5 hourly retries
    STARTUP_RETRY_HOURS = 1

    logger.info("Startup: running pipeline immediately (attempt 1/{}).", STARTUP_MAX_TRIES)
    if run_daily_job():
        logger.info("Startup attempt succeeded.")
    else:
        startup_attempts = [1]   # mutable counter accessible inside closure

        def _startup_retry() -> object:
            attempt = startup_attempts[0] + 1
            startup_attempts[0] = attempt
            logger.info("Startup retry {}/{}.", attempt, STARTUP_MAX_TRIES)
            if run_daily_job():
                logger.info("Startup retry {} succeeded — cancelling startup retries.", attempt)
                _cancel_retries()
                return schedule.CancelJob
            if attempt >= STARTUP_MAX_TRIES:
                logger.warning(
                    "All {} startup attempts failed. "
                    "Falling back to daily schedule at {}.",
                    STARTUP_MAX_TRIES, cfg.run_at,
                )
                _cancel_retries()
                return schedule.CancelJob
            return None

        logger.info(
            "Startup attempt failed — scheduling {} hourly retries.",
            STARTUP_MAX_TRIES - 1,
        )
        job = schedule.every(STARTUP_RETRY_HOURS).hours.do(_startup_retry)
        _retry_job.append(job)

    while True:
        schedule.run_pending()
        time.sleep(30)


if __name__ == "__main__":
    main()
