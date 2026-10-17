"""Generate the Setlist app icon: a guitar pick with a waveform inside.

Usage: python tools/make_icon.py
Writes setlist/resources/icon.png (1024x1024) and, on macOS, setlist/resources/icon.icns.
"""

import math
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QGuiApplication, QImage, QLinearGradient, QPainter, QPainterPath, QPen

ROOT = Path(__file__).resolve().parent.parent
PNG_PATH = ROOT / "setlist" / "resources" / "icon.png"
ICNS_PATH = ROOT / "setlist" / "resources" / "icon.icns"
SIZE = 1024


def pick_path():
    """Classic 351-style pick: wide rounded top tapering to a rounded tip."""
    path = QPainterPath(QPointF(512, 128))
    path.cubicTo(716, 128, 892, 150, 892, 300)
    path.cubicTo(892, 480, 650, 790, 566, 878)
    path.cubicTo(536, 910, 488, 910, 458, 878)
    path.cubicTo(374, 790, 132, 480, 132, 300)
    path.cubicTo(132, 150, 308, 128, 512, 128)
    path.closeSubpath()
    return path


def bar_heights(count):
    """Deterministic, musical-looking amplitudes with a soft envelope."""
    heights = []
    for i in range(count):
        t = i / (count - 1)
        envelope = 0.35 + 0.65 * math.sin(math.pi * t)
        texture = 0.55 + 0.45 * abs(math.sin(i * 1.7) * math.cos(i * 0.6))
        heights.append(envelope * texture)
    return heights


def draw_icon():
    image = QImage(SIZE, SIZE, QImage.Format_ARGB32)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing)
    pick = pick_path()

    # Soft drop shadow
    for i in range(14, 0, -1):
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 6))
        painter.drawPath(pick.translated(0, 6 + i * 1.6))

    # Dark grey body, matching the app's dark interface
    gradient = QLinearGradient(QPointF(200, 128), QPointF(820, 900))
    gradient.setColorAt(0.0, QColor("#4a4a4a"))
    gradient.setColorAt(1.0, QColor("#1c1c1c"))
    painter.setBrush(gradient)
    painter.setPen(QPen(QColor(255, 255, 255, 40), 6))
    painter.drawPath(pick)
    painter.setPen(Qt.NoPen)

    # Glossy highlight on the upper half
    gloss = QLinearGradient(QPointF(0, 128), QPointF(0, 520))
    gloss.setColorAt(0.0, QColor(255, 255, 255, 30))
    gloss.setColorAt(1.0, QColor(255, 255, 255, 0))
    painter.setBrush(gloss)
    painter.drawPath(pick)

    # Waveform: played part in accent blue, the rest grey (like the app)
    painter.setClipPath(pick)
    count, bar, gap = 19, 18, 10
    left = 512 - (count * bar + (count - 1) * gap) / 2
    mid, max_half = 395, 165
    played = int(count * 0.6)
    for i, h in enumerate(bar_heights(count)):
        half = max(10, h * max_half)
        painter.setBrush(QColor("#2f7bf6") if i < played else QColor(255, 255, 255, 120))
        painter.drawRoundedRect(QRectF(left + i * (bar + gap), mid - half, bar, 2 * half), bar / 2, bar / 2)

    painter.end()
    return image


def write_icns(image):
    if sys.platform != "darwin" or not shutil.which("iconutil"):
        return
    with tempfile.TemporaryDirectory() as tmp:
        iconset = Path(tmp) / "Setlist.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                image.scaled(px, px, Qt.KeepAspectRatio, Qt.SmoothTransformation).save(str(iconset / name))
        ICNS_PATH.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(ICNS_PATH)], check=True)


def main():
    app = QGuiApplication(sys.argv)  # noqa: F841 — needed for font/painting backends
    image = draw_icon()
    PNG_PATH.parent.mkdir(parents=True, exist_ok=True)
    image.save(str(PNG_PATH))
    write_icns(image)
    print(f"Wrote {PNG_PATH.relative_to(ROOT)}" + (f" and {ICNS_PATH.relative_to(ROOT)}" if ICNS_PATH.exists() else ""))


if __name__ == "__main__":
    main()
