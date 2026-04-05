import unittest

try:
    import camera
except Exception as exc:  # pragma: no cover
    camera = None
    IMPORT_ERROR = exc
else:
    IMPORT_ERROR = None


class FakeCapture:
    def __init__(self, opened=False, frame=None):
        self._opened = opened
        self._frame = frame
        self.released = False

    def isOpened(self):
        return self._opened

    def set(self, prop, value):
        return True

    def read(self):
        return (self._frame is not None, self._frame)

    def release(self):
        self.released = True


class CameraTests(unittest.TestCase):
    def setUp(self):
        if IMPORT_ERROR is not None:
            self.skipTest(f"camera import unavailable: {IMPORT_ERROR}")

    def test_capture_candidates_include_fallback_video_nodes(self):
        cam = camera.Camera(device=1)

        candidates = cam._capture_candidates()

        self.assertEqual(candidates[0], 1)
        self.assertIn("/dev/video1", candidates)
        self.assertIn(0, candidates)
        self.assertIn("/dev/video0", candidates)

    def test_open_capture_falls_back_to_next_candidate(self):
        original = camera.cv2.VideoCapture
        calls = []

        def fake_videocapture(source, backend=None):
            calls.append((source, backend))
            if source in (1, "/dev/video1"):
                return FakeCapture(opened=False)
            if source in (0, "/dev/video0"):
                return FakeCapture(opened=True, frame=b"frame")
            return FakeCapture(opened=False)

        camera.cv2.VideoCapture = fake_videocapture
        try:
            cam = camera.Camera(device=1)
            cap = cam._open_capture()
        finally:
            camera.cv2.VideoCapture = original

        self.assertIsNotNone(cap)
        self.assertIn((0, camera.cv2.CAP_V4L2), calls)
        self.assertEqual(cam._active_source, 0)
        self.assertEqual(cam._frame, b"frame")


if __name__ == "__main__":
    unittest.main()
