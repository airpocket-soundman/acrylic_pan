from __future__ import annotations

import io
import os
import re
import threading
import wave
from pathlib import Path

import numpy as np


SAMPLE_RATE_HZ = 44_100
NOTE_PATTERN = re.compile(r"^([A-G])(#?)([2-7])$")
SEMITONES = {"C": -9, "D": -7, "E": -5, "F": -4, "G": -2, "A": 0, "B": 2}
SOUNDFONT_PATH = Path(__file__).resolve().parent / "soundfonts" / "GeneralUserGS.sf3"
INSTRUMENT_PROGRAMS = {"piano": 0, "harpsichord": 6, "guitar": 24, "steel_drum": 114}
DRUM_NOTES = (36, 38, 42, 46, 45, 47, 49, 51, 50, 53, 55, 57)
_renderer = None
_renderer_checked = False
_renderer_lock = threading.RLock()


def _number(options: dict[str, str], name: str, default: float,
            minimum: float, maximum: float) -> float:
    try:
        value = float(options.get(name, default))
    except (TypeError, ValueError):
        value = default
    return min(maximum, max(minimum, value))


def _frequency(note: str, transpose: int) -> float:
    match = NOTE_PATTERN.match(note)
    if not match:
        return 440.0
    semitone = SEMITONES[match.group(1)] + bool(match.group(2)) + (int(match.group(3)) + transpose - 4) * 12
    return 440.0 * 2.0 ** (semitone / 12.0)


def _midi_note(note: str, transpose: int) -> int:
    match = NOTE_PATTERN.match(note)
    if not match:
        return 69
    pitch_class = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}[match.group(1)]
    pitch_class += int(bool(match.group(2)))
    return min(127, max(0, (int(match.group(3)) + transpose + 1) * 12 + pitch_class))


def _envelope(length: int, attack: float, decay: float, sustain: float,
              release: float) -> np.ndarray:
    counts = [max(1, int(value * SAMPLE_RATE_HZ)) for value in (attack, decay, release)]
    sustain_count = max(0, length - sum(counts))
    return np.concatenate((
        np.linspace(0.0, 1.0, counts[0], endpoint=False),
        np.linspace(1.0, sustain, counts[1], endpoint=False),
        np.full(sustain_count, sustain),
        np.linspace(sustain, 0.0, counts[2]),
    ))[:length]


TARGET_PEAK = 0.92
MAX_NORMALIZE_GAIN = 40.0


