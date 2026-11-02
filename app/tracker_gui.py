"""
Particle Tracker GUI (v2)

Skeleton: load a .twv video via the pims-based TWVReader (twv.py), display
the currently selected frame on the right, and scrub through frames with a
slider + linked spin box underneath. The left side is reserved for future
controls (detection/filtering parameters, log box, etc.).
"""
import multiprocessing
import os
import sys

import matplotlib
matplotlib.use("QtAgg")

import trackpy as tp
from trackpy import predict as tp_predict
from trackpy.try_numba import NUMBA_AVAILABLE

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg
from matplotlib.figure import Figure

from PyQt6.QtCore import Qt, QLocale, QThread, pyqtSignal, QSettings
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QDialog, QSplitter, QVBoxLayout, QHBoxLayout,
    QPushButton, QLabel, QFileDialog, QMessageBox, QSlider, QSpinBox, QDoubleSpinBox,
    QCheckBox, QGroupBox, QSizePolicy, QTabWidget, QProgressBar,
)

from app.twv import TWVReader

tp.quiet()

# Below this drag distance (in data/pixel units), a press+release is treated
# as a single point click rather than a ruler measurement.
CLICK_DRAG_THRESHOLD = 1.0

# Prefer the numba-accelerated refinement/linking paths; fall back to
# trackpy's own auto-detection (still correct, just slower) if numba isn't
# importable, e.g. a frozen build that failed to bundle it.
LOCATE_ENGINE = "numba" if NUMBA_AVAILABLE else "auto"
LINK_STRATEGY = "numba" if NUMBA_AVAILABLE else "auto"

# QSettings identity: fixed explicitly (rather than relying on
# QApplication's org/app name) so persisted settings land in the same place
# regardless of how the frozen executable is invoked.
SETTINGS_ORG = "ParticleTrackerLab"
SETTINGS_APP = "ParticleTracker"


class SliderSpin(QWidget):
    """A slider synced with a spin box, sharing one value.

    decimals=0 backs the spin box with an int QSpinBox; decimals>0 uses a
    QDoubleSpinBox, with the (integer-only) QSlider internally scaled by
    10**decimals. odd_only snaps both widgets to the nearest odd integer,
    for parameters like trackpy's `diameter`.
    """

    valueChanged = pyqtSignal(object)

    def __init__(self, minimum, maximum, default, step=1, decimals=0, odd_only=False, parent=None):
        super().__init__(parent)
        self.decimals = decimals
        self.scale = 10 ** decimals
        self.odd_only = odd_only

        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(round(minimum * self.scale), round(maximum * self.scale))
        self.slider.setSingleStep(max(1, round(step * self.scale)))

        if decimals > 0:
            self.spin = QDoubleSpinBox()
            self.spin.setDecimals(decimals)
            self.spin.setRange(minimum, maximum)
            self.spin.setSingleStep(step)
        else:
            self.spin = QSpinBox()
            self.spin.setRange(int(minimum), int(maximum))
            self.spin.setSingleStep(int(step))
        self.spin.setLocale(QLocale.c())
        self.spin.setGroupSeparatorShown(False)
        self.spin.setFixedWidth(90)

        layout.addWidget(self.slider, stretch=1)
        layout.addWidget(self.spin)

        self.slider.valueChanged.connect(self._on_slider)
        self.spin.valueChanged.connect(self._on_spin)

        self.set_value(default)

    def _snap_odd(self, v):
        v = int(round(v))
        return v if v % 2 == 1 else v + 1

    def _on_slider(self, ival):
        val = ival / self.scale if self.decimals else ival
        if self.odd_only:
            val = self._snap_odd(val)
        self.spin.blockSignals(True)
        self.spin.setValue(val)
        self.spin.blockSignals(False)
        target_i = round(val * self.scale)
        if target_i != ival:
            self.slider.blockSignals(True)
            self.slider.setValue(target_i)
            self.slider.blockSignals(False)
        self.valueChanged.emit(val)

    def _on_spin(self, val):
        if self.odd_only:
            snapped = self._snap_odd(val)
            if snapped != val:
                self.spin.blockSignals(True)
                self.spin.setValue(snapped)
                self.spin.blockSignals(False)
                val = snapped
        self.slider.blockSignals(True)
        self.slider.setValue(round(val * self.scale))
        self.slider.blockSignals(False)
        self.valueChanged.emit(val)

    def value(self):
        return self.spin.value()

    def set_value(self, v):
        self.spin.setValue(v)
        self.slider.blockSignals(True)
        self.slider.setValue(round(self.spin.value() * self.scale))
        self.slider.blockSignals(False)


