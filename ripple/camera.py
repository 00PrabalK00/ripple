"""Fixed-region depth observation. Camera measurements never come from a model."""
from io import BytesIO
import threading
import time
import numpy as np
from PIL import Image, ImageDraw


class DepthDetector:
    def __init__(self):
        self.baseline = None
        self.state = 'UNKNOWN'
        self.blocked_count = self.clear_count = 0
        self.last_valid = None

    def calibrate(self, depth):
        if np.mean(np.isfinite(depth) & (depth > 0)) < 0.8:
            raise ValueError('At least 80% valid depth is required for calibration')
        self.baseline = depth.copy()
        self.state = 'UNKNOWN'
        self.blocked_count = self.clear_count = 0
        self.last_valid = None

    def update(self, depth, now):
        if self.baseline is None or self.baseline.shape != depth.shape:
            return self.invalidate()
        valid = np.isfinite(depth) & (depth > 0) & np.isfinite(self.baseline) & (self.baseline > 0)
        if np.mean(valid) < 0.8:
            return self.invalidate()
        self.last_valid = now
        fraction = np.mean((self.baseline[valid] - depth[valid]) >= 0.03)
        self.blocked_count = self.blocked_count + 1 if fraction >= 0.10 else 0
        self.clear_count = self.clear_count + 1 if fraction < 0.02 else 0
        if self.blocked_count >= 5:
            self.state = 'BLOCKED'
        elif self.clear_count >= 10:
            self.state = 'CLEAR'
        return self.state

    def invalidate(self):
        self.state = 'UNKNOWN'
        self.blocked_count = self.clear_count = 0
        self.last_valid = None
        return self.state

    def current(self, now):
        if self.last_valid is None or now - self.last_valid > 1:
            return self.invalidate()
        return self.state


class Camera:
    def __init__(self, events):
        self.events = events
        self.lock = threading.Lock()
        self.stop = threading.Event()
        self.detector = DepthDetector()
        self.roi = None
        self.depth = self.color = None
        self.frame_at = None
        self.error = 'Camera not connected'
        self.thread = threading.Thread(target=self.run, daemon=True, name='realsense')
        self.thread.start()

    def run(self):
        import pyrealsense2 as rs
        from .contracts import RobotEvent
        while not self.stop.is_set():
            pipeline = rs.pipeline()
            started = False
            try:
                config = rs.config()
                config.enable_stream(rs.stream.depth, 640, 480, rs.format.z16, 30)
                config.enable_stream(rs.stream.color, 640, 480, rs.format.rgb8, 30)
                profile = pipeline.start(config)
                started = True
                scale = profile.get_device().first_depth_sensor().get_depth_scale()
                align = rs.align(rs.stream.color)
                with self.lock:
                    self.detector = DepthDetector()  # reconnect requires new baseline
                while not self.stop.is_set():
                    frames = align.process(pipeline.wait_for_frames(1000))
                    depth_frame, color_frame = frames.get_depth_frame(), frames.get_color_frame()
                    if not depth_frame or not color_frame:
                        raise RuntimeError('Missing aligned depth or color frame')
                    depth = np.asanyarray(depth_frame.get_data()).astype(np.float32) * scale
                    color = np.asanyarray(color_frame.get_data()).copy()
                    now = time.monotonic()
                    with self.lock:
                        self.depth, self.color, self.frame_at = depth, color, now
                        self.error = None
                        if self.roi:
                            x, y, w, h = self.roi
                            state = self.detector.update(depth[y:y+h, x:x+w], now)
                        else:
                            state = 'UNKNOWN'
                    self.events.put(RobotEvent('camera', data={'state': state, 'received_at': now}))
            except Exception as error:
                with self.lock:
                    self.error = str(error)
                    self.detector.invalidate()
                self.events.put(RobotEvent('camera', data={'state': 'UNKNOWN', 'received_at': time.monotonic()}))
                self.stop.wait(1)
            finally:
                if started:
                    pipeline.stop()

    def calibrate(self, roi):
        x, y, w, h = roi
        if min(x, y) < 0 or min(w, h) < 20 or x+w > 640 or y+h > 480:
            raise ValueError('ROI must be inside 640×480 and at least 20×20 pixels')
        with self.lock:
            if self.depth is None or time.monotonic() - self.frame_at > 1:
                raise ValueError('A fresh camera frame is required')
            self.detector.calibrate(self.depth[y:y+h, x:x+w])
            self.roi = list(roi)

    def status(self):
        with self.lock:
            return dict(state=self.detector.current(time.monotonic()), roi=self.roi,
                        calibrated=self.detector.baseline is not None, error=self.error,
                        fresh=self.frame_at is not None and time.monotonic()-self.frame_at < 1)

    def jpeg(self):
        with self.lock:
            if self.color is None or time.monotonic()-self.frame_at > 1:
                raise ValueError('No fresh camera image')
            im = Image.fromarray(self.color)
            if self.roi:
                x, y, w, h = self.roi
                draw = ImageDraw.Draw(im)
                draw.rectangle((x,y,x+w,y+h), outline='#ffb000', width=3)
            output = BytesIO()
            im.save(output, format='JPEG', quality=80)
            return output.getvalue()

    def close(self):
        self.stop.set()
        self.thread.join(timeout=3)
