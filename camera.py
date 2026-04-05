import logging
import threading
import time
from datetime import datetime
from pathlib import Path

import cv2

logger = logging.getLogger(__name__)

TIMELAPSE_DIR = Path(__file__).parent / "timelapse"
TIMELAPSE_DIR.mkdir(exist_ok=True)

_JPEG_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 85]


class Camera:
    """Continuous capture thread; provides MJPEG stream and snapshot/timelapse."""

    def __init__(self, device=0):
        self.device = device
        self._lock = threading.Lock()
        self._frame = None
        self._running = False
        self._thread = None
        self._active_source = None

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("Camera thread started (device %s)", self.device)

    def stop(self) -> None:
        self._running = False

    def _capture_candidates(self) -> list:
        candidates = [self.device]

        if isinstance(self.device, int):
            candidates.append(f"/dev/video{self.device}")
            for index in range(4):
                if index != self.device:
                    candidates.append(index)
                    candidates.append(f"/dev/video{index}")
        elif isinstance(self.device, str) and self.device.startswith("/dev/video"):
            suffix = self.device.removeprefix("/dev/video")
            if suffix.isdigit():
                candidates.append(int(suffix))

        deduped = []
        seen = set()
        for item in candidates:
            key = str(item)
            if key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def _open_capture(self):
        for source in self._capture_candidates():
            for backend in (cv2.CAP_V4L2, cv2.CAP_ANY):
                try:
                    cap = cv2.VideoCapture(source, backend)
                except Exception:
                    cap = cv2.VideoCapture(source)
                if not cap or not cap.isOpened():
                    if cap:
                        cap.release()
                    continue

                cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
                cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
                cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                cap.set(cv2.CAP_PROP_FOURCC, cv2.VideoWriter_fourcc(*"MJPG"))

                ok, frame = cap.read()
                if ok and frame is not None:
                    self._active_source = source
                    logger.info("Camera opened via source %s (backend %s)", source, backend)
                    with self._lock:
                        self._frame = frame
                    return cap

                cap.release()

        self._active_source = None
        return None

    def _capture_loop(self) -> None:
        while self._running:
            cap = self._open_capture()
            if cap is None:
                logger.error("Cannot open camera device %s", self.device)
                time.sleep(2)
                continue

            try:
                failures = 0
                while self._running:
                    ret, frame = cap.read()
                    if ret and frame is not None:
                        failures = 0
                        with self._lock:
                            self._frame = frame
                        continue

                    failures += 1
                    if failures >= 5:
                        logger.warning("Camera read failed repeatedly on %s, reconnecting", self._active_source)
                        break
                    time.sleep(0.2)
            finally:
                cap.release()

            time.sleep(1)

    @property
    def is_available(self) -> bool:
        return self._frame is not None

    def _encode(self, frame) -> bytes | None:
        ret, buf = cv2.imencode(".jpg", frame, _JPEG_PARAMS)
        return buf.tobytes() if ret else None

    def get_snapshot(self) -> bytes | None:
        with self._lock:
            if self._frame is None:
                return None
            return self._encode(self._frame)

    def save_timelapse_frame(self) -> str | None:
        with self._lock:
            if self._frame is None:
                return None
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = TIMELAPSE_DIR / f"frame_{ts}.jpg"
            cv2.imwrite(str(path), self._frame)
            return str(path)

    def generate_stream(self):
        while True:
            snapshot = self.get_snapshot()
            if snapshot:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + snapshot
                    + b"\r\n"
                )
            time.sleep(0.05)