class OptionalSliderSpin(QWidget):
    """A SliderSpin gated behind an "include this parameter" checkbox.

    When unchecked, `is_enabled()` is False and the slider/spin is disabled;
    callers should omit the corresponding trackpy.locate kwarg entirely so
    trackpy falls back to its own default, rather than passing this widget's
    (possibly stale) value.
    """

    def __init__(self, label, minimum, maximum, default, step=1, decimals=0, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.checkbox = QCheckBox(label)
        self.checkbox.setChecked(False)
        layout.addWidget(self.checkbox)

        self.slider_spin = SliderSpin(minimum, maximum, default, step=step, decimals=decimals)
        self.slider_spin.setEnabled(False)
        self.checkbox.toggled.connect(self.slider_spin.setEnabled)
        layout.addWidget(self.slider_spin)

    def is_enabled(self):
        return self.checkbox.isChecked()

    def value(self):
        return self.slider_spin.value()


class AdvancedSettingsDialog(QDialog):
    """Secondary trackpy.locate/trackpy.link parameters, each opt-in via its
    own checkbox (except `invert`, whose own checked state is its value).

    Kept alive for the lifetime of MainWindow (shown/hidden, never destroyed)
    so the chosen values and enable states persist across Detect/Track runs.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Advanced Settings")
        self.resize(380, 420)

        layout = QVBoxLayout(self)
        tabs = QTabWidget()
        layout.addWidget(tabs)

        # ---- Detection tab (trackpy.locate) ----
        det_tab = QWidget()
        det_layout = QVBoxLayout(det_tab)

        self.invert_chk = QCheckBox("Invert (dark particles on bright background)")
        self.invert_chk.setChecked(False)
        det_layout.addWidget(self.invert_chk)

        self.percentile_opt = OptionalSliderSpin(
            "Percentile", minimum=0, maximum=100, default=64, step=1,
        )
        det_layout.addWidget(self.percentile_opt)

        self.noise_size_opt = OptionalSliderSpin(
            "Noise size (px)", minimum=0.1, maximum=10.0, default=1.0, step=0.1, decimals=1,
        )
        det_layout.addWidget(self.noise_size_opt)

        self.smoothing_size_opt = OptionalSliderSpin(
            "Smoothing size (px, default = diameter)", minimum=1, maximum=201, default=21, step=1,
        )
        det_layout.addWidget(self.smoothing_size_opt)

        self.topn_opt = OptionalSliderSpin(
            "Keep only N brightest", minimum=1, maximum=1000, default=50, step=1,
        )
        det_layout.addWidget(self.topn_opt)

        det_layout.addStretch()
        tabs.addTab(det_tab, "Detection")

        # ---- Tracking tab (trackpy.link) ----
        link_tab = QWidget()
        link_layout = QVBoxLayout(link_tab)

        self.memory_opt = OptionalSliderSpin(
            "Memory (frames a particle may vanish)", minimum=0, maximum=50, default=3, step=1,
        )
        link_layout.addWidget(self.memory_opt)

        self.adaptive_step_opt = OptionalSliderSpin(
            "Adaptive step (shrink factor)", minimum=0.5, maximum=0.99, default=0.95, step=0.01, decimals=2,
        )
        link_layout.addWidget(self.adaptive_step_opt)

        self.predictor_chk = QCheckBox("Use nearest-velocity predictor (fast/directed motion)")
        self.predictor_chk.setChecked(False)
        link_layout.addWidget(self.predictor_chk)

        link_layout.addStretch()
        tabs.addTab(link_tab, "Tracking")

        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.close)
        layout.addWidget(close_btn)


class HistogramDialog(QDialog):
    """A one-shot window showing a detection-property histogram for the
    current frame, with the corresponding filter's current threshold marked
    (that filter is ignored when gathering the values, so its full
    distribution is visible)."""

    def __init__(self, values, threshold_value, column, threshold_name, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle(f"{column.capitalize()} Histogram")
        self.resize(600, 450)

        layout = QVBoxLayout(self)
        fig = Figure(figsize=(5, 4))
        ax = fig.add_subplot(111)
        canvas = FigureCanvasQTAgg(fig)
        layout.addWidget(canvas)

        if len(values):
            ax.hist(values, bins=50, color="steelblue", edgecolor="black")
            ax.axvline(threshold_value, color="red", linestyle="--", label=f"{threshold_name} = {threshold_value:g}")
            ax.legend()
        ax.set_xlabel(column)
        ax.set_ylabel("count")
        ax.set_title(f"{column.capitalize()} distribution ({len(values)} detections, {threshold_name} filter ignored)")
        fig.tight_layout()


class TrajectoryPlotDialog(QDialog):
    """x(t) and y(t) for every trajectory, overlaid in one color at low
    opacity. Each trajectory is shifted to start at 0 (its own initial x/y
    subtracted) and plotted against its own recorded timestamps, so
    trajectories starting at different positions or frames stay comparable."""

    def __init__(self, tracked, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Trajectory Plots")
        self.resize(900, 650)

        layout = QVBoxLayout(self)
        fig = Figure(figsize=(9, 6.5))
        ax_x = fig.add_subplot(211)
        ax_y = fig.add_subplot(212, sharex=ax_x)
        canvas = FigureCanvasQTAgg(fig)
        layout.addWidget(canvas)

        for _, g in tracked.groupby("particle"):
            g = g.sort_values("frame")
            ts = g["timestamp"].to_numpy()
            dx = g["x"].to_numpy() - g["x"].to_numpy()[0]
            dy = g["y"].to_numpy() - g["y"].to_numpy()[0]
            ax_x.plot(ts, dx, color="steelblue", alpha=0.25, linewidth=1)
            ax_y.plot(ts, dy, color="steelblue", alpha=0.25, linewidth=1)

        ax_x.set_ylabel("x - x0 (px)")
        ax_x.set_title("x(t)")
        ax_y.set_xlabel("time")
        ax_y.set_ylabel("y - y0 (px)")
        ax_y.set_title("y(t)")
        fig.tight_layout()


class VideoCanvas(FigureCanvasQTAgg):
    """Matplotlib canvas showing a single grayscale frame.

    Also supports a click-to-measure pixel ruler: click a point to report
    its coordinates, or click-drag to a second point to report the distance
    between them. Emits `measured` with a human-readable status string.
    """

    measured = pyqtSignal(str)

    def __init__(self):
        self.fig = Figure()
        self.ax = self.fig.add_subplot(111)
        self.ax.set_axis_off()
        self.fig.subplots_adjust(left=0, right=1, top=1, bottom=0)
        super().__init__(self.fig)
        self._im = None
        self._detections_artist = None
        self._trajectory_artists = []

        self._press_xy = None
        self._last_xy = None
        self._start_marker = None
        self._end_marker = None
        self._line_artist = None

        self.mpl_connect("button_press_event", self._on_press)
        self.mpl_connect("motion_notify_event", self._on_motion)
        self.mpl_connect("button_release_event", self._on_release)

    def show_frame(self, img):
        if self._im is None:
            self._im = self.ax.imshow(img, cmap="gray", vmin=0, vmax=255)
        else:
            self._im.set_data(img)
        self.clear_detections()
        self.reset_measurement()
        self.draw_idle()

    def show_detections(self, f):
        """Overlay detected particle positions (a DataFrame with x, y columns)."""
        self.clear_detections()
        if len(f):
            self._detections_artist = self.ax.scatter(
                f["x"], f["y"], s=80, facecolors="none", edgecolors="lime", linewidths=1.2,
            )
        self.draw_idle()

    def clear_detections(self):
        if self._detections_artist is not None:
            self._detections_artist.remove()
            self._detections_artist = None

    def show_trajectories(self, t):
        """Overlay linked trajectories (a DataFrame with x, y, particle columns)."""
        self.clear_trajectories()
        self._trajectory_artists = [
            self.ax.plot(g["x"], g["y"], color="yellow", linewidth=1.2)[0]
            for _, g in t.groupby("particle")
        ]
        self.draw_idle()

    def clear_trajectories(self):
        for artist in self._trajectory_artists:
            artist.remove()
        self._trajectory_artists = []

    def clear(self):
        self.ax.clear()
        self.ax.set_axis_off()
        self._im = None
        self._detections_artist = None
        self._trajectory_artists = []
        self.reset_measurement()

    def reset_measurement(self):
        self._clear_artists()
        self._press_xy = None
        self._last_xy = None
        self.draw_idle()
        self.measured.emit("")

    # ------------------------------------------------------------ ruler --
    @staticmethod
    def _ruler_label(x0, y0, x1, y1, dist):
        dx, dy = x1 - x0, y1 - y0
        angle = dx / dy if dy != 0 else float("inf")
        return (
            f"({x0:.1f}, {y0:.1f}) → ({x1:.1f}, {y1:.1f})   "
            f"d = {dist:.2f} px   angle (dx/dy) = {angle:.3f}"
        )

    def _on_press(self, event):
        if event.button != 1 or event.inaxes != self.ax or event.xdata is None:
            return
        self._clear_artists()
        self._press_xy = (event.xdata, event.ydata)
        self._last_xy = self._press_xy
        self._start_marker, = self.ax.plot(
            event.xdata, event.ydata, marker="+", color="red",
            markersize=12, markeredgewidth=1.5,
        )
        self.draw_idle()
        self.measured.emit(f"({event.xdata:.1f}, {event.ydata:.1f})")

    def _on_motion(self, event):
        if self._press_xy is None:
            return
        if event.inaxes == self.ax and event.xdata is not None:
            self._last_xy = (event.xdata, event.ydata)
        x0, y0 = self._press_xy
        x1, y1 = self._last_xy
        if self._line_artist is None:
            self._line_artist, = self.ax.plot([x0, x1], [y0, y1], "r-", linewidth=1.2)
        else:
            self._line_artist.set_data([x0, x1], [y0, y1])
        dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5
        self.draw_idle()
        self.measured.emit(self._ruler_label(x0, y0, x1, y1, dist))

    def _on_release(self, event):
        if self._press_xy is None:
            return
        if event.inaxes == self.ax and event.xdata is not None:
            self._last_xy = (event.xdata, event.ydata)
        x0, y0 = self._press_xy
        x1, y1 = self._last_xy
        dist = ((x1 - x0) ** 2 + (y1 - y0) ** 2) ** 0.5

        if dist < CLICK_DRAG_THRESHOLD:
            # Plain click, no drag: keep just the point marker.
            if self._line_artist is not None:
                self._line_artist.remove()
                self._line_artist = None
            self.measured.emit(f"Point: ({x0:.1f}, {y0:.1f})")
        else:
            self._end_marker, = self.ax.plot(
                x1, y1, marker="+", color="red", markersize=12, markeredgewidth=1.5,
            )
            self.measured.emit(self._ruler_label(x0, y0, x1, y1, dist))

        self.draw_idle()
        self._press_xy = None

    def _clear_artists(self):
        for artist in (self._start_marker, self._end_marker, self._line_artist):
            if artist is not None:
                artist.remove()
        self._start_marker = self._end_marker = self._line_artist = None


class TrackingCancelled(Exception):
    """Raised from the locate callback to unwind out of tp.batch when the
    user aborts a run in progress."""


class TrackingWorker(QThread):
    """Runs tp.locate over every frame (parallelized across CPU cores via
    tp.batch), then links + filters the results, off the GUI thread so the
    window stays responsive on long videos."""

    progress = pyqtSignal(int, int)
    finished_ok = pyqtSignal(object)
    failed = pyqtSignal(str)

    def __init__(self, video_path, locate_params, link_params, filter_stubs_threshold, use_predictor, max_ecc):
        super().__init__()
        self.video_path = video_path
        self.locate_params = locate_params
        self.link_params = link_params
        self.filter_stubs_threshold = filter_stubs_threshold
        self.use_predictor = use_predictor
        self.max_ecc = max_ecc

    def run(self):
        frames = None
        try:
            frames = TWVReader(self.video_path)
            n = len(frames)
            timestamps = [frames.get_timestamp(i) for i in range(n)]

            def after_locate(frame_no, features):
                if self.isInterruptionRequested():
                    raise TrackingCancelled()
                self.progress.emit(frame_no + 1, n)
                return features

            f_all = tp.batch(
                frames, processes="auto", after_locate=after_locate, **self.locate_params,
            )

            f_all = f_all[f_all["ecc"] < self.max_ecc]

            if f_all.empty:
                self.failed.emit("No particles detected in any frame.")
                return

            if self.use_predictor:
                t = tp_predict.NearestVelocityPredict().link_df(f_all, **self.link_params)
            else:
                t = tp.link(f_all, **self.link_params)

            t = tp.filter_stubs(t, threshold=self.filter_stubs_threshold)
            t = t.reset_index(drop=True)

            if t.empty:
                self.failed.emit("No trajectories survived linking/filtering.")
                return

            t["timestamp"] = t["frame"].map(lambda fn: timestamps[int(fn)])

            self.finished_ok.emit(t)
        except TrackingCancelled:
            self.failed.emit("Tracking cancelled.")
        except Exception as e:
            self.failed.emit(str(e))
        finally:
            if frames is not None:
                frames.close()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Particle Tracker")
        self.resize(1400, 900)

        self.video_path = None
        self.frames = None  # TWVReader instance
        self.tracked = None
        self.tracking_worker = None

        self.advanced_dialog = AdvancedSettingsDialog(parent=self)

        self._build_ui()
        self._load_settings()

    # ------------------------------------------------------------------ UI --
    def _build_ui(self):
        splitter = QSplitter()
        self.setCentralWidget(splitter)

        # ---- left: controls (placeholder for now) ----
        self.left_panel = QWidget()
        left_layout = QVBoxLayout(self.left_panel)
        left_layout.setContentsMargins(8, 8, 8, 8)

        open_btn = QPushButton("Open Video (.twv)...")
        open_btn.clicked.connect(self.open_video)
        left_layout.addWidget(open_btn)

        self.video_label = QLabel("No video loaded.")
        self.video_label.setWordWrap(True)
        left_layout.addWidget(self.video_label)

        det_group = QGroupBox("Detection (trackpy.locate)")
        det_layout = QVBoxLayout(det_group)

        det_layout.addWidget(QLabel("Diameter (odd, px)"))
        self.diameter_ctrl = SliderSpin(minimum=3, maximum=101, default=21, step=2, odd_only=True)
        det_layout.addWidget(self.diameter_ctrl)

        det_layout.addWidget(QLabel("Min mass"))
        minmass_row = QHBoxLayout()
        self.minmass_ctrl = SliderSpin(minimum=0, maximum=20000, default=0, step=10)
        minmass_row.addWidget(self.minmass_ctrl, stretch=1)
        self.hist_btn = QPushButton("Histogram...")
        self.hist_btn.setEnabled(False)
        self.hist_btn.clicked.connect(self.show_mass_histogram)
        minmass_row.addWidget(self.hist_btn)
        det_layout.addLayout(minmass_row)

        det_layout.addWidget(QLabel("Separation (px)"))
        self.separation_ctrl = SliderSpin(minimum=1, maximum=200, default=22, step=1)
        det_layout.addWidget(self.separation_ctrl)

        det_layout.addWidget(QLabel("Max size (radius of gyration, px)"))
        maxsize_row = QHBoxLayout()
        self.maxsize_ctrl = SliderSpin(minimum=0.5, maximum=50.0, default=10.0, step=0.5, decimals=1)
        maxsize_row.addWidget(self.maxsize_ctrl, stretch=1)
        self.size_hist_btn = QPushButton("Histogram...")
        self.size_hist_btn.setEnabled(False)
        self.size_hist_btn.clicked.connect(self.show_size_histogram)
        maxsize_row.addWidget(self.size_hist_btn)
        det_layout.addLayout(maxsize_row)

        det_layout.addWidget(QLabel("Max eccentricity (0 = circular, 1 = line)"))
        ecc_row = QHBoxLayout()
        self.max_ecc_ctrl = SliderSpin(minimum=0.0, maximum=1.0, default=1.0, step=0.05, decimals=2)
        ecc_row.addWidget(self.max_ecc_ctrl, stretch=1)
        self.ecc_hist_btn = QPushButton("Histogram...")
        self.ecc_hist_btn.setEnabled(False)
        self.ecc_hist_btn.clicked.connect(self.show_ecc_histogram)
        ecc_row.addWidget(self.ecc_hist_btn)
        det_layout.addLayout(ecc_row)

        left_layout.addWidget(det_group)

        track_group = QGroupBox("Tracking (trackpy.link)")
        track_layout = QVBoxLayout(track_group)

        track_layout.addWidget(QLabel("Search range (px)"))
        self.search_range_ctrl = SliderSpin(minimum=1, maximum=100, default=5, step=1)
        track_layout.addWidget(self.search_range_ctrl)

        track_layout.addWidget(QLabel("Min trajectory length (frames, 1 = no filter)"))
        self.filter_stubs_ctrl = SliderSpin(minimum=1, maximum=1000, default=1, step=1)
        track_layout.addWidget(self.filter_stubs_ctrl)

        track_layout.addWidget(QLabel("Adaptive stop (px) — shrinks search range to resolve dense clusters"))
        self.adaptive_stop_ctrl = SliderSpin(minimum=0.1, maximum=50.0, default=1.0, step=0.1, decimals=1)
        track_layout.addWidget(self.adaptive_stop_ctrl)

        left_layout.addWidget(track_group)

        left_layout.addStretch()

        advanced_btn = QPushButton("Advanced Settings...")
        advanced_btn.clicked.connect(self.open_advanced_settings)
        left_layout.addWidget(advanced_btn)

        self.detect_btn = QPushButton("Detect Particles")
        self.detect_btn.setEnabled(False)
        self.detect_btn.clicked.connect(self.run_detection)
        left_layout.addWidget(self.detect_btn)

        self.detect_count_label = QLabel("Detected: -")
        left_layout.addWidget(self.detect_count_label)

        track_row = QHBoxLayout()
        self.track_btn = QPushButton("Track Full Video")
        self.track_btn.setEnabled(False)
        self.track_btn.clicked.connect(self.run_tracking)
        track_row.addWidget(self.track_btn, stretch=1)

        self.abort_btn = QPushButton("Abort")
        self.abort_btn.setEnabled(False)
        self.abort_btn.clicked.connect(self.abort_tracking)
        track_row.addWidget(self.abort_btn)
        left_layout.addLayout(track_row)

        self.track_progress = QProgressBar()
        left_layout.addWidget(self.track_progress)

        self.track_status_label = QLabel("")
        self.track_status_label.setWordWrap(True)
        left_layout.addWidget(self.track_status_label)

        save_row = QHBoxLayout()
        self.save_btn = QPushButton("Save Trajectories")
        self.save_btn.setEnabled(False)
        self.save_btn.clicked.connect(self.save_trajectories)
        save_row.addWidget(self.save_btn, stretch=1)

        self.plot_btn = QPushButton("Plot Trajectories")
        self.plot_btn.setEnabled(False)
        self.plot_btn.clicked.connect(self.plot_trajectories)
        save_row.addWidget(self.plot_btn)
        left_layout.addLayout(save_row)

        self.left_panel.setMinimumWidth(280)
        self.left_panel.setMaximumWidth(420)
        splitter.addWidget(self.left_panel)

        # ---- right: video display + frame scrubber ----
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)
        right_layout.setContentsMargins(4, 4, 4, 4)

        top_bar = QHBoxLayout()
        top_bar.addStretch()
        self.measure_label = QLabel("Click a point to measure (px)")
        self.measure_label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        top_bar.addWidget(self.measure_label)
        right_layout.addLayout(top_bar)

        self.canvas = VideoCanvas()
        self.canvas.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.canvas.measured.connect(self._on_measured)
        right_layout.addWidget(self.canvas, stretch=1)

        scrub_layout = QHBoxLayout()
        self.frame_slider = QSlider(Qt.Orientation.Horizontal)
        self.frame_slider.setEnabled(False)
        self.frame_slider.valueChanged.connect(self._on_slider_changed)
        scrub_layout.addWidget(self.frame_slider, stretch=1)

        self.frame_spin = QSpinBox()
        self.frame_spin.setEnabled(False)
        self.frame_spin.setMinimumWidth(80)
        self.frame_spin.valueChanged.connect(self._on_spin_changed)
        scrub_layout.addWidget(self.frame_spin)

        right_layout.addLayout(scrub_layout)

        splitter.addWidget(right_panel)
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

    # -------------------------------------------------------------- actions --
    def open_video(self):
        path, _ = QFileDialog.getOpenFileName(self, "Open .twv video", "", "TWV videos (*.twv)")
        if not path:
            return
        try:
            frames = TWVReader(path)
        except Exception as e:
            QMessageBox.critical(self, "Error opening video", str(e))
            return

        if self.frames is not None:
            self.frames.close()

        self.video_path = path
        self.frames = frames
        n = len(frames)

        self.video_label.setText(
            f"{os.path.basename(path)}\n{n} frames, {frames.frame_shape[1]}x{frames.frame_shape[0]}"
        )

        for w in (self.frame_slider, self.frame_spin):
            w.blockSignals(True)
            w.setRange(0, n - 1)
            w.setValue(0)
            w.blockSignals(False)
            w.setEnabled(n > 1)

        self.canvas.reset_measurement()
        self.canvas.clear_trajectories()
        self.tracked = None
        self.detect_btn.setEnabled(True)
        self.hist_btn.setEnabled(True)
        self.size_hist_btn.setEnabled(True)
        self.ecc_hist_btn.setEnabled(True)
        self.track_btn.setEnabled(True)
        self.detect_count_label.setText("Detected: -")
        self.track_progress.setValue(0)
        self.track_status_label.setText("")
        self.save_btn.setEnabled(False)
        self.plot_btn.setEnabled(False)
        self.abort_btn.setEnabled(False)
        self.show_frame(0)

    def _on_measured(self, text):
        self.measure_label.setText(text if text else "Click a point to measure (px)")

    def show_frame(self, idx):
        if self.frames is None:
            return
        img = self.frames[idx]
        self.canvas.show_frame(img)

    def open_advanced_settings(self):
        self.advanced_dialog.show()
        self.advanced_dialog.raise_()
        self.advanced_dialog.activateWindow()

    def _gather_locate_params(self, minmass_override=None, include_maxsize=True):
        """Collect trackpy.locate kwargs from the sliders and the advanced
        dialog. Advanced params are only included if their checkbox is on;
        otherwise they're left out so trackpy uses its own default."""
        params = dict(
            diameter=self.diameter_ctrl.value(),
            minmass=self.minmass_ctrl.value() if minmass_override is None else minmass_override,
            separation=self.separation_ctrl.value(),
            invert=self.advanced_dialog.invert_chk.isChecked(),
            engine=LOCATE_ENGINE,
        )
        if include_maxsize:
            params["maxsize"] = self.maxsize_ctrl.value()
        adv = self.advanced_dialog
        if adv.percentile_opt.is_enabled():
            params["percentile"] = adv.percentile_opt.value()
        if adv.noise_size_opt.is_enabled():
            params["noise_size"] = adv.noise_size_opt.value()
        if adv.smoothing_size_opt.is_enabled():
            params["smoothing_size"] = adv.smoothing_size_opt.value()
        if adv.topn_opt.is_enabled():
            params["topn"] = int(adv.topn_opt.value())
        return params

    def run_detection(self):
        """Run trackpy.locate on the currently selected frame and overlay results."""
        if self.frames is None:
            return
        idx = self.frame_spin.value()
        img = self.frames[idx]
        try:
            f = tp.locate(img, **self._gather_locate_params())
        except Exception as e:
            QMessageBox.warning(self, "Detection failed", str(e))
            return
        f = f[f["ecc"] < self.max_ecc_ctrl.value()]
        self.canvas.show_detections(f)
        self.detect_count_label.setText(f"Detected: {len(f)} particle(s)")

    def show_mass_histogram(self):
        """Open a new window with a mass histogram for the current frame,
        using all current settings except minmass (so the full distribution
        is visible), with the chosen minmass marked as a reference line."""
        if self.frames is None:
            return
        idx = self.frame_spin.value()
        img = self.frames[idx]
        try:
            f = tp.locate(img, **self._gather_locate_params(minmass_override=0))
        except Exception as e:
            QMessageBox.warning(self, "Detection failed", str(e))
            return
        dlg = HistogramDialog(
            f["mass"].values if len(f) else [], self.minmass_ctrl.value(), "mass", "minmass", parent=self,
        )
        dlg.show()

    def show_size_histogram(self):
        """Open a new window with a size (radius of gyration) histogram for
        the current frame, using all current settings except maxsize, with
        the chosen maxsize marked as a reference line."""
        if self.frames is None:
            return
        idx = self.frame_spin.value()
        img = self.frames[idx]
        try:
            f = tp.locate(img, **self._gather_locate_params(include_maxsize=False))
        except Exception as e:
            QMessageBox.warning(self, "Detection failed", str(e))
            return
        dlg = HistogramDialog(
            f["size"].values if len(f) else [], self.maxsize_ctrl.value(), "size", "maxsize", parent=self,
        )
        dlg.show()

    def show_ecc_histogram(self):
        """Open a new window with an eccentricity histogram for the current
        frame, using all current settings, with the chosen max eccentricity
        marked as a reference line (the eccentricity filter itself is
        applied by this GUI, not trackpy.locate, so it's naturally excluded
        here)."""
        if self.frames is None:
            return
        idx = self.frame_spin.value()
        img = self.frames[idx]
        try:
            f = tp.locate(img, **self._gather_locate_params())
        except Exception as e:
            QMessageBox.warning(self, "Detection failed", str(e))
            return
        dlg = HistogramDialog(
            f["ecc"].values if len(f) else [], self.max_ecc_ctrl.value(), "eccentricity", "max ecc", parent=self,
        )
        dlg.show()

    def _gather_link_params(self):
        """Collect trackpy.link kwargs from the main-panel sliders and the
        advanced dialog's Tracking tab (opt-in checkboxes), same convention
        as detection. adaptive_stop is always on: it's a no-cost safety net
        against oversized subnetworks (dense clusters of detections) that
        otherwise make linking hang."""
        params = dict(
            search_range=self.search_range_ctrl.value(),
            adaptive_stop=self.adaptive_stop_ctrl.value(),
            link_strategy=LINK_STRATEGY,
        )
        adv = self.advanced_dialog
        if adv.memory_opt.is_enabled():
            params["memory"] = adv.memory_opt.value()
        if adv.adaptive_step_opt.is_enabled():
            params["adaptive_step"] = adv.adaptive_step_opt.value()
        return params

    def run_tracking(self):
        """Locate particles in every frame, link them into trajectories, and
        overlay the result (in yellow) on the last frame once it's done."""
        if self.frames is None or self.video_path is None:
            return
        self.track_btn.setEnabled(False)
        self.abort_btn.setEnabled(True)
        self.track_progress.setValue(0)
        self.track_status_label.setText("Locating particles in all frames...")

        self.tracking_worker = TrackingWorker(
            self.video_path,
            self._gather_locate_params(),
            self._gather_link_params(),
            self.filter_stubs_ctrl.value(),
            self.advanced_dialog.predictor_chk.isChecked(),
            self.max_ecc_ctrl.value(),
        )
        self.tracking_worker.progress.connect(self._on_track_progress)
        self.tracking_worker.finished_ok.connect(self._on_track_finished)
        self.tracking_worker.failed.connect(self._on_track_failed)
        self.tracking_worker.start()

    def abort_tracking(self):
        """Ask the worker to stop. Only takes effect at the next per-frame
        locate callback, so it won't interrupt an in-progress link() call
        (which is normally short relative to per-frame detection)."""
        if self.tracking_worker is not None and self.tracking_worker.isRunning():
            self.tracking_worker.requestInterruption()
            self.abort_btn.setEnabled(False)
            self.track_status_label.setText("Cancelling...")

    def _on_track_progress(self, i, n):
        self.track_progress.setMaximum(n)
        self.track_progress.setValue(i)
        self.track_status_label.setText(f"Locating particles: frame {i}/{n}...")

    def _on_track_failed(self, message):
        self.track_btn.setEnabled(True)
        self.abort_btn.setEnabled(False)
        self.track_status_label.setText(f"Failed: {message}")
        if message != "Tracking cancelled.":
            QMessageBox.warning(self, "Tracking failed", message)

    def _on_track_finished(self, t):
        self.track_btn.setEnabled(True)
        self.abort_btn.setEnabled(False)
        self.tracked = t
        n_particles = t["particle"].nunique()
        self.track_status_label.setText(
            f"Done. {n_particles} trajectories, {len(t)} points."
        )
        self.save_btn.setEnabled(True)
        self.plot_btn.setEnabled(True)

        last_idx = len(self.frames) - 1
        for w in (self.frame_slider, self.frame_spin):
            w.blockSignals(True)
            w.setValue(last_idx)
            w.blockSignals(False)
        self.show_frame(last_idx)
        self.canvas.show_trajectories(t)

    def save_trajectories(self):
        """Export the linked trajectories to a CSV next to the source video,
        same base name, overwriting any existing file."""
        if self.tracked is None:
            return
        path = os.path.splitext(self.video_path)[0] + ".csv"
        try:
            self.tracked.to_csv(path, index=False)
        except Exception as e:
            QMessageBox.warning(self, "Save failed", str(e))
            return
        self.track_status_label.setText(
            f"{self.track_status_label.text()}\nSaved to {os.path.basename(path)}"
        )

    def plot_trajectories(self):
        """Open a window plotting x(t) and y(t) for every trajectory."""
        if self.tracked is None:
            return
        dlg = TrajectoryPlotDialog(self.tracked, parent=self)
        dlg.show()

    def _on_slider_changed(self, value):
        self.frame_spin.blockSignals(True)
        self.frame_spin.setValue(value)
        self.frame_spin.blockSignals(False)
        self.show_frame(value)

    def _on_spin_changed(self, value):
        self.frame_slider.blockSignals(True)
        self.frame_slider.setValue(value)
        self.frame_slider.blockSignals(False)
        self.show_frame(value)

    def closeEvent(self, event):
        if self.tracking_worker is not None and self.tracking_worker.isRunning():
            self.tracking_worker.requestInterruption()
            self.tracking_worker.wait()
        if self.frames is not None:
            self.frames.close()
        self._save_settings()
        super().closeEvent(event)

    # -------------------------------------------------------------- settings --
    def _save_settings(self):
        """Persist current control values so they survive app restarts."""
        s = QSettings(SETTINGS_ORG, SETTINGS_APP)
        s.setValue("diameter", self.diameter_ctrl.value())
        s.setValue("minmass", self.minmass_ctrl.value())
        s.setValue("separation", self.separation_ctrl.value())
        s.setValue("maxsize", self.maxsize_ctrl.value())
        s.setValue("max_ecc", self.max_ecc_ctrl.value())
        s.setValue("search_range", self.search_range_ctrl.value())
        s.setValue("filter_stubs", self.filter_stubs_ctrl.value())
        s.setValue("adaptive_stop", self.adaptive_stop_ctrl.value())

        adv = self.advanced_dialog
        s.setValue("invert", adv.invert_chk.isChecked())
        s.setValue("predictor", adv.predictor_chk.isChecked())
        for name, opt in self._advanced_opt_widgets().items():
            s.setValue(f"{name}_enabled", opt.is_enabled())
            s.setValue(f"{name}_value", opt.value())

    def _load_settings(self):
        """Restore control values saved by a previous session, if any."""
        s = QSettings(SETTINGS_ORG, SETTINGS_APP)
        self.diameter_ctrl.set_value(s.value("diameter", self.diameter_ctrl.value(), type=int))
        self.minmass_ctrl.set_value(s.value("minmass", self.minmass_ctrl.value(), type=int))
        self.separation_ctrl.set_value(s.value("separation", self.separation_ctrl.value(), type=int))
        self.maxsize_ctrl.set_value(s.value("maxsize", self.maxsize_ctrl.value(), type=float))
        self.max_ecc_ctrl.set_value(s.value("max_ecc", self.max_ecc_ctrl.value(), type=float))
        self.search_range_ctrl.set_value(s.value("search_range", self.search_range_ctrl.value(), type=int))
        self.filter_stubs_ctrl.set_value(s.value("filter_stubs", self.filter_stubs_ctrl.value(), type=int))
        self.adaptive_stop_ctrl.set_value(s.value("adaptive_stop", self.adaptive_stop_ctrl.value(), type=float))

        adv = self.advanced_dialog
        adv.invert_chk.setChecked(s.value("invert", adv.invert_chk.isChecked(), type=bool))
        adv.predictor_chk.setChecked(s.value("predictor", adv.predictor_chk.isChecked(), type=bool))
        for name, opt in self._advanced_opt_widgets().items():
            opt.checkbox.setChecked(s.value(f"{name}_enabled", opt.is_enabled(), type=bool))
            value_type = float if opt.slider_spin.decimals > 0 else int
            opt.slider_spin.set_value(s.value(f"{name}_value", opt.value(), type=value_type))

    def _advanced_opt_widgets(self):
        adv = self.advanced_dialog
        return {
            "percentile": adv.percentile_opt,
            "noise_size": adv.noise_size_opt,
            "smoothing_size": adv.smoothing_size_opt,
            "topn": adv.topn_opt,
            "memory": adv.memory_opt,
            "adaptive_step": adv.adaptive_step_opt,
        }


def main():
    # Required on Windows before any multiprocessing.Pool is created (trackpy
    # uses one internally): without it, a frozen executable re-launches the
    # whole app in each worker process instead of just running the worker.
    multiprocessing.freeze_support()

    app = QApplication(sys.argv)

    if not NUMBA_AVAILABLE:
        QMessageBox.warning(
            None, "numba not available",
            "The numba acceleration library could not be found.\n\n"
            "Detection and tracking will still work, but will run "
            "noticeably slower in pure-Python mode.",
        )

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
