#!/usr/bin/env python3
"""Read a QALCOSONIC W1 LCD using a fixed USB camera and publish to HA."""

from __future__ import annotations

import json
import os
from pathlib import Path
import signal
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import cv2
import numpy as np


DEVICE = os.getenv("CAMERA_DEVICE", "/dev/video0")
INTERVAL_SECONDS = int(os.getenv("INTERVAL_SECONDS", "300"))
LISTEN_HOST = os.getenv("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.getenv("LISTEN_PORT", "8080"))
HA_URL = os.getenv("HA_URL", "").rstrip("/")
HA_TOKEN = os.getenv("HA_TOKEN", "")
DATA_DIR = Path(os.getenv("DATA_DIR", "/var/lib/water-meter-ocr"))

# Calibrated for the fixed LifeCam position. Coordinates are x1, y1, x2, y2.
DIGIT_BOXES = [
    (468, 297, 505, 364),
    (511, 295, 543, 363),
    (551, 297, 582, 357),
    (587, 298, 622, 357),
    (624, 297, 662, 361),
    (660, 297, 697, 357),
    (704, 310, 727, 364),
    (730, 311, 755, 359),
    (757, 310, 785, 357),
]

LARGE_SAMPLE_REGIONS = {
    "a": (25, 75, 5, 25),
    "b": (75, 95, 25, 85),
    "c": (75, 95, 115, 175),
    "d": (25, 75, 175, 195),
    "e": (5, 25, 115, 175),
    "f": (5, 25, 25, 85),
    "g": (25, 75, 90, 110),
}

SMALL_SAMPLE_REGIONS = {
    "a": (20, 80, 5, 25),
    "b": (70, 95, 25, 85),
    "c": (70, 95, 115, 175),
    "d": (20, 80, 170, 195),
    "e": (5, 30, 115, 175),
    "f": (5, 30, 25, 85),
    "g": (20, 80, 90, 110),
}

SEGMENT_DIGITS = {
    frozenset("abcdef"): "0",
    frozenset("bc"): "1",
    frozenset("abdeg"): "2",
    frozenset("abcdg"): "3",
    frozenset("bcfg"): "4",
    frozenset("acdfg"): "5",
    frozenset("acdefg"): "6",
    frozenset("abc"): "7",
    frozenset("abcdefg"): "8",
    frozenset("abcdfg"): "9",
}
MIN_PATTERN_CONFIDENCE = 2.0
MIN_CONSENSUS_CONFIDENCE = 1.5
METER_MAX_FLOW_M3_PER_HOUR = 4.0
FLOW_RATE_SAFETY_FACTOR = 1.5
MIN_ALLOWED_DELTA_M3 = 0.005
NIGHT_MIN_INTERVAL_SECONDS = max(
    1800, int(os.getenv("NIGHT_MIN_INTERVAL_SECONDS", "1800"))
)
DARK_FRAME_MEAN_THRESHOLD = float(os.getenv("DARK_FRAME_MEAN_THRESHOLD", "35"))

STATE_LOCK = threading.Lock()
CAMERA_LOCK = threading.Lock()
STOP_EVENT = threading.Event()
MEASURE_WAKEUP_EVENT = threading.Event()
INTERVAL_FILE = DATA_DIR / "interval.txt"
CAMERA_SETTINGS_FILE = DATA_DIR / "camera_settings.json"
CAMERA_SETTINGS_LOCK = threading.Lock()
ALIGNMENT_REFERENCE_FILE = DATA_DIR / "alignment_reference.jpg"
ALIGNMENT_REFERENCE_LOCK = threading.Lock()
ALIGNMENT_REFERENCE = None
CAMERA_SETTINGS = {
    "focus": 20,
    "brightness": 133,
    "exposure_auto": True,
    "exposure_time": 600,
}
STATE = {
    "status": "starting",
    "value": None,
    "raw": None,
    "unit": "m³",
    "updated_at": None,
    "last_success_at": None,
    "confidence": None,
    "interval": INTERVAL_SECONDS,
    "effective_interval": INTERVAL_SECONDS,
    "night_protection": False,
    "night_retry_after": None,
    "camera": dict(CAMERA_SETTINGS),
    "alignment": {
        "status": "starting",
        "reference": ALIGNMENT_REFERENCE_FILE.name,
    },
    "error": None,
}


class FrameTooDarkError(RuntimeError):
    """Raised when the camera image is too dark for a reliable reading."""

    def __init__(self, brightness: float) -> None:
        self.brightness = brightness
        super().__init__(
            f"Bild zu dunkel (Helligkeit {brightness:.1f}, "
            f"Minimum {DARK_FRAME_MEAN_THRESHOLD:.1f})"
        )


def get_interval() -> int:
    with STATE_LOCK:
        return INTERVAL_SECONDS


def get_effective_interval(night_protection: bool = False) -> int:
    """Return the configured interval with the mandatory night minimum."""
    interval = get_interval()
    return max(interval, NIGHT_MIN_INTERVAL_SECONDS) if night_protection else interval


def set_interval(seconds: int) -> int:
    global INTERVAL_SECONDS
    seconds = max(10, min(86400, int(seconds)))
    with STATE_LOCK:
        INTERVAL_SECONDS = seconds
        STATE["interval"] = seconds
    try:
        atomic_write(INTERVAL_FILE, str(seconds).encode("utf-8"))
    except Exception as exc:
        print(f"Fehler beim Speichern des Intervalls: {exc}", flush=True)
    return seconds


def load_interval() -> int:
    global INTERVAL_SECONDS
    if INTERVAL_FILE.exists():
        try:
            val = int(INTERVAL_FILE.read_text(encoding="utf-8").strip())
            if 10 <= val <= 86400:
                INTERVAL_SECONDS = val
        except Exception:
            pass
    with STATE_LOCK:
        STATE["interval"] = INTERVAL_SECONDS
    return INTERVAL_SECONDS


def get_camera_settings() -> dict:
    with CAMERA_SETTINGS_LOCK:
        return dict(CAMERA_SETTINGS)


def load_camera_settings() -> dict:
    if CAMERA_SETTINGS_FILE.exists():
        try:
            saved = json.loads(CAMERA_SETTINGS_FILE.read_text(encoding="utf-8"))
            update_camera_settings(saved, persist=False, wake=False)
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            print(f"Kameraeinstellungen konnten nicht geladen werden: {exc}", flush=True)
    settings = get_camera_settings()
    with STATE_LOCK:
        STATE["camera"] = settings
    return settings


def update_camera_settings(updates: dict, *, persist: bool = True, wake: bool = True) -> dict:
    validated = {}
    if "focus" in updates:
        validated["focus"] = max(0, min(40, int(updates["focus"])))
    if "brightness" in updates:
        validated["brightness"] = max(30, min(255, int(updates["brightness"])))
    if "exposure_auto" in updates:
        value = updates["exposure_auto"]
        if isinstance(value, str):
            value = value.strip().lower() in ("1", "true", "on", "yes")
        validated["exposure_auto"] = bool(value)
    if "exposure_time" in updates:
        validated["exposure_time"] = max(1, min(10000, int(updates["exposure_time"])))
    if not validated:
        raise ValueError("Keine gültigen Kameraeinstellungen übergeben")

    with CAMERA_SETTINGS_LOCK:
        CAMERA_SETTINGS.update(validated)
        settings = dict(CAMERA_SETTINGS)
    with STATE_LOCK:
        STATE["camera"] = settings
    if persist:
        atomic_write(
            CAMERA_SETTINGS_FILE,
            json.dumps(settings, indent=2).encode("utf-8"),
        )
    if wake:
        MEASURE_WAKEUP_EVENT.set()
    return settings


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def alignment_mask(shape: tuple[int, ...]) -> np.ndarray:
    """Mask stable meter features while excluding the changing LCD digits."""
    height, width = shape[:2]
    mask = np.zeros((height, width), dtype=np.uint8)
    cv2.rectangle(
        mask,
        (int(width * 0.25), int(height * 0.07)),
        (int(width * 0.78), int(height * 0.88)),
        255,
        -1,
    )
    cv2.rectangle(
        mask,
        (int(width * 0.32), int(height * 0.37)),
        (int(width * 0.70), int(height * 0.62)),
        0,
        -1,
    )
    return mask


def alignment_features(frame: np.ndarray):
    """Extract lighting-tolerant ORB features from the fixed meter housing."""
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    enhanced = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(gray)
    orb = cv2.ORB_create(nfeatures=1800, fastThreshold=10)
    return orb.detectAndCompute(enhanced, alignment_mask(frame.shape))


def load_alignment_reference() -> np.ndarray | None:
    """Load and cache the canonical camera frame used by the OCR boxes."""
    global ALIGNMENT_REFERENCE
    with ALIGNMENT_REFERENCE_LOCK:
        if ALIGNMENT_REFERENCE is None and ALIGNMENT_REFERENCE_FILE.exists():
            reference = cv2.imread(str(ALIGNMENT_REFERENCE_FILE), cv2.IMREAD_COLOR)
            if reference is not None:
                ALIGNMENT_REFERENCE = reference
        return ALIGNMENT_REFERENCE


def save_alignment_reference(frame: np.ndarray) -> np.ndarray:
    """Persist the first canonical frame for future camera alignment."""
    global ALIGNMENT_REFERENCE
    ok, encoded = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 96])
    if not ok:
        raise RuntimeError("Ausrichtungsreferenz konnte nicht gespeichert werden")
    atomic_write(ALIGNMENT_REFERENCE_FILE, encoded.tobytes())
    with ALIGNMENT_REFERENCE_LOCK:
        ALIGNMENT_REFERENCE = frame.copy()
    return ALIGNMENT_REFERENCE


