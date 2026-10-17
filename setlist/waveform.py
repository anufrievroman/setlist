"""Track row widget: title, time and waveform with playback progress."""

import numpy as np
from PySide6.QtCore import QRectF, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette
from PySide6.QtWidgets import QSizePolicy, QWidget

BAR_WIDTH = 2
BAR_GAP = 1


def format_time(seconds):
    seconds = int(max(0, seconds))
    return f"{seconds // 60}:{seconds % 60:02d}"


class TrackWidget(QWidget):
    """One row in the track list: file name above its waveform."""

    clicked = Signal(object)
    double_clicked = Signal(object)
    seek_requested = Signal(object, float)

    TITLE_HEIGHT = 32
    WAVE_HEIGHT = 35
    PADDING = 8
    WAVE_BOTTOM_GAP = 6

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
        self._bars_cache = (None, None)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.setFixedHeight(self.TITLE_HEIGHT + self.WAVE_HEIGHT + 2 * self.PADDING + self.WAVE_BOTTOM_GAP)

    def set_analysis(self, peaks, duration):
        self.peaks = peaks
        if not self.duration:
            self.duration = duration
        self._bars_cache = (None, None)
        self.update()

    def set_duration(self, duration):
        self.duration = duration
        self.update()

    def set_state(self, selected=None, current=None, playing=None):
        if selected is not None:
            self.selected = selected
        if current is not None:
            self.current = current
        if playing is not None:
            self.playing = playing
        self.update()

    def set_progress(self, progress):
        if progress != self.progress:
            self.progress = progress
            self.update()

    def _wave_rect(self):
        p = self.PADDING
        return QRectF(p + 6, p + self.TITLE_HEIGHT, self.width() - 2 * p - 12, self.WAVE_HEIGHT)

    def _bars(self, count):
        if self._bars_cache[0] == count:
            return self._bars_cache[1]
        edges = np.linspace(0, len(self.peaks), count + 1).astype(int)
        edges[1:] = np.maximum(edges[1:], edges[:-1] + 1)
        edges = np.minimum(edges, len(self.peaks))
        bars = np.array([self.peaks[a:b].max() if b > a else 0.0 for a, b in zip(edges[:-1], edges[1:])])
        self._bars_cache = (count, bars)
        return bars

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        pal = self.palette()
        accent = pal.color(QPalette.Accent)
        text_color = pal.color(QPalette.Text)

        card = QRectF(self.rect()).adjusted(4, 3, -4, -3)
        bg = QColor(text_color)
        bg.setAlpha(40 if self.selected else 12)
        painter.setPen(Qt.NoPen)
        painter.setBrush(bg)
        painter.drawRoundedRect(card, 10, 10)

        # Title
        font = QFont(self.font())
        font.setPointSize(18)
        font.setBold(self.current)
        painter.setFont(font)
        painter.setPen(text_color)
        marker = ("▶  " if self.playing else "❚❚  ") if self.current else ""
        title_rect = QRectF(self.PADDING + 6, self.PADDING, self.width() - 2 * self.PADDING - 12, self.TITLE_HEIGHT)

        time_text = format_time(self.duration) if self.duration else ""
        if self.current and self.duration:
            time_text = f"{format_time(self.progress * self.duration)} / {time_text}"
        time_font = QFont("Menlo")
        time_font.setPointSize(16)
        time_font.setBold(self.current)
        painter.save()
        painter.setFont(time_font)
        time_width = painter.fontMetrics().horizontalAdvance(time_text)
        if not self.current:
            painter.setPen(QColor(text_color.red(), text_color.green(), text_color.blue(), 160))
        painter.drawText(title_rect, Qt.AlignRight | Qt.AlignVCenter, time_text)
        painter.restore()

        title_width = int(title_rect.width() - time_width - 24)
        text = painter.fontMetrics().elidedText(marker + self.title, Qt.ElideRight, title_width)
        painter.drawText(title_rect, Qt.AlignLeft | Qt.AlignVCenter, text)

        # Waveform
        rect = self._wave_rect()
        mid = rect.center().y()
        dim = QColor(text_color)
        dim.setAlpha(90)
        if self.peaks is None:
            painter.setPen(dim)
            painter.drawLine(rect.left(), mid, rect.right(), mid)
            return

        step = BAR_WIDTH + BAR_GAP
        count = max(1, int(rect.width() // step))
        bars = self._bars(count)
        split = self.progress * count if self.current else -1
        painter.setPen(Qt.NoPen)
        half = rect.height() / 2
        for i, v in enumerate(bars):
            h = max(1.0, v * half)
            painter.setBrush(accent if i < split else dim)
            painter.drawRect(QRectF(rect.left() + i * step, mid - h, BAR_WIDTH, 2 * h))

        if self.current:
            x = rect.left() + self.progress * count * step
            painter.setPen(text_color)
            painter.drawLine(x, rect.top(), x, rect.bottom())

    def mousePressEvent(self, event):
        if event.button() != Qt.LeftButton:
            return
        rect = self._wave_rect()
        pos = event.position()
        if self.current and rect.contains(pos):
            self.seek_requested.emit(self, (pos.x() - rect.left()) / rect.width())
        else:
            self.clicked.emit(self)

    def mouseDoubleClickEvent(self, event):
        if event.button() == Qt.LeftButton and not (self.current and self._wave_rect().contains(event.position())):
            self.double_clicked.emit(self)
