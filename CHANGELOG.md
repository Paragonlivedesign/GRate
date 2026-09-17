# Changelog

## 1.0.1 — 2026-09-17

- Fix monitor crackle from resample phase wrapping and short output callbacks
- Drop UI refresh to 30 fps so the GUI thread stops starving audio
- Pack app icon and UI assets into the PyInstaller exe
- Document tracks-with-channels (this was already in 1.0.0 source)

### Known issues

- Monitor can still click on start/stop or device changes
- MIDI controller path not fully tested on a live console — prefer OSC for now

## 1.0.0 — 2026-08-09

First public release.

- Multi-lane analysis: assign different Dante inputs to different triggers
- Live scrolling waveform, input level and BPM readout per lane
- Graphic EQ per track (early — see known issues)
- Monitor output for cueing the selected lane
- OSC output to grandMA3 (`Master 3.x At BPM ...`, optional per-beat `Go+`)
- MIDI note-per-beat and MIDI clock output
- Beat divider, tap tempo, half/double, bar resync
- Settings stored in `%APPDATA%\GRate`
- Installer and portable exe

### Known issues

- Monitor audio can pop/click when starting, stopping, or switching devices
- Graphic EQ still needs work (curve, smoothing, usability)
- MIDI controller path not fully tested on a live console — prefer OSC for now
