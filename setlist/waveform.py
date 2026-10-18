"""Track row widget: title, time and waveform with playback progress."""

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPainterPath, QPalette
from PySide6.QtWidgets import QApplication, QSizePolicy, QWidget

POINT_SPACING = 2  # px between envelope points


def format_time(seconds):
    seconds = int(max(0, seconds))
    if seconds >= 3600:
        return f"{seconds // 3600}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"
    return f"{seconds // 60}:{seconds % 60:02d}"


class TrackWidget(QWidget):
    """One row in the track list: file name above its waveform."""

    clicked = Signal(object)
    double_clicked = Signal(object)
    switch_requested = Signal(object)  # shift+click: jump here at the same time position
    seek_requested = Signal(object, float)
    drag_moved = Signal(object, QPointF)  # track, global cursor position
    drag_finished = Signal(object)

    # Sizes for the big rehearsal view; `scale` shrinks them for the compact view.
    TITLE_HEIGHT = 32
    WAVE_HEIGHT = 35
    PADDING = 8
    WAVE_BOTTOM_GAP = 6
    TITLE_POINT_SIZE = 18
    TIME_POINT_SIZE = 16

    def __init__(self, path, title, parent=None):
        super().__init__(parent)
        self.path = path
        self.title = title
        self.peaks = None
        self.duration = 0.0
        self.progress = 0.0
        self.selected = False
        self.current = False
        self.playing = False
        self.paused = False
        self.dragging = False
        self._press_pos = None
        self._path_cache = (None, None)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.set_scale(1.0)

    def set_scale(self, scale):
        self.scale = scale
        self.setFixedHeight(
            self._px(self.TITLE_HEIGHT + self.WAVE_HEIGHT + 2 * self.PADDING + self.WAVE_BOTTOM_GAP)
        )
        self.update()

    def _px(self, value):
        return max(1, round(value * self.scale))

    def set_analysis(self, peaks, duration):
        self.peaks = peaks
        if not self.duration:
            self.duration = duration
        self._path_cache = (None, None)
        self.update()

    def set_duration(self, duration):
        self.duration = duration
        self.update()

    def set_state(self, selected=None, current=None, playing=None, paused=None):
        if selected is not None:
            self.selected = selected
        if current is not None:
            self.current = current
        if playing is not None:
            self.playing = playing
        if paused is not None:
            self.paused = paused
        self.update()

    def set_progress(self, progress):
        if progress != self.progress:
            self.progress = progress
            self.update()

    def _wave_rect(self):
        p = self._px(self.PADDING)
        inset = self._px(6)
        return QRectF(
            p + inset, p + self._px(self.TITLE_HEIGHT), self.width() - 2 * p - 2 * inset, self._px(self.WAVE_HEIGHT)
        )

    def _wave_path(self, rect):
        """Closed mirrored outline of the peak envelope, cached per waveform rect."""
        key = (rect.left(), rect.top(), rect.width(), rect.height())
        if self._path_cache[0] == key:
            return self._path_cache[1]
        count = max(2, int(rect.width() // POINT_SPACING) + 1)
        edges = np.linspace(0, len(self.peaks), count + 1).astype(int)
        edges[1:] = np.maximum(edges[1:], edges[:-1] + 1)
        edges = np.minimum(edges, len(self.peaks))
        env = np.array([self.peaks[a:b].mean() if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])])
        env = np.convolve(np.pad(env, 1, mode="edge"), [0.25, 0.5, 0.25], mode="valid")  # soften spikes
        half = rect.height() / 2
        mid = rect.center().y()
        heights = np.maximum(0.5, env * half)
        xs = np.linspace(rect.left(), rect.right(), count)
        path = QPainterPath(QPointF(xs[0], mid))
        for x, h in zip(xs, heights):
            path.lineTo(x, mid - h)
        path.lineTo(xs[-1], mid)
        for x, h in zip(xs[::-1], heights[::-1]):
            path.lineTo(x, mid + h)
        path.closeSubpath()
        self._path_cache = (key, path)
        return path

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        accent = pal.color(QPalette.Accent)
        text_color = pal.color(QPalette.Text)

        card = QRectF(self.rect()).adjusted(4, 3, -4, -3)
        bg = QColor(text_color)
        bg.setAlpha(70 if self.dragging else 40 if self.selected else 12)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        radius = self._px(10)
        painter.drawRoundedRect(card, radius, radius)

        # Title
        font = QFont(self.font())
        font.setPointSize(self._px(self.TITLE_POINT_SIZE))
        painter.setFont(font)
        painter.setPen(text_color)
        # Loaded track only: ♫ while playing, ⏸ when paused, nothing when stopped.
        marker = ("♫" if self.playing else "⏸" if self.paused else "") if self.current else ""
        wave_rect = self._wave_rect()
        title_rect = QRectF(wave_rect.left(), self._px(self.PADDING), wave_rect.width(), self._px(self.TITLE_HEIGHT))

        time_text = format_time(self.duration) if self.duration else ""
        if self.current and self.duration:
            time_text = f"{format_time(self.progress * self.duration)} / {time_text}"
        time_font = QFont("Menlo")
        time_font.setPointSize(self._px(self.TIME_POINT_SIZE))
        painter.save()
        painter.setFont(time_font)
        time_width = painter.fontMetrics().horizontalAdvance(time_text)
        if not self.current:
            painter.setPen(QColor(text_color.red(), text_color.green(), text_color.blue(), 160))
        painter.drawText(title_rect, Qt.AlignRight | Qt.AlignVCenter, time_text)
        painter.restore()

        # Title on the left, then a quiet play/pause marker at the title's size.
        gap = self._px(10)
        marker_width = painter.fontMetrics().horizontalAdvance(marker)
        room = title_rect.width() - time_width - self._px(24) - (marker_width + gap if marker else 0)
        text = painter.fontMetrics().elidedText(self.title, Qt.ElideRight, int(room))
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, text)
        if marker:
            marker_rect = title_rect.adjusted(painter.fontMetrics().horizontalAdvance(text) + gap, 0, 0, 0)
            painter.drawText(marker_rect, Qt.AlignLeft | Qt.AlignVCenter, marker)

        # Waveform
        rect = wave_rect
        mid = rect.center().y()
        dim = QColor(text_color)
        dim.setAlpha(90)
        if self.peaks is None:
            painter.setPen(dim)
            painter.drawLine(rect.left(), mid, rect.right(), mid)
            return

        path = self._wave_path(rect)
        full = QRectF(self.rect())
        split = rect.left() + self.progress * rect.width() if self.current else full.left()
        for color, clip in (
            (accent, QRectF(full.left(), full.top(), split - full.left(), full.height())),
            (dim, QRectF(split, full.top(), full.right() - split, full.height())),
        ):
            if clip.width() <= 0:
                continue
            painter.save()
            painter.setClipRect(clip)
            fill = QColor(color)
            # Quieter fill on rows that are neither selected nor loaded.
            active = self.selected or self.current
            if color is dim:
                fill.setAlpha(110 if active else 55)
            painter.setBrush(fill)
            painter.setPen(Qt.NoPen)
            painter.drawPath(path)
            painter.restore()

        if self.current:
            painter.setPen(text_color)
            painter.drawLine(split, rect.top(), split, rect.bottom())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        rect = self._wave_rect()
        pos = event.position()
        if self.current and rect.contains(pos):
            self.seek_requested.emit(self, (pos.x() - rect.left()) / rect.width())
        elif event.modifiers() & Qt.ShiftModifier:
            self.switch_requested.emit(self)
        else:
            self.clicked.emit(self)
            self._press_pos = pos  # may become a drag to reorder

    def mouseMoveEvent(self, event):
        if self._press_pos is None or not event.buttons() & Qt.LeftButton:
            return
        if not self.dragging:
            if (event.position() - self._press_pos).manhattanLength() < QApplication.startDragDistance():
                return
            self.dragging = True
            self.setCursor(Qt.ClosedHandCursor)
            self.raise_()
            self.update()
        self.drag_moved.emit(self, event.globalPosition())

    def mouseReleaseEvent(self, event):
        self._press_pos = None
        if self.dragging:
            self.dragging = False
            self.unsetCursor()
            self.update()
            self.drag_finished.emit(self)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and not (self.current and self._wave_rect().contains(event.position())):
            self.double_clicked.emit(self)
