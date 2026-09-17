# GRate

Real-time BPM detection for grandMA3, fed straight from your Dante network.

GRate listens to Dante Virtual Soundcard inputs, tracks the tempo of whatever is on them (drums, click, full mix), and keeps your MA3 Speed Masters locked to the live band via OSC. It can also fire a cue on every beat and send MIDI clock/notes if you'd rather trigger things that way.

Built for touring — dark UI, big readouts, runs on the same Windows machine as your DVS.

**Download the latest release:**  
[GRate-Setup.exe](https://github.com/Paragonlivedesign/GRate/releases/latest/download/GRate-Setup.exe) (installer) · [GRate.exe](https://github.com/Paragonlivedesign/GRate/releases/latest/download/GRate.exe) (portable) · [All releases](https://github.com/Paragonlivedesign/GRate/releases)

Windows may warn about an unsigned executable on first run. Click "More info" → "Run anyway".

Stable builds ship from `main`. Beta builds come from the `beta` branch and are marked as pre-releases.

## What it does

- **Tracks with channels** — each track holds multiple Dante inputs. Kick can drive the Speed Master while snare/vocal channels fire their own OSC triggers.
- **Per-channel EQ** — Kick / Snare / Vocal presets or custom Hz, plus mute, solo, and monitor-listen to dial a range in by ear.
- **Live metering** — scrolling waveform with per-channel envelopes, input level, and a large BPM readout.
- **OSC out** — sends `Master 3.x At BPM ...` to MA3, optional `Go+ Sequence ...` on every beat.
- **MIDI out** — note per beat and MIDI clock, for consoles or gear that prefer it.
- **Tempo tools** — beat divider, tap tempo with Apply, half/double, bar resync.

## Requirements

- Windows 10/11 (64-bit)
- [Dante Virtual Soundcard](https://www.audinate.com/products/software/dante-virtual-soundcard) with a license
- grandMA3 console or onPC on the same network

## Setup

### 1. Dante Virtual Soundcard

DVS must run in **WDM** mode — ASIO hides the channels from Windows apps.

1. Stop DVS
2. Set **Audio Interface → WDM**
3. Reboot if prompted, then start DVS
4. In Dante Controller, subscribe your feeds to DVS receive channels (e.g. drum bus → DVS Receive 1-2)

Feed it something rhythmic. Drums, click, or a full mix all track well. A sparse vocal on its own will not.

### 2. grandMA3

1. **Menu → In & Out → OSC**
2. Enable **Input**
3. Port `8000` (GRate's default), prefix `gma3`
4. Set **Receive Command = Yes**
5. While testing, turn on **Echo Input** and watch the System Monitor — you should see the commands arrive

GRate sends plain single OSC messages (MA3 doesn't accept bundles):

```
/gma3/cmd  "Master 3.1 At BPM 128.5"
/gma3/cmd  "Go+ Sequence 1"
```

Test against free MA3 onPC before pointing it at the real console.

### 3. MIDI (optional)

1. Create a virtual port with [loopMIDI](https://www.tobias-erichsen.de/software/loopmidi.html), or use a hardware interface
2. In GRate settings, enable MIDI and pick the port
3. On MA3: **In & Out → MIDI Remotes**, map the note to `Go+` or use Learn on a Speed Master

Heads up: MA3's MIDI Index is the note number **plus one**.

## Using it

1. Start GRate
2. On a channel, pick your DVS receive device and hit **Start**
3. Watch the waveform and BPM settle in — a few seconds of steady material is enough
4. Point the track's OSC target at your console IP and enable the trigger

Settings live in `%APPDATA%\GRate\settings.json`, so they survive updates and reinstalls.

## Known issues (1.0.1)

Still working through these — none of them block the main BPM → OSC path:

- **Monitor audio** — 1.0.1 reduces pops (larger output blocks, 30 fps UI, resample-phase fix). Start/stop or device changes can still click.
- **MIDI controller** — MIDI note-per-beat and MIDI clock are implemented, but haven't been fully validated on a live console yet. Prefer OSC for show use until this is signed off.

## Troubleshooting

**No DVS devices in the list** — DVS is in ASIO mode, or hasn't been started. Switch to WDM and restart it.

**BPM jumps around** — the source is too sparse or too quiet. Try a drum bus instead of a full quiet mix, and check the input level meter is actually moving.

**MA3 isn't reacting** — check OSC Input is enabled on the console, the port and prefix match, and both machines are on the same subnet. Echo Input in the System Monitor tells you immediately whether the messages are arriving.

**BPM is exactly half or double** — that's the nature of beat detection with some material. Use the ½ / ×2 buttons.

**Monitor pops / clicks** — improved in 1.0.1. Mute the monitor output if it still distracts; OSC/MIDI triggers keep running.

## Building from source

The full application source lives in [`source/`](source/). To run it directly:

```powershell
cd source
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m grate.main
```

To produce the exe and installer yourself:

```powershell
.\scripts\build_exe.ps1        # dist\GRate.exe (PyInstaller)
.\scripts\build_installer.ps1  # installer\output\GRate-Setup.exe (Inno Setup 6)
```

The installer script will fetch Inno Setup via winget if it isn't installed.

## Versions

| Branch | What it is |
|---|---|
| `main` | Stable. Every release here is tagged and has binaries attached. |
| `beta` | Pre-release builds for testing new features. |

Found a bug? [Open an issue](https://github.com/Paragonlivedesign/GRate/issues) with what you were doing, your DVS setup, and the BPM behaviour you saw.

## Credits

Beat tracking is built on [aubio](https://aubio.org/). BPM detection approach informed by [realtime-bpm-analyzer](https://github.com/dlepaux/realtime-bpm-analyzer) and [BPM-to-OSC](https://github.com/d00mfish/BPM-to-OSC).
