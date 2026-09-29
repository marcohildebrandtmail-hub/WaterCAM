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
    (472, 301, 505, 368),
    (511, 303, 545, 367),
    (547, 301, 584, 365),
    (585, 302, 624, 365),
    (624, 301, 664, 365),
    (664, 301, 701, 365),
    (706, 318, 729, 364),
    (734, 315, 759, 361),
    (758, 314, 788, 361),
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

STATE_LOCK = threading.Lock()
CAMERA_LOCK = threading.Lock()
STOP_EVENT = threading.Event()
STATE = {
    "status": "starting",
    "value": None,
    "raw": None,
    "updated_at": None,
    "confidence": None,
    "error": None,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def atomic_write(path: Path, data: bytes) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_bytes(data)
    temporary.replace(path)


def configure_camera() -> None:
    for control in (
        "power_line_frequency=1",
        "auto_exposure=3",
        "focus_automatic_continuous=0",
        "focus_absolute=20",
    ):
        subprocess.run(
            ["v4l2-ctl", "-d", DEVICE, f"--set-ctrl={control}"],
            check=True,
            capture_output=True,
            text=True,
        )
        time.sleep(0.2)


def capture_frames() -> list[np.ndarray]:
    with CAMERA_LOCK:
        camera = cv2.VideoCapture(DEVICE, cv2.CAP_V4L2)
        camera.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))
        camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        configure_camera()
        frames = []
        try:
            # Auto exposure needs roughly two seconds after opening the LifeCam,
            # especially when the utility room changes from daylight to darkness.
            for index in range(75):
                ok, frame = camera.read()
                if not ok:
                    continue
                if index >= 70:
                    frames.append(frame)
        finally:
            camera.release()
        if len(frames) < 3:
            raise RuntimeError(f"Nur {len(frames)} Kamerabilder empfangen")
        return frames


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


def choose_threshold(scores: list[dict[str, float]]) -> float:
    """Find the threshold that best explains all positions as seven-segment digits."""
    best = None
    for half_step in range(20, 50):  # 10.0 to 24.5
        threshold = half_step / 2.0
        total_distance = 0
        for digit_scores in scores:
            active = frozenset(name for name, score in digit_scores.items() if score >= threshold)
            total_distance += min(
                len(active.symmetric_difference(pattern)) for pattern in SEGMENT_DIGITS
            )
        margin = min(abs(value - threshold) for digit in scores for value in digit.values())
        candidate = (total_distance, abs(threshold - 15.0), -margin, threshold)
        if best is None or candidate < best:
            best = candidate
    return best[-1] if best else 15.0


def decode_frame(frame: np.ndarray) -> tuple[str, float, list[dict[str, float]]]:
    scores = segment_scores(frame)
    threshold = choose_threshold(scores)
    digits = []
    margins = []
    for index, digit_scores in enumerate(scores):
        active = frozenset(name for name, score in digit_scores.items() if score >= threshold)
        decoded = SEGMENT_DIGITS.get(active)
        if decoded is None:
            nearest = sorted(
                (len(active.symmetric_difference(pattern)), value)
                for pattern, value in SEGMENT_DIGITS.items()
            )
            best_distance = nearest[0][0]
            nearest_digits = [value for distance, value in nearest if distance == best_distance]
            if best_distance != 1 or len(nearest_digits) != 1:
                raise ValueError(
                    f"Ungültiges Segmentmuster {sorted(active)} (nächste Ziffern {nearest[:3]})"
                )
            decoded = nearest_digits[0]
        digits.append(decoded)
        margins.extend(abs(score - threshold) for score in digit_scores.values())
    raw = "".join(digits[:6]) + "." + "".join(digits[6:])
    confidence = round(min(margins), 2)
    return raw, confidence, scores


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


def load_previous_value() -> float | None:
    path = DATA_DIR / "status.json"
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("value")
        return None if value is None else float(value)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def validate_value(value: float, previous: float | None) -> None:
    if previous is None:
        return
    if value + 0.001 < previous:
        raise ValueError(f"Zähler würde rückwärts springen: {previous:.3f} -> {value:.3f}")
    if value - previous > 5.0:
        raise ValueError(f"Unplausibler Sprung: {previous:.3f} -> {value:.3f}")


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
    push_webhook({"status": "error", "updated_at": timestamp, "error": message})
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


def measure_once() -> None:
    timestamp = utc_now()
    previous = load_previous_value()
    try:
        frames = capture_frames()
        readings = []
        for frame in frames:
            try:
                readings.append((*decode_frame(frame)[:2], frame))
            except ValueError:
                continue
        if not readings:
            raise RuntimeError("Keine der Aufnahmen konnte sicher gelesen werden")

        counts = {}
        for raw, _, _ in readings:
            counts[raw] = counts.get(raw, 0) + 1
        raw = max(counts, key=counts.get)
        matching = [item for item in readings if item[0] == raw]
        if len(matching) < 3:
            raise RuntimeError(f"Keine stabile Mehrheitslesung: {counts}")
        raw, confidence, best_frame = max(matching, key=lambda item: item[1])
        value = float(raw)
        validate_value(value, previous)

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
            "confidence": confidence,
            "samples": counts,
            "error": None,
        }
        atomic_write(DATA_DIR / "status.json", json.dumps(status, indent=2).encode("utf-8"))
        with STATE_LOCK:
            STATE.update(status)
        publish_success(value, raw, confidence, timestamp)
        push_webhook(status)
        print(f"{timestamp} gelesen: {raw} m³ (Sicherheit {confidence:.2f})", flush=True)
    except Exception as exc:
        message = f"{type(exc).__name__}: {exc}"
        with STATE_LOCK:
            STATE.update({"status": "error", "updated_at": timestamp, "error": message})
        publish_error(message, timestamp)
        print(f"{timestamp} FEHLER: {message}", flush=True)


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
            def run_measure():
                try:
                    measure_once()
                except Exception as exc:
                    print(f"Manuelle Messung fehlgeschlagen: {exc}", flush=True)

            threading.Thread(target=run_measure, name="manual-measure", daemon=True).start()
            self.send_bytes(
                json.dumps({"status": "accepted", "message": "Messung gestartet"}).encode("utf-8"),
                "application/json",
                202,
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


def main() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    previous_path = DATA_DIR / "status.json"
    previous_state = None
    if previous_path.exists():
        try:
            previous_state = json.loads(previous_path.read_text(encoding="utf-8"))
            with STATE_LOCK:
                STATE.update(previous_state)
        except (OSError, json.JSONDecodeError):
            pass

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
            measure_once()
            STOP_EVENT.wait(INTERVAL_SECONDS)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