def estimate_alignment(
    current: np.ndarray, reference: np.ndarray
) -> tuple[np.ndarray, dict]:
    """Estimate a safe similarity transform from the current frame to reference."""
    reference_points, reference_descriptors = alignment_features(reference)
    current_points, current_descriptors = alignment_features(current)
    if reference_descriptors is None or current_descriptors is None:
        raise RuntimeError("Zu wenige Merkmale für die Kameraausrichtung")

    matcher = cv2.BFMatcher(cv2.NORM_HAMMING)
    pairs = matcher.knnMatch(current_descriptors, reference_descriptors, k=2)
    good = [
        first
        for pair in pairs
        if len(pair) == 2
        for first, second in [pair]
        if first.distance < 0.72 * second.distance
    ]
    if len(good) < 12:
        raise RuntimeError(f"Nur {len(good)} sichere Ausrichtungsmerkmale gefunden")

    source = np.float32([current_points[item.queryIdx].pt for item in good])
    target = np.float32([reference_points[item.trainIdx].pt for item in good])
    matrix, inlier_mask = cv2.estimateAffinePartial2D(
        source,
        target,
        method=cv2.RANSAC,
        ransacReprojThreshold=2.5,
        maxIters=3000,
        confidence=0.995,
        refineIters=20,
    )
    if matrix is None or inlier_mask is None:
        raise RuntimeError("Kamerabewegung konnte nicht berechnet werden")

    inliers = inlier_mask.ravel().astype(bool)
    inlier_count = int(inliers.sum())
    inlier_ratio = inlier_count / len(good)
    if inlier_count < 10 or inlier_ratio < 0.35:
        raise RuntimeError(
            f"Kameraausrichtung unsicher ({inlier_count}/{len(good)} Merkmale)"
        )

    transformed = cv2.transform(source.reshape(-1, 1, 2), matrix).reshape(-1, 2)
    mean_error = float(np.linalg.norm(transformed[inliers] - target[inliers], axis=1).mean())
    scale = float(np.hypot(matrix[0, 0], matrix[1, 0]))
    rotation = float(np.degrees(np.arctan2(matrix[1, 0], matrix[0, 0])))
    height, width = current.shape[:2]
    center = np.float32([[[width / 2, height / 2]]])
    aligned_center = cv2.transform(center, matrix)[0, 0]
    shift_x = float(aligned_center[0] - width / 2)
    shift_y = float(aligned_center[1] - height / 2)

    if not 0.94 <= scale <= 1.06:
        raise RuntimeError(f"Kameraskalierung außerhalb des Limits ({scale:.3f})")
    if abs(rotation) > 4.0:
        raise RuntimeError(f"Kameradrehung außerhalb des Limits ({rotation:.2f} Grad)")
    if np.hypot(shift_x, shift_y) > 80:
        raise RuntimeError(
            f"Kameraverschiebung außerhalb des Limits ({shift_x:.1f}, {shift_y:.1f} px)"
        )

    details = {
        "status": "aligned",
        "reference": ALIGNMENT_REFERENCE_FILE.name,
        "shift_x": round(shift_x, 2),
        "shift_y": round(shift_y, 2),
        "rotation": round(rotation, 3),
        "scale": round(scale, 4),
        "inliers": inlier_count,
        "matches": len(good),
        "quality": round(inlier_ratio, 3),
        "mean_error": round(mean_error, 3),
    }
    return matrix, details


