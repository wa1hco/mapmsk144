#!/usr/bin/env python3
"""Generate a combined 48 kHz complex MSK144 test stream from WAV burst files.

This tool:
- Reads mono WAV bursts (default from ./MSK144)
- Resamples each file from source sample rate (expected 12 kHz) to 48 kHz
- Frequency-shifts each burst from a known source center (default 1500 Hz)
  to user-selected target centers
- Sums all shifted bursts into one complex stream
- Writes complex output as .npy and optional stereo I/Q WAV
"""

from __future__ import annotations

import argparse
import wave
from pathlib import Path

import numpy as np


def read_wav_mono(path: Path) -> tuple[np.ndarray, int]:
    """Read a WAV file and return mono float32 samples in [-1, 1] and sample rate."""
    with wave.open(str(path), 'rb') as wf:
        sample_rate = wf.getframerate()
        channels = wf.getnchannels()
        sample_width = wf.getsampwidth()
        n_frames = wf.getnframes()
        raw = wf.readframes(n_frames)

    if sample_width == 1:
        data = np.frombuffer(raw, dtype=np.uint8).astype(np.float32)
        data = (data - 128.0) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
    elif sample_width == 4:
        # Try float32 first; if not finite, fall back to int32 scaling.
        float_try = np.frombuffer(raw, dtype=np.float32)
        if np.all(np.isfinite(float_try)) and np.max(np.abs(float_try)) <= 10:
            data = float_try.astype(np.float32)
            max_abs = float(np.max(np.abs(data))) if data.size else 0.0
            if max_abs > 1.0:
                data /= max_abs
        else:
            data = np.frombuffer(raw, dtype=np.int32).astype(np.float32) / 2147483648.0
    else:
        raise ValueError(f"Unsupported WAV sample width {sample_width} bytes: {path}")

    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)

    return data.astype(np.float32), sample_rate


def resample_linear(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    """Resample using linear interpolation (dependency-free fallback)."""
    if src_rate == dst_rate:
        return samples.astype(np.float32)

    if samples.size == 0:
        return np.array([], dtype=np.float32)

    out_len = int(round(samples.size * (dst_rate / src_rate)))
    old_x = np.arange(samples.size, dtype=np.float64)
    new_x = np.linspace(0, samples.size - 1, out_len, dtype=np.float64)
    out = np.interp(new_x, old_x, samples.astype(np.float64))
    return out.astype(np.float32)


def freq_shift_real_to_complex(samples: np.ndarray, shift_hz: float, sample_rate: int) -> np.ndarray:
    """Apply complex frequency translation to a real-valued signal."""
    n = np.arange(samples.size, dtype=np.float64)
    lo = np.exp(1j * 2.0 * np.pi * shift_hz * n / sample_rate)
    return samples.astype(np.complex64) * lo.astype(np.complex64)


def write_iq_wav(path: Path, iq: np.ndarray, sample_rate: int) -> None:
    """Write complex IQ as stereo int16 WAV (left=I, right=Q)."""
    i = np.real(iq)
    q = np.imag(iq)
    interleaved = np.empty(iq.size * 2, dtype=np.float32)
    interleaved[0::2] = i
    interleaved[1::2] = q

    peak = float(np.max(np.abs(interleaved))) if interleaved.size else 1.0
    scale = 0.95 / peak if peak > 0 else 1.0
    pcm = np.clip(interleaved * scale, -1.0, 1.0)
    pcm_i16 = (pcm * 32767.0).astype(np.int16)

    with wave.open(str(path), 'wb') as wf:
        wf.setnchannels(2)
        wf.setsampwidth(2)
        wf.setframerate(sample_rate)
        wf.writeframes(pcm_i16.tobytes())


def parse_float_list(text: str) -> list[float]:
    return [float(item.strip()) for item in text.split(',') if item.strip()]


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate 48 kHz combined MSK144 complex test signal")
    parser.add_argument('--input-dir', default='MSK144', help='Folder containing source WAV files')
    parser.add_argument('--pattern', default='*.wav', help='Glob pattern for source files')
    parser.add_argument('--source-center-hz', type=float, default=1500.0,
                        help='Center frequency of source bursts within each input WAV')
    parser.add_argument('--output-rate', type=int, default=48000,
                        help='Output sample rate in Hz')
    parser.add_argument('--target-centers-hz', default='-9000,-3000,3000,9000',
                        help='Comma-separated target center frequencies in Hz for each file')
    parser.add_argument('--start-times-sec', default='0,0,0,0',
                        help='Comma-separated start times (seconds) for each file in output stream')
    parser.add_argument('--output-npy', default='msk144_combined_iq_48k.npy',
                        help='Output complex .npy file path')
    parser.add_argument('--output-iq-wav', default='msk144_combined_iq_48k.wav',
                        help='Optional output stereo IQ WAV path; use empty string to skip')
    args = parser.parse_args()

    input_dir = Path(args.input_dir)
    files = sorted(input_dir.glob(args.pattern))
    if not files:
        raise SystemExit(f"No files found in {input_dir} matching {args.pattern}")

    target_centers = parse_float_list(args.target_centers_hz)
    start_times = parse_float_list(args.start_times_sec)

    if len(target_centers) != len(files):
        raise SystemExit(
            f"Need one target center per file: found {len(files)} files but {len(target_centers)} centers"
        )
    if len(start_times) != len(files):
        raise SystemExit(
            f"Need one start time per file: found {len(files)} files but {len(start_times)} start times"
        )

    prepared: list[tuple[int, np.ndarray]] = []
    max_len = 0

    print("Input files:")
    for index, path in enumerate(files):
        samples, src_rate = read_wav_mono(path)
        up = resample_linear(samples, src_rate, args.output_rate)

        shift_hz = target_centers[index] - args.source_center_hz
        shifted = freq_shift_real_to_complex(up, shift_hz, args.output_rate)

        start_idx = int(round(start_times[index] * args.output_rate))
        prepared.append((start_idx, shifted))

        end_idx = start_idx + shifted.size
        max_len = max(max_len, end_idx)

        print(
            f"  {path.name}: src_rate={src_rate} Hz, samples={samples.size}, "
            f"upsampled={up.size}, target_center={target_centers[index]:.1f} Hz, "
            f"shift={shift_hz:+.1f} Hz, start={start_times[index]:.3f} s"
        )

    combined = np.zeros(max_len, dtype=np.complex64)
    for start_idx, shifted in prepared:
        combined[start_idx:start_idx + shifted.size] += shifted

    peak = float(np.max(np.abs(combined))) if combined.size else 0.0
    if peak > 0:
        combined *= (0.95 / peak)

    out_npy = Path(args.output_npy)
    np.save(out_npy, combined)
    print(f"Wrote complex stream: {out_npy} ({combined.size} samples @ {args.output_rate} Hz)")

    if args.output_iq_wav.strip():
        out_wav = Path(args.output_iq_wav)
        write_iq_wav(out_wav, combined, args.output_rate)
        print(f"Wrote IQ WAV: {out_wav}")


if __name__ == '__main__':
    main()
