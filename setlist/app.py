"""Setlist — a minimal backing-track player for band practice."""

import argparse
import re
import sys
from pathlib import Path

from PySide6.QtCore import QSettings, Qt, QThreadPool, QTimer, QUrl
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPalette, QShortcut
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QApplication,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QScrollArea,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from . import __version__
from .analysis import AnalysisJob
from .waveform import TrackWidget

AUDIO_EXTENSIONS = {".mp3", ".wav"}
ICON_PATH = Path(__file__).parent / "resources" / "icon.png"
AUTO_ADVANCE_ON = "⇉"
AUTO_ADVANCE_OFF = "⇥"
# Normalization only turns tracks down (output volume can't exceed 1.0), so the
# reference is the quietest track, but never quieter than this.
NORMALIZE_FLOOR_LUFS = -23.0
SEEK_STEP_MS = 5000

TRANSPORT_STYLE = """
QPushButton { border: none; background: transparent; font-size: 40px; padding: 4px 14px; color: palette(text); }
QPushButton:pressed { color: palette(highlight); }
"""


def natural_key(name):
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", name)]


class MainWindow(QMainWindow):
    def __init__(self, start_folder=None):
        super().__init__()
        self.settings = QSettings("Setlist", "Setlist")
        self.loudness = {}
        self.tracks = []
        self.selected_index = None
        self.current_index = None
        self.folder = None

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)
        self.player.mediaStatusChanged.connect(self._on_media_status)
        self.player.playbackStateChanged.connect(self._on_playback_state)
        self.player.durationChanged.connect(self._on_duration)
        self.player.positionChanged.connect(lambda _: self._update_progress())

        self.progress_timer = QTimer(self, interval=30)
        self.progress_timer.timeout.connect(self._update_progress)

        self.pool = QThreadPool(self)
        self.pool.setMaxThreadCount(2)

        self._build_ui()
        self._build_shortcuts()

        # Apps launched from Finder start in "/", which is never a useful default.
        default = Path.cwd() if Path.cwd() != Path("/") else Path.home()
        folder = start_folder or self.settings.value("folder") or str(default)
        self.load_folder(folder)

    # ----- UI -----

    def _transport_button(self, text, tooltip, slot):
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setFocusPolicy(Qt.NoFocus)
        button.setStyleSheet(TRANSPORT_STYLE)
        button.setCursor(Qt.PointingHandCursor)
        button.clicked.connect(slot)
        return button

    def _setting_button(self, text, tooltip, slot, checkable=False):
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setFocusPolicy(Qt.NoFocus)
        button.setCheckable(checkable)
        button.setFixedSize(56, 44)
        button.clicked.connect(slot)
        self.setting_buttons.append(button)
        return button

    def _build_ui(self):
        self.setting_buttons = []
        central = QWidget()
        central.setAutoFillBackground(True)
        self.central = central
        layout = QVBoxLayout(central)
        layout.setContentsMargins(12, 12, 12, 12)

        top = QHBoxLayout()
        top.addWidget(self._setting_button("≡", "Open folder (⌘O)", self.choose_folder))
        top.addStretch(1)

        self.auto_button = self._setting_button("", "", self._on_auto_toggled, checkable=True)
        self.auto_button.setChecked(self.settings.value("auto_advance", False, type=bool))
        self._refresh_auto_button()
        top.addWidget(self.auto_button)

        self.normalize_button = self._setting_button(
            "≃",
            "Normalize: match the loudness (LUFS) of all tracks so the volume doesn't jump between them",
            self._on_normalize_toggled,
            checkable=True,
        )
        self.normalize_button.setChecked(self.settings.value("normalize", True, type=bool))
        top.addWidget(self.normalize_button)
        layout.addLayout(top)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFocusPolicy(Qt.NoFocus)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.viewport().setAutoFillBackground(False)
        self.list_container = QWidget()
        self.list_layout = QVBoxLayout(self.list_container)
        self.list_layout.setContentsMargins(0, 0, 0, 0)
        self.list_layout.setSpacing(4)
        self.list_layout.addStretch(1)
        self.scroll.setWidget(self.list_container)

        self.empty_label = QLabel("No mp3 or wav files in this folder")
        self.empty_label.setAlignment(Qt.AlignCenter)
        font = self.empty_label.font()
        font.setPointSize(14)
        self.empty_label.setFont(font)

        self.list_stack = QStackedWidget()
        self.list_stack.addWidget(self.scroll)
        self.list_stack.addWidget(self.empty_label)
        layout.addWidget(self.list_stack, 1)

        transport = QHBoxLayout()
        transport.addStretch(1)
        transport.addWidget(self._transport_button("⏮", "Previous track", self.previous_track))
        transport.addWidget(self._transport_button("▶", "Play (Space)", self.play))
        transport.addWidget(self._transport_button("⏸", "Pause (Space)", self.player.pause))
        transport.addWidget(self._transport_button("⏹", "Stop", self.stop))
        transport.addWidget(self._transport_button("⏭", "Next track", self.next_track))
        transport.addStretch(1)
        layout.addLayout(transport)

        self.setCentralWidget(central)
        self.resize(900, 700)
        geometry = self.settings.value("geometry")
        if geometry:
            self.restoreGeometry(geometry)
        self._apply_theme()
        QApplication.styleHints().colorSchemeChanged.connect(lambda _: self._apply_theme())

    def _apply_theme(self):
        """Use the system's slightly darker 'Mid' color as background in dark mode."""
        dark = QApplication.styleHints().colorScheme() == Qt.ColorScheme.Dark
        self.central.setBackgroundRole(QPalette.Mid if dark else QPalette.Window)
        pal = self.empty_label.palette()
        gray = QColor(QApplication.palette().color(QPalette.WindowText))
        gray.setAlpha(110)
        pal.setColor(QPalette.WindowText, gray)
        self.empty_label.setPalette(pal)

        text = QApplication.palette().color(QPalette.Text)
        rgb = f"{text.red()}, {text.green()}, {text.blue()}"
        style = f"""
            QPushButton {{ border: none; border-radius: 8px; font-size: 26px; background: rgba({rgb}, 0.08); }}
            QPushButton:hover {{ background: rgba({rgb}, 0.14); }}
            QPushButton:pressed {{ background: rgba({rgb}, 0.22); }}
            QPushButton:checked {{ background: rgba({rgb}, 0.30); }}
            QPushButton:checked:hover {{ background: rgba({rgb}, 0.36); }}
        """
        for button in self.setting_buttons:
            button.setStyleSheet(style)

    def _build_shortcuts(self):
        for key, slot in (
            (Qt.Key_Space, self.toggle_play),
            (Qt.Key_Backspace, self.restart),
            (Qt.Key_Up, lambda: self.move_selection(-1)),
            (Qt.Key_Down, lambda: self.move_selection(1)),
            (Qt.Key_Left, lambda: self.seek_by(-SEEK_STEP_MS)),
            (Qt.Key_Right, lambda: self.seek_by(SEEK_STEP_MS)),
            (Qt.Key_Return, self.play),
        ):
            shortcut = QShortcut(QKeySequence(key), self)
            shortcut.setContext(Qt.ApplicationShortcut)
            shortcut.activated.connect(slot)
        QShortcut(QKeySequence.Open, self, activated=self.choose_folder)

    def _refresh_auto_button(self):
        on = self.auto_button.isChecked()
        self.auto_button.setText(AUTO_ADVANCE_ON if on else AUTO_ADVANCE_OFF)
        self.auto_button.setToolTip(
            "Next track starts automatically" if on else "After each track, wait for Space before playing the next"
        )

    def save_window_state(self):
        self.settings.setValue("geometry", self.saveGeometry())
        self.settings.sync()

    def closeEvent(self, event):
        self.save_window_state()
        super().closeEvent(event)

    def _on_auto_toggled(self):
        self._refresh_auto_button()
        self.settings.setValue("auto_advance", self.auto_button.isChecked())

    def _on_normalize_toggled(self):
        self.settings.setValue("normalize", self.normalize_button.isChecked())
        self._apply_volume()

    def _apply_volume(self):
        """Set output volume so the loaded track matches the folder's reference loudness."""
        gain = 1.0
        if self.normalize_button.isChecked() and self.current_index is not None:
            levels = [v for v in self.loudness.values() if v is not None]
            track_level = self.loudness.get(self.tracks[self.current_index].path)
            if levels and track_level is not None:
                reference = max(min(levels), NORMALIZE_FLOOR_LUFS)
                gain = min(1.0, 10 ** ((reference - track_level) / 20))
        self.audio_output.setVolume(gain)

    # ----- Folder / tracks -----

    def choose_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Choose tracks folder", self.folder or str(Path.home()))
        if folder:
            self.load_folder(folder)

    def load_folder(self, folder):
        path = Path(folder).expanduser()
        if not path.is_dir():
            path = Path.home()
        self.folder = str(path)
        self.settings.setValue("folder", self.folder)
        self.setWindowTitle(f"Setlist — {path.name}")

        self.player.stop()
        self.player.setSource(QUrl())
        self.current_index = None
        self.selected_index = None
        for track in self.tracks:
            track.deleteLater()
        self.tracks = []
        self.loudness = {}
        self.pool.clear()

        files = sorted(
            (f for f in path.iterdir() if f.is_file() and f.suffix.lower() in AUDIO_EXTENSIONS),
            key=lambda f: natural_key(f.name),
        )
        for i, f in enumerate(files):
            track = TrackWidget(str(f), f.stem)
            track.clicked.connect(self._on_track_clicked)
            track.double_clicked.connect(self._on_track_double_clicked)
            track.seek_requested.connect(self._on_seek_requested)
            self.list_layout.insertWidget(self.list_layout.count() - 1, track)
            self.tracks.append(track)
            job = AnalysisJob(str(f))
            job.signals.done.connect(self._on_analysis)
            self.pool.start(job)

        self.list_stack.setCurrentWidget(self.scroll if self.tracks else self.empty_label)
        if self.tracks:
            self.select(0)
            self.load(0)
        self._update_progress()

    def _on_analysis(self, path, peaks, duration, loudness):
        for track in self.tracks:
            if track.path == path:
                track.set_analysis(peaks, duration)
                self.loudness[path] = loudness
                self._apply_volume()

    # ----- Selection / loading -----

    def select(self, index):
        if not self.tracks:
            return
        index = max(0, min(index, len(self.tracks) - 1))
        if self.selected_index is not None and self.selected_index < len(self.tracks):
            self.tracks[self.selected_index].set_state(selected=False)
        self.selected_index = index
        self.tracks[index].set_state(selected=True)
        self.scroll.ensureWidgetVisible(self.tracks[index], 0, 10)

    def load(self, index):
        if self.current_index is not None:
            self.tracks[self.current_index].set_state(current=False, playing=False)
            self.tracks[self.current_index].set_progress(0.0)
        self.current_index = index
        self.tracks[index].set_state(current=True, playing=False)
        self.player.setSource(QUrl.fromLocalFile(self.tracks[index].path))
        self._apply_volume()
        self._update_progress()

    def move_selection(self, delta):
        if self.selected_index is not None:
            self.select(self.selected_index + delta)

    # ----- Transport -----

    def _is_playing(self):
        return self.player.playbackState() == QMediaPlayer.PlayingState

    def play(self):
        if self.selected_index is None:
            return
        if self.current_index != self.selected_index:
            self.load(self.selected_index)
        self.player.play()

    def toggle_play(self):
        if self.selected_index is None:
            return
        if self.current_index == self.selected_index and self._is_playing():
            self.player.pause()
        else:
            self.play()

    def stop(self):
        self.player.stop()
        self.player.setPosition(0)
        self._update_progress()

    def seek_by(self, delta_ms):
        duration = self.player.duration()
        if self.current_index is None or duration <= 0:
            return
        self.player.setPosition(max(0, min(self.player.position() + delta_ms, duration - 1)))
        self._update_progress()

    def restart(self):
        if self.current_index is not None:
            self.player.setPosition(0)
            self._update_progress()

    def _jump(self, delta):
        if not self.tracks:
            return
        base = self.current_index if self.current_index is not None else self.selected_index
        index = max(0, min(base + delta, len(self.tracks) - 1))
        was_playing = self._is_playing()
        self.select(index)
        self.load(index)
        if was_playing:
            self.player.play()

    def next_track(self):
        self._jump(1)

    def previous_track(self):
        self._jump(-1)

    # ----- Player callbacks -----

    def _on_media_status(self, status):
        if status != QMediaPlayer.EndOfMedia or self.current_index is None:
            return
        next_index = self.current_index + 1
        if next_index >= len(self.tracks):
            self.player.stop()
            self.tracks[self.current_index].set_progress(0.0)
            return
        self.select(next_index)
        self.load(next_index)
        if self.auto_button.isChecked():
            self.player.play()

    def _on_playback_state(self, state):
        playing = state == QMediaPlayer.PlayingState
        if self.current_index is not None:
            self.tracks[self.current_index].set_state(playing=playing)
        if playing:
            self.progress_timer.start()
        else:
            self.progress_timer.stop()
        self._update_progress()

    def _on_track_clicked(self, track):
        self.select(self.tracks.index(track))

    def _on_track_double_clicked(self, track):
        self.select(self.tracks.index(track))
        self.load(self.selected_index)
        self.player.play()

    def _on_seek_requested(self, track, fraction):
        self.select(self.tracks.index(track))
        duration = self.player.duration()
        if duration > 0:
            self.player.setPosition(int(max(0.0, min(1.0, fraction)) * duration))
            self._update_progress()

    def _on_duration(self, duration):
        if self.current_index is not None and duration > 0:
            self.tracks[self.current_index].set_duration(duration / 1000)
        self._update_progress()

    def _update_progress(self):
        duration = self.player.duration()
        position = self.player.position()
        if self.current_index is not None and duration > 0:
            self.tracks[self.current_index].set_progress(min(1.0, position / duration))


def parse_args():
    parser = argparse.ArgumentParser(prog="setlist", description="Backing-track player for band practice.")
    parser.add_argument("folder", nargs="?", help="folder with .mp3/.wav tracks (default: last used)")
    parser.add_argument(
        "--install-app",
        nargs="?",
        const="",
        metavar="DIR",
        help="macOS: create Setlist.app in ~/Applications (or DIR) and exit",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser.parse_known_args()[0]


def main():
    args = parse_args()
    if args.install_app is not None:
        from .macos_app import install_app

        print(f"Installed {install_app(args.install_app or None)}")
        return

    app = QApplication(sys.argv)
    app.setApplicationName("Setlist")
    app.setWindowIcon(QIcon(str(ICON_PATH)))
    window = MainWindow(args.folder)
    app.aboutToQuit.connect(window.save_window_state)  # ⌘Q may skip closeEvent
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
