"""Single-pass track analysis: waveform envelope, duration and loudness (LUFS)."""

import math

import numpy as np
import soundfile as sf
from PySide6.QtCore import QObject, QRunnable, Signal
from scipy.signal import sosfilt

PEAK_BUCKETS = 3000
ABSOLUTE_GATE = -70.0
RELATIVE_GATE = -10.0
# Waveform display: per-bucket RMS in dB, stretched over the track's own range so
# heavily limited masters still show their quiet and loud parts.
DISPLAY_FLOOR = 0.06  # height of the quietest non-silent part
DISPLAY_MIN_RANGE_DB = 6.0  # don't stretch flatter tracks further than this
DISPLAY_MAX_RANGE_DB = 40.0
DISPLAY_LOW_PERCENTILE = 20  # quieter parts (intros, breaks) shrink to the floor


def k_weighting_sos(rate):
    """ITU-R BS.1770 K-weighting (high shelf + high pass) as second-order sections."""
    # High shelf: +4 dB above ~1.5 kHz
    gain, q, fc = 4.0, 1 / math.sqrt(2), 1500.0
    a = 10 ** (gain / 40)
    w0 = 2 * math.pi * fc / rate
    alpha = math.sin(w0) / (2 * q)
    cos = math.cos(w0)
    shelf_b = [
        a * ((a + 1) + (a - 1) * cos + 2 * math.sqrt(a) * alpha),
        -2 * a * ((a - 1) + (a + 1) * cos),
        a * ((a + 1) + (a - 1) * cos - 2 * math.sqrt(a) * alpha),
    ]
    shelf_a = [
        (a + 1) - (a - 1) * cos + 2 * math.sqrt(a) * alpha,
        2 * ((a - 1) - (a + 1) * cos),
        (a + 1) - (a - 1) * cos - 2 * math.sqrt(a) * alpha,
    ]
    # High pass at ~38 Hz
    q, fc = 0.5, 38.0
    w0 = 2 * math.pi * fc / rate
    alpha = math.sin(w0) / (2 * q)
    cos = math.cos(w0)
    hp_b = [(1 + cos) / 2, -(1 + cos), (1 + cos) / 2]
    hp_a = [1 + alpha, -2 * cos, 1 - alpha]

    def section(b, a):
        return [c / a[0] for c in b] + [1.0] + [c / a[0] for c in a[1:]]

    return np.array([section(shelf_b, shelf_a), section(hp_b, hp_a)])


def integrated_loudness(hop_energy, hop):
    """Gated integrated loudness from per-100ms channel-summed energy sums."""
    if len(hop_energy) < 4:
        return None
    sums = np.convolve(hop_energy, np.ones(4), mode="valid") / (4 * hop)
    with np.errstate(divide="ignore"):
        block_lufs = -0.691 + 10 * np.log10(sums)
    gated = sums[block_lufs > ABSOLUTE_GATE]
    if not len(gated):
        return None
    threshold = -0.691 + 10 * np.log10(gated.mean()) + RELATIVE_GATE
    gated = sums[(block_lufs > ABSOLUTE_GATE) & (block_lufs > threshold)]
    if not len(gated):
        return None
    return float(-0.691 + 10 * np.log10(gated.mean()))


def display_envelope(power):
    """Map per-bucket mean power to 0..1 waveform heights (dB, contrast-stretched per track)."""
    db = 10 * np.log10(np.maximum(power, 1e-12))
    top = db.max()
    audible = db[db > top - DISPLAY_MAX_RANGE_DB]
    low = np.percentile(audible, DISPLAY_LOW_PERCENTILE) if len(audible) else top - DISPLAY_MAX_RANGE_DB
    low = min(max(low, top - DISPLAY_MAX_RANGE_DB), top - DISPLAY_MIN_RANGE_DB)
    level = (db - low) / (top - low)
    return np.where(db > top - DISPLAY_MAX_RANGE_DB, DISPLAY_FLOOR + (1 - DISPLAY_FLOOR) * np.clip(level, 0, 1), 0.0)


def analyze(path, buckets=PEAK_BUCKETS):
    """Return (waveform envelope 0..1, duration in seconds, integrated LUFS or None)."""
    info = sf.info(path)
    rate, channels = info.samplerate, info.channels
    bucket = max(1, info.frames // buckets)
    hop = int(rate * 0.1)

    sos = k_weighting_sos(rate)
    zi = np.zeros((sos.shape[0], 2, channels))  # filter state carried across blocks

    power_parts, hop_energy = [], []
    leftover = np.zeros(0)
    frames = 0
    for block in sf.blocks(path, blocksize=bucket * 256, dtype="float32", always_2d=True):
        frames += len(block)
        mono = (block.astype(np.float64) ** 2).mean(axis=1)
        whole = len(mono) // bucket * bucket
        if whole:
            power_parts.append(mono[:whole].reshape(-1, bucket).mean(axis=1))
        if whole < len(mono):
            power_parts.append(mono[whole:].mean(keepdims=True))

        weighted, zi = sosfilt(sos, block.astype(np.float64), axis=0, zi=zi)
        energy = np.concatenate([leftover, (weighted**2).sum(axis=1)])
        usable = len(energy) // hop * hop
        if usable:
            hop_energy.append(energy[:usable].reshape(-1, hop).sum(axis=1))
        leftover = energy[usable:]

    peaks = display_envelope(np.concatenate(power_parts)) if power_parts else np.zeros(1)
    loudness = integrated_loudness(np.concatenate(hop_energy) if hop_energy else np.zeros(0), hop)
    return peaks, frames / rate, loudness


class AnalysisSignals(QObject):
    done = Signal(str, object, float, object)


class AnalysisJob(QRunnable):
    def __init__(self, path):
        super().__init__()
        self.path = path
        self.signals = AnalysisSignals()

    def run(self):
        try:
            peaks, duration, loudness = analyze(self.path)
        except Exception:
            peaks, duration, loudness = None, 0.0, None
        self.signals.done.emit(self.path, peaks, duration, loudness)
