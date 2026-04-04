import cv2
import threading
import time
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

TIMELAPSE_DIR = Path(__file__).parent / "timelapse"
TIMELAPSE_DIR.mkdir(exist_ok=True)

# JPEG encode params
_JPEG_PARAMS = [cv2.IMWRITE_JPEG_QUALITY, 85]


class Camera:
    """Continuous capture thread; provides MJPEG stream and snapshot/timelapse."""

    def __init__(self, device: int = 0):
        self.device = device
        self._lock = threading.Lock()
        self._frame = None          # raw BGR ndarray
        self._running = False
        self._thread = None

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        self._running = True
        self._thread = threading.Thread(target=self._capture_loop, daemon=True)
        self._thread.start()
        logger.info("Camera thread started (device %s)", self.device)

    def stop(self) -> None:
        self._running = False

    def _capture_loop(self) -> None:
        cap = cv2.VideoCapture(self.device)
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)

        if not cap.isOpened():
            logger.error("Cannot open camera device %s", self.device)
            return

        while self._running:
            ret, frame = cap.read()
            if ret:
                with self._lock:
                    self._frame = frame
            else:
                time.sleep(0.5)

        cap.release()

    # ------------------------------------------------------------------ output

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
        """Generator for MJPEG multipart stream."""
        while True:
            snapshot = self.get_snapshot()
            if snapshot:
                yield (
                    b"--frame\r\n"
                    b"Content-Type: image/jpeg\r\n\r\n"
                    + snapshot
                    + b"\r\n"
                )
            time.sleep(0.05)  # ~20 fps cap