def align_frames(frames: list[np.ndarray]) -> tuple[list[np.ndarray], dict]:
    """Align one capture burst to the canonical frame before OCR."""
    reference = load_alignment_reference()
    if reference is None:
        reference = save_alignment_reference(frames[len(frames) // 2])
        return frames, {
            "status": "reference_created",
            "reference": ALIGNMENT_REFERENCE_FILE.name,
            "shift_x": 0.0,
            "shift_y": 0.0,
            "rotation": 0.0,
            "scale": 1.0,
            "quality": 1.0,
        }
    if reference.shape != frames[0].shape:
        raise RuntimeError(
            f"Ausrichtungsreferenz hat falsche Größe {reference.shape}, erwartet {frames[0].shape}"
        )

    candidates = [frames[len(frames) // 2], frames[-1], frames[0]]
    estimates = []
    errors = []
    for candidate in candidates:
        try:
            matrix, details = estimate_alignment(candidate, reference)
            estimates.append((details["inliers"], -details["mean_error"], matrix, details))
        except RuntimeError as exc:
            errors.append(str(exc))
    if not estimates:
        raise RuntimeError(f"Kameraausrichtung fehlgeschlagen: {'; '.join(errors)}")

    _, _, matrix, details = max(estimates, key=lambda item: item[:2])
    height, width = reference.shape[:2]
    aligned = [
        cv2.warpAffine(
            frame,
            matrix,
            (width, height),
            flags=cv2.INTER_LINEAR,
            borderMode=cv2.BORDER_REPLICATE,
        )
        for frame in frames
    ]
    return aligned, details


def configure_camera() -> None:
    settings = get_camera_settings()
    controls = [
        "power_line_frequency=1",
        f"brightness={settings['brightness']}",
        "focus_automatic_continuous=0",
        f"focus_absolute={settings['focus']}",
        f"auto_exposure={3 if settings['exposure_auto'] else 1}",
    ]
    if not settings["exposure_auto"]:
        controls.append(f"exposure_time_absolute={settings['exposure_time']}")
    for control in controls:
        subprocess.run(
            ["v4l2-ctl", "-d", DEVICE, f"--set-ctrl={control}"],
            check=True,
            capture_output=True,
            text=True,
        )
        time.sleep(0.2)


def capture_frames() -> tuple[list[np.ndarray], dict]:
    with CAMERA_LOCK:
        camera = cv2.VideoCapture(DEVICE, cv2.CAP_V4L2)
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
        configure_camera()
        frames = []
        try:
            # Auto exposure needs roughly two seconds after opening the LifeCam,
            # especially when the utility room changes from daylight to darkness.
            for index in range(90):
                ok, frame = camera.read()
                if not ok:
                    continue
                if index >= 75:
                    frames.append(frame)
        finally:
            camera.release()
        if len(frames) < 3:
            raise RuntimeError(f"Nur {len(frames)} Kamerabilder empfangen")
        ambient_brightness = float(
            np.median(
                [
                    cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY).mean()
                    for frame in frames
                ]
            )
        )
        if ambient_brightness < DARK_FRAME_MEAN_THRESHOLD:
            raise FrameTooDarkError(ambient_brightness)
        aligned, details = align_frames(frames)
        details["ambient_brightness"] = round(ambient_brightness, 1)
        return aligned, details


def segment_scores(frame: np.ndarray) -> list[dict[str, float]]:
    gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
    result = []
    for index, (x1, y1, x2, y2) in enumerate(DIGIT_BOXES):
        digit = cv2.resize(gray[y1:y2, x1:x2], (100, 200), interpolation=cv2.INTER_CUBIC)
        baseline = float(np.percentile(digit, 70))
        regions = LARGE_SAMPLE_REGIONS if index < 6 else SMALL_SAMPLE_REGIONS
        scores = {}
        for name, (sx1, sx2, sy1, sy2) in regions.items():
            sample = digit[sy1:sy2, sx1:sx2]
            scores[name] = baseline - float(np.percentile(sample, 20))
        result.append(scores)
    return result


def classify_digit(digit_scores: dict[str, float]) -> tuple[str, float]:
    """Classify one digit by comparing complete seven-segment patterns."""
    ranked = []
    for pattern, digit in SEGMENT_DIGITS.items():
        active = [digit_scores[name] for name in pattern]
        inactive = [
            digit_scores[name] for name in "abcdefg" if name not in pattern
        ]
        # Small LCD digits have uneven segment contrast. The mean separation
        # carries most of the decision while the edge separation still
        # penalizes missing required segments and strong unexpected segments.
        inactive_mean = sum(inactive) / len(inactive) if inactive else 5.0
        inactive_max = max(inactive) if inactive else 5.0
        mean_fit = sum(active) / len(active) - inactive_mean
        edge_fit = min(active) - inactive_max
        fit = 0.9 * mean_fit + 0.1 * edge_fit
        ranked.append((fit, digit))

    ranked.sort(key=lambda item: item[0], reverse=True)
    confidence = ranked[0][0] - ranked[1][0]
    return ranked[0][1], confidence


def decode_scores(
    scores: list[dict[str, float]],
    minimum_confidence: float = MIN_PATTERN_CONFIDENCE,
) -> tuple[str, float]:
    """Decode all positions and reject weak pattern matches."""
    digits = []
    confidences = []
    for digit_scores in scores:
        digit, confidence = classify_digit(digit_scores)
        digits.append(digit)
        confidences.append(confidence)

    confidence = round(min(confidences), 2)
    if confidence < minimum_confidence:
        raise ValueError(
            f"Sieben-Segment-Muster nicht eindeutig (Sicherheit {confidence:.2f})"
        )
    raw = "".join(digits[:6]) + "." + "".join(digits[6:])
    return raw, confidence


def decode_frame(frame: np.ndarray) -> tuple[str, float, list[dict[str, float]]]:
    scores = segment_scores(frame)
    raw, confidence = decode_scores(scores)
    return raw, confidence, scores


def median_scores(score_sets: list[list[dict[str, float]]]) -> list[dict[str, float]]:
    """Build an outlier-resistant segment profile for one capture burst."""
    return [
        {
            segment: float(
                np.median([scores[index][segment] for scores in score_sets])
            )
            for segment in "abcdefg"
        }
        for index in range(len(DIGIT_BOXES))
    ]


def annotate(frame: np.ndarray, raw: str, confidence: float) -> np.ndarray:
    output = frame.copy()
    for index, (x1, y1, x2, y2) in enumerate(DIGIT_BOXES):
        cv2.rectangle(output, (x1, y1), (x2, y2), (0, 210, 0), 1)
        digit = raw.replace(".", "")[index]
        cv2.putText(output, digit, (x1, y1 - 4), cv2.FONT_HERSHEY_SIMPLEX, 0.4, (0, 210, 0), 1)
    cv2.putText(
        output,
        f"Wasser: {raw} m3  Sicherheit: {confidence:.1f}",
        (24, 40),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.75,
        (0, 220, 0),
        2,
    )
    return output


def load_previous_reading() -> tuple[float | None, datetime | None]:
    path = DATA_DIR / "status.json"
    if not path.exists():
        return None, None
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        value = state.get("value")
        timestamp = state.get("last_success_at") or state.get("updated_at")
        parsed_timestamp = datetime.fromisoformat(timestamp) if timestamp else None
        return (None if value is None else float(value), parsed_timestamp)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None, None


def validate_value(
    value: float,
    previous: float | None,
    previous_timestamp: datetime | None,
    current_timestamp: datetime,
) -> None:
    if previous is None:
        return
    if value + 0.001 < previous:
        raise ValueError(f"Zähler würde rückwärts springen: {previous:.3f} -> {value:.3f}")
    if previous_timestamp is None:
        max_delta = 0.1
    else:
        elapsed_seconds = max(
            1.0, (current_timestamp - previous_timestamp).total_seconds()
        )
        max_delta = max(
            MIN_ALLOWED_DELTA_M3,
            METER_MAX_FLOW_M3_PER_HOUR
            * elapsed_seconds
            / 3600.0
            * FLOW_RATE_SAFETY_FACTOR,
        )
    delta = value - previous
    if delta > max_delta:
        raise ValueError(
            f"Durchfluss physikalisch unplausibel: {previous:.3f} -> {value:.3f} "
            f"({delta * 1000:.1f} L, erlaubt {max_delta * 1000:.1f} L)"
        )


def select_stable_reading(
    readings: list[tuple[str, float, np.ndarray]],
) -> tuple[str, float, np.ndarray, dict[str, int]]:
    """Select a clearly dominant reading from one camera burst."""
    counts: dict[str, int] = {}
    for raw, _, _ in readings:
        counts[raw] = counts.get(raw, 0) + 1

    ranked = sorted(counts.items(), key=lambda item: item[1], reverse=True)
    raw, top_count = ranked[0]
    second_count = ranked[1][1] if len(ranked) > 1 else 0
    valid_count = len(readings)
    share = top_count / valid_count

    if top_count < 3:
        raise RuntimeError(f"Keine stabile Mehrheitslesung: {counts}")
    if second_count and (share < 0.65 or top_count - second_count < 2):
        raise RuntimeError(
            f"Mehrheitslesung nicht eindeutig ({top_count}/{valid_count}, "
            f"Abstand {top_count - second_count}): {counts}"
        )

    matching = [item for item in readings if item[0] == raw]
    selected_raw, confidence, best_frame = max(matching, key=lambda item: item[1])
    return selected_raw, confidence, best_frame, counts


def ha_set_state(entity_id: str, state: str, attributes: dict) -> None:
    if not HA_URL or not HA_TOKEN:
        return
    body = json.dumps({"state": state, "attributes": attributes}).encode("utf-8")
    request = Request(
        f"{HA_URL}/api/states/{entity_id}",
        data=body,
        method="POST",
        headers={"Authorization": f"Bearer {HA_TOKEN}", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=10) as response:
        if response.status not in (200, 201):
            raise RuntimeError(f"Home Assistant antwortet mit HTTP {response.status}")


def publish_success(value: float, raw: str, confidence: float, timestamp: str) -> None:
    attributes = {
        "friendly_name": "Wasserzähler Zählerstand",
        "unit_of_measurement": "m³",
        "device_class": "water",
        "state_class": "total_increasing",
        "source": "wasserzaehler-ocr (LXC 121)",
        "raw_display": raw,
        "confidence_margin": confidence,
        "last_read": timestamp,
    }
    # Keep the original entity as an alias while publishing the entity name
    # expected by the existing Home Assistant dashboards and automations.
    ha_set_state("sensor.watermeter_value", f"{value:.3f}", attributes)
    ha_set_state("sensor.wasserzaehler_zaehlerstand", f"{value:.3f}", attributes)
    ha_set_state(
        "binary_sensor.wasserzaehler_ocr_status",
        "on",
        {
            "friendly_name": "Wasserzähler OCR Status",
            "device_class": "connectivity",
            "last_read": timestamp,
            "last_error": "",
        },
    )


WEBHOOK_FILE = DATA_DIR / "webhook_url.txt"


def get_webhook_url() -> str:
    if WEBHOOK_FILE.exists():
        try:
            return WEBHOOK_FILE.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return os.getenv("WEBHOOK_URL", "")


def set_webhook_url(url: str) -> None:
    try:
        WEBHOOK_FILE.write_text(url.strip(), encoding="utf-8")
    except Exception as exc:
        print(f"Fehler beim Speichern der Webhook-URL: {exc}", flush=True)


def push_webhook(payload: dict) -> None:
    webhook_url = get_webhook_url()
    if not webhook_url:
        return
    try:
        req = Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        with urlopen(req, timeout=5) as response:
            pass
    except Exception as exc:
        print(f"Webhook-Push fehlgeschlagen ({webhook_url}): {exc}", flush=True)


def publish_error(message: str, timestamp: str) -> None:
    with STATE_LOCK:
        payload = dict(STATE)
    push_webhook(payload)
    try:
        ha_set_state(
            "binary_sensor.wasserzaehler_ocr_status",
            "off",
            {
                "friendly_name": "Wasserzähler OCR Status",
                "device_class": "connectivity",
                "last_error": message,
                "last_attempt": timestamp,
            },
        )
    except Exception:
        pass


def measure_once() -> str:
    timestamp = utc_now()
    current_timestamp = datetime.fromisoformat(timestamp)
    previous, previous_timestamp = load_previous_reading()
    alignment = None
    try:
        frames, alignment = capture_frames()
        readings = []
        score_sets = []
        for frame in frames:
            scores = segment_scores(frame)
            score_sets.append(scores)
            try:
                raw, confidence = decode_scores(scores)
                readings.append((raw, confidence, frame))
            except ValueError:
                continue
        if not readings:
            raise RuntimeError("Keine der Aufnahmen konnte sicher gelesen werden")

        raw, confidence, best_frame, counts = select_stable_reading(readings)
        consensus_raw, consensus_confidence = decode_scores(
            median_scores(score_sets), MIN_CONSENSUS_CONFIDENCE
        )
        if consensus_raw != raw:
            raise RuntimeError(
                f"Frame-Mehrheit und Segment-Median widersprechen sich: "
                f"{raw} / {consensus_raw} ({counts})"
            )
        confidence = consensus_confidence
        value = float(raw)
        validate_value(value, previous, previous_timestamp, current_timestamp)

        annotated = annotate(best_frame, raw, confidence)
        display = best_frame[275:470, 410:890]
        ok_full, encoded_full = cv2.imencode(".jpg", annotated, [cv2.IMWRITE_JPEG_QUALITY, 92])
        ok_display, encoded_display = cv2.imencode(".jpg", display, [cv2.IMWRITE_JPEG_QUALITY, 95])
        if not ok_full or not ok_display:
            raise RuntimeError("JPEG-Ausgabe fehlgeschlagen")
        atomic_write(DATA_DIR / "snapshot.jpg", encoded_full.tobytes())
        atomic_write(DATA_DIR / "display.jpg", encoded_display.tobytes())

        status = {
            "status": "ok",
            "value": value,
            "raw": raw,
            "unit": "m³",
            "updated_at": timestamp,
            "last_success_at": timestamp,
            "confidence": confidence,
            "samples": counts,
            "interval": get_interval(),
            "effective_interval": get_interval(),
            "night_protection": False,
            "night_retry_after": None,
            "camera": get_camera_settings(),
            "alignment": alignment,
            "error": None,
        }
        atomic_write(DATA_DIR / "status.json", json.dumps(status, indent=2).encode("utf-8"))
        with STATE_LOCK:
            STATE.update(status)
        publish_success(value, raw, confidence, timestamp)
        push_webhook(status)
        print(f"{timestamp} gelesen: {raw} m³ (Sicherheit {confidence:.2f})", flush=True)
        return "ok"
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        is_dark = isinstance(exc, FrameTooDarkError)
        effective_interval = get_effective_interval(is_dark)
        retry_after = (
            datetime.fromtimestamp(
                current_timestamp.timestamp() + effective_interval,
                tz=timezone.utc,
            ).isoformat()
            if is_dark
            else None
        )
        with STATE_LOCK:
            update = {
                "status": "error",
                "updated_at": timestamp,
                "error": message,
                "effective_interval": effective_interval,
                "night_protection": is_dark,
                "night_retry_after": retry_after,
            }
            if is_dark:
                update["ambient_brightness"] = round(exc.brightness, 1)
            if alignment is not None:
                update["alignment"] = alignment
            STATE.update(update)
        publish_error(message, timestamp)
        print(f"{timestamp} FEHLER: {message}", flush=True)
        if is_dark:
            print(
                f"Nachtschutz aktiv: nächster automatischer Versuch frühestens "
                f"in {effective_interval}s",
                flush=True,
            )
        return "dark" if is_dark else "error"


class ApiHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args) -> None:
        return

    def send_bytes(self, content: bytes, content_type: str, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(content)

    def do_HEAD(self) -> None:
        self.do_GET()

    def do_GET(self) -> None:
        if self.path in ("/", "/api/status", "/health"):
            with STATE_LOCK:
                state = dict(STATE)
            status = 200 if state["status"] == "ok" or self.path != "/health" else 503
            self.send_bytes(json.dumps(state, indent=2).encode("utf-8"), "application/json", status)
            return
        if self.path == "/api/camera":
            self.send_bytes(
                json.dumps({"camera": get_camera_settings()}, indent=2).encode("utf-8"),
                "application/json",
            )
            return
        if self.path == "/api/alignment":
            with STATE_LOCK:
                alignment = dict(STATE.get("alignment") or {})
            alignment["reference_exists"] = ALIGNMENT_REFERENCE_FILE.exists()
            self.send_bytes(
                json.dumps({"alignment": alignment}, indent=2).encode("utf-8"),
                "application/json",
            )
            return
        paths = {
            "/snapshot.jpg": DATA_DIR / "snapshot.jpg",
            "/display.jpg": DATA_DIR / "display.jpg",
        }
        path = paths.get(self.path)
        if path and path.exists():
            self.send_bytes(path.read_bytes(), "image/jpeg")
            return
        self.send_bytes(b"Not found\n", "text/plain", 404)

    def do_POST(self) -> None:
        if self.path in ("/api/measure", "/measure"):
            MEASURE_WAKEUP_EVENT.set()
            self.send_bytes(
                json.dumps({"status": "accepted", "message": "Messung gestartet"}).encode("utf-8"),
                "application/json",
                202,
            )
            return
        if self.path == "/api/camera":
            length = int(self.headers.get("Content-Length", 0))
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
                settings = update_camera_settings(body)
            except (ValueError, TypeError, json.JSONDecodeError) as exc:
                self.send_bytes(
                    json.dumps({"error": str(exc)}).encode("utf-8"),
                    "application/json",
                    400,
                )
                return
            print(f"Kameraeinstellungen aktualisiert: {settings}", flush=True)
            with STATE_LOCK:
                current_state = dict(STATE)
            threading.Thread(target=push_webhook, args=(current_state,), daemon=True).start()
            self.send_bytes(
                json.dumps({"status": "accepted", "camera": settings}).encode("utf-8"),
                "application/json",
                202,
            )
            return
        if self.path in ("/api/interval", "/interval"):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            new_interval = body.get("interval")
            if new_interval is not None:
                try:
                    ival = int(new_interval)
                    if 10 <= ival <= 86400:
                        set_interval(ival)
                        MEASURE_WAKEUP_EVENT.set()
                        print(f"Messintervall auf {ival}s aktualisiert", flush=True)
                        self.send_bytes(
                            json.dumps({"status": "ok", "interval": ival}).encode("utf-8"),
                            "application/json",
                            200,
                        )
                        return
                except (ValueError, TypeError):
                    pass
            self.send_bytes(
                json.dumps({"error": "invalid interval (10-86400 seconds required)"}).encode("utf-8"),
                "application/json",
                400,
            )
            return
        if self.path in ("/api/webhook_register", "/api/webhook"):
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length).decode("utf-8")) if length > 0 else {}
            url = body.get("webhook_url", "")
            if url:
                set_webhook_url(url)
                print(f"Neuer Home-Assistant-Webhook registriert: {url}", flush=True)
                with STATE_LOCK:
                    current_state = dict(STATE)
                threading.Thread(target=push_webhook, args=(current_state,), daemon=True).start()
                self.send_bytes(
                    json.dumps({"status": "ok", "webhook_url": url}).encode("utf-8"),
                    "application/json",
                    200,
                )
                return
            self.send_bytes(
                json.dumps({"error": "missing webhook_url"}).encode("utf-8"),
                "application/json",
                400,
            )
            return
        self.send_bytes(b"Not found\n", "text/plain", 404)


def signal_handler(_signum, _frame) -> None:
    STOP_EVENT.set()
    MEASURE_WAKEUP_EVENT.set()


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    load_interval()
    previous_path = DATA_DIR / "status.json"
    previous_state = None
    if previous_path.exists():
        try:
            previous_state = json.loads(previous_path.read_text(encoding="utf-8"))
            with STATE_LOCK:
                STATE.update(previous_state)
                STATE["unit"] = "m³"
                STATE["last_success_at"] = previous_state.get(
                    "last_success_at", previous_state.get("updated_at")
                )
        except (OSError, json.JSONDecodeError):
            pass
    load_camera_settings()

    # Recreate the HA entities immediately after a service or HA restart. A
    # rejected camera frame must not prevent the last confirmed total from
    # being available under the expected entity IDs.
    if previous_state and previous_state.get("value") is not None:
        try:
            publish_success(
                float(previous_state["value"]),
                str(previous_state.get("raw", previous_state["value"])),
                float(previous_state.get("confidence") or 0),
                str(previous_state.get("updated_at") or utc_now()),
            )
        except Exception as exc:
            print(f"Home-Assistant-Wiederherstellung fehlgeschlagen: {exc}", flush=True)

    signal.signal(signal.SIGTERM, signal_handler)
    signal.signal(signal.SIGINT, signal_handler)
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), ApiHandler)
    threading.Thread(target=server.serve_forever, name="http-api", daemon=True).start()
    try:
        while not STOP_EVENT.is_set():
            cycle_started = time.monotonic()
            outcome = measure_once()
            if STOP_EVENT.is_set():
                break
            elapsed = time.monotonic() - cycle_started
            effective_interval = get_effective_interval(outcome == "dark")
            MEASURE_WAKEUP_EVENT.wait(timeout=max(0, effective_interval - elapsed))
            MEASURE_WAKEUP_EVENT.clear()
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
