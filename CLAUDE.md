# BackingTracks

App name is **BackingTracks** (one word) everywhere: window title, QSettings, app name.

Minimal Qt (PySide6) desktop player for band-practice backing tracks. Plays `.mp3` and `.wav` files from one folder, shown as a large-text list with waveforms; playback progress fills the waveform.

## Run

```sh
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt   # first time
.venv/bin/python main.py [folder]
```

Without a folder argument it opens the last used folder (QSettings), else the current directory.

## Layout

- `main.py` — `MainWindow`: folder loading, selection vs. loaded track, transport, keyboard shortcuts, `QMediaPlayer` wiring.
- `analysis.py` — `analyze()`: one streaming pass per file (soundfile) producing waveform peaks, duration and integrated loudness (ITU-R BS.1770 LUFS: K-weighting via scipy `sosfilt`, 400 ms blocks, absolute + relative gating). Runs on a `QThreadPool` via `AnalysisJob`. Matches ffmpeg `ebur128` output.
- `waveform.py` — `TrackWidget`, which paints title, time (right of title; `pos / total` on the loaded track) and waveform with progress fill.

## Behavior (keep these working)

- Space: play/pause the selected track; if a different track is selected than the loaded one, load and play it.
- Backspace: back to the start of the loaded track. Up/Down: move selection only (doesn't interrupt playback). Return: play selected.
- At end of track the next track is selected and loaded but waits for Space, unless the auto-advance toggle (`⇥` off / `⇉` on) is enabled. The toggle state is persisted.
- Top bar holds the settings: `Folder…`, the auto-advance toggle, and `Normalize`. Bottom bar is borderless Unicode transport buttons only.
- Normalize: sets `QAudioOutput` volume (linear, max 1.0, so it can only attenuate). Reference = quietest track's LUFS, floored at `NORMALIZE_FLOOR_LUFS` (−23). Persisted.
- Click selects, double-click plays, click on the loaded track's waveform seeks.
- Transport buttons use Unicode glyphs, not text. All buttons have `NoFocus` so Space never "clicks" them.
- Track title is the file name stem; no metadata.
- Colors come from the palette (`Accent`, `Text` with alpha) so light and dark mode both work.

## Git rules

- Never commit unless the user explicitly asks.
- Commit messages: a concise title only (about 5–10 words). No body, no co-authors, no links.