def _wav_bytes(signal: np.ndarray, level: float | None = None) -> bytes:
    """Encode mono 16-bit PCM.

    With ``level`` the note is scaled so its peak is ``level`` x 0.92 of full scale; the SoundFont
    renders quietly (peaks of 0.07-0.13), so every note is brought to the same loudness.
    Without it the signal is only scaled down when it would clip.
    """
    if level is None:
        peak = max(1.0, float(np.max(np.abs(signal))) / .96)
        scaled = signal / peak
    else:
        peak = float(np.max(np.abs(signal)))
        gain = min(MAX_NORMALIZE_GAIN, TARGET_PEAK * level / peak) if peak > 1e-6 else 1.0
        scaled = signal * gain
    pcm = np.rint(np.clip(scaled, -1.0, 1.0) * 32767.0).astype("<i2")
    output = io.BytesIO()
    with wave.open(output, "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(SAMPLE_RATE_HZ)
        stream.writeframes(pcm.tobytes())
    return output.getvalue()


def _register_fluidsynth_library() -> None:
    """Make a bundled FluidSynth build visible to pyFluidSynth (Windows).

    Looks at FLUIDSYNTH_DIR, then at .local/fluidsynth/*/bin in the repository.
    """
    repository = Path(__file__).resolve().parents[2]
    candidates = [Path(path) for path in (os.environ.get("FLUIDSYNTH_DIR"),) if path]
    candidates += sorted((repository / ".local" / "fluidsynth").glob("*/bin"))
    for directory in candidates:
        if directory.is_dir():
            os.environ["PATH"] = str(directory) + os.pathsep + os.environ.get("PATH", "")
            if hasattr(os, "add_dll_directory"):
                os.add_dll_directory(str(directory))
            return


class _SoundFontRenderer:
    """Thread-safe offline renderer backed by FluidSynth and GeneralUser GS."""

    def __init__(self) -> None:
        _register_fluidsynth_library()
        import fluidsynth

        self.synth = fluidsynth.Synth(gain=.55, samplerate=SAMPLE_RATE_HZ)
        self.synth.setting("synth.reverb.active", 1)
        self.synth.setting("synth.chorus.active", 1)
        self.soundfont_id = self.synth.sfload(str(SOUNDFONT_PATH))
        if self.soundfont_id < 0:
            raise RuntimeError(f"could not load SoundFont: {SOUNDFONT_PATH}")
        self.lock = threading.RLock()

    def render(self, options: dict[str, str]) -> bytes:
        instrument = options.get("instrument", "steel_drum")
        transpose = int(_number(options, "transpose", 0, -2, 2))
        velocity = _number(options, "velocity", .75, .05, 1.0)
        volume = _number(options, "volume", .70, 0.0, 1.0)
        brightness = _number(options, "brightness", .65, 0.0, 1.0)
        attack = _number(options, "attack", .005, .001, .5)
        release = _number(options, "release", .90, .03, 3.0)
        area = int(_number(options, "area", 0, 0, 11))
        length = _number(options, "length", 1.0, .25, 5.0)
        channel = 9 if instrument == "drums" else 0
        key = DRUM_NOTES[area] if instrument == "drums" else _midi_note(options.get("note", "A4"), transpose)
        bank, program = (128, 0) if instrument == "drums" else (0, INSTRUMENT_PROGRAMS.get(instrument, 0))
        held_seconds = {"drums": .20, "harpsichord": .55, "steel_drum": .85,
                        "guitar": .90, "piano": 1.15}.get(instrument, 1.0) * length
        release_seconds = min(6.0, max(.35, min(3.0, release) * max(1.0, length)))

        with self.lock:
            self.synth.system_reset()
            if self.synth.program_select(channel, self.soundfont_id, bank, program) < 0:
                raise RuntimeError(f"SoundFont preset is unavailable: bank={bank}, program={program}")
            self.synth.cc(channel, 7, 127)
            self.synth.cc(channel, 11, 127)
            self.synth.cc(channel, 74, int(round(brightness * 127)))
            self.synth.noteon(channel, key, max(1, min(127, int(round(velocity * 127)))))
            held = np.asarray(self.synth.get_samples(int(held_seconds * SAMPLE_RATE_HZ)), dtype=np.int16)
            self.synth.noteoff(channel, key)
            tail = np.asarray(self.synth.get_samples(int(release_seconds * SAMPLE_RATE_HZ)), dtype=np.int16)
            self.synth.all_sounds_off(channel)

        stereo = np.concatenate((held, tail)).reshape(-1, 2).astype(np.float64)
        signal = stereo.mean(axis=1) / 32768.0
        fade_in = min(len(signal), max(1, int(attack * SAMPLE_RATE_HZ)))
        signal[:fade_in] *= np.linspace(0.0, 1.0, fade_in)
        fade_out = min(len(signal), max(1, int(min(.12, release_seconds) * SAMPLE_RATE_HZ)))
        signal[-fade_out:] *= np.linspace(1.0, 0.0, fade_out)
        return _wav_bytes(_mix_echo(signal, options), level=volume)


def _soundfont_renderer():
    global _renderer, _renderer_checked
    with _renderer_lock:
        if not _renderer_checked:
            _renderer_checked = True
            if SOUNDFONT_PATH.is_file():
                try:
                    _renderer = _SoundFontRenderer()
                except (ImportError, OSError, RuntimeError):
                    _renderer = None
        return _renderer


def audio_backend_name() -> str:
    return "GeneralUser-GS/FluidSynth" if _soundfont_renderer() is not None else "procedural-fallback"


def initialize_audio() -> str:
    """Load the SoundFont during app startup so the first strike stays responsive."""
    return "GeneralUser-GS/FluidSynth" if _soundfont_renderer() is not None else "procedural-fallback"


def _mix_echo(signal: np.ndarray, options: dict[str, str]) -> np.ndarray:
    echo_mix = _number(options, "echo_mix", .18, 0.0, .75)
    echo_delay = int(_number(options, "echo_delay", .18, .03, .8) * SAMPLE_RATE_HZ)
    echo_feedback = _number(options, "echo_feedback", .24, 0.0, .8)
    mixed = signal.copy()
    gain = echo_mix
    for repeat in range(1, 4):
        offset = echo_delay * repeat
        if offset >= len(signal):
            break
        mixed[offset:] += signal[:-offset] * gain
        gain *= echo_feedback
    return mixed


def synthesize_note(options: dict[str, str]) -> bytes:
    """Render one note on UNO Q and return mono 16-bit PCM WAV bytes."""
    renderer = _soundfont_renderer()
    if renderer is not None:
        return renderer.render(options)
    return _synthesize_procedural(options)


def _synthesize_procedural(options: dict[str, str]) -> bytes:
    """Keep the original oscillator as a deployment-safe fallback."""
    instrument = options.get("instrument", "steel_drum")
    transpose = int(_number(options, "transpose", 0, -2, 2))
    frequency = _frequency(options.get("note", "A4"), transpose)
    velocity = _number(options, "velocity", .75, .05, 1.0)
    volume = _number(options, "volume", .70, 0.0, 1.0)
    brightness = _number(options, "brightness", .65, 0.0, 1.0)
    attack = _number(options, "attack", .005, .001, .5)
    decay = _number(options, "decay", .35, .03, 1.5)
    sustain = _number(options, "sustain", .18, 0.0, 1.0)
    release = _number(options, "release", .90, .03, 3.0)
    duration = min(4.5, attack + decay + release + .12)
    count = max(1, int(duration * SAMPLE_RATE_HZ))
    time = np.arange(count, dtype=np.float64) / SAMPLE_RATE_HZ
    envelope = _envelope(count, attack, decay, sustain, release)

    if instrument == "steel_drum":
        partials = ((1.0, .78), (2.0, .18), (3.01, .08), (4.2, .04))
        signal = sum(weight * np.sin(2 * np.pi * frequency * ratio * time) *
                     np.exp(-time * (1.2 + index * (1.4 - brightness)))
                     for index, (ratio, weight) in enumerate(partials))
    elif instrument == "harpsichord":
        harmonics = 3 + int(brightness * 7)
        signal = sum(np.sin(2 * np.pi * frequency * harmonic * time) / harmonic
                     for harmonic in range(1, harmonics + 1)) * np.exp(-time * 3.5)
    elif instrument == "piano":
        signal = (np.sin(2 * np.pi * frequency * time) +
                  .24 * np.sin(2 * np.pi * frequency * 2.01 * time) +
                  .08 * np.sin(2 * np.pi * frequency * 3.03 * time)) * np.exp(-time * .85)
    elif instrument == "guitar":
        signal = sum((.72 / harmonic) * np.sin(2 * np.pi * frequency * harmonic * time)
                     for harmonic in range(1, 7)) * np.exp(-time * 2.1)
    else:
        area = int(_number(options, "area", 0, 0, 11))
        sweep = frequency * (.35 + .65 * np.exp(-time * 18.0))
        phase = 2 * np.pi * np.cumsum(sweep) / SAMPLE_RATE_HZ
        signal = np.sin(phase) * np.exp(-time * 12.0)
        if area % 3:
            signal += np.random.default_rng(area).normal(0.0, .25, count) * np.exp(-time * 22.0)

    signal *= envelope * velocity * volume
    return _wav_bytes(_mix_echo(signal, options))
