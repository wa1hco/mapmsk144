"""Main visualizer class and shared state for the modular DAXIQ GUI."""

import datetime
import numpy as np
from PyQt5 import QtWidgets

from .ui import setup_ui, on_min_level_changed, on_max_level_changed
from .runtime import setup_flex_client, run_flex_client, _get_tuned_frequency_mhz, closeEvent
from .processing import process_iq_data
from .displays import update_displays


class DAXIQVisualizer(QtWidgets.QMainWindow):
    """Main window with three synchronized displays for DAXIQ data."""

    setup_ui = setup_ui
    on_min_level_changed = on_min_level_changed
    on_max_level_changed = on_max_level_changed
    setup_flex_client = setup_flex_client
    run_flex_client = run_flex_client
    _get_tuned_frequency_mhz = _get_tuned_frequency_mhz
    process_iq_data = process_iq_data
    update_displays = update_displays
    closeEvent = closeEvent

    def __init__(self, center_freq_mhz=50.260, sample_rate=48000, fft_size=2048):
        super().__init__()
        self.center_freq_mhz = center_freq_mhz
        self.sample_rate = sample_rate
        self.fft_size = fft_size
        self.history_secs = 15
        self.blocks_per_sec = self.sample_rate / self.fft_size
        self.max_history = int(round(self.history_secs * self.blocks_per_sec))
        self.running = True

        self.sample_buffer = np.array([], dtype=np.complex64)

        current_time = datetime.datetime.now().timestamp()
        self.time_in_window = current_time % self.history_secs
        self.next_boundary = current_time + (self.history_secs - self.time_in_window)

        self.spectrogram_data = np.full((self.max_history, self.fft_size), -130.0)
        self.spec_staging = np.full((self.max_history, self.fft_size), -130.0)

        self.spec_boundary = int(current_time / self.history_secs)
        self.spec_staging_filled = False
        initial_index = int(self.time_in_window * self.blocks_per_sec)
        self.spec_write_index = min(max(initial_index, 0), self.max_history - 1)

        timestamp = datetime.datetime.now().strftime("%H:%M:%S")
        print(
            f"[{timestamp}] Starting at {self.time_in_window:.1f}s into 15-sec window, "
            f"next boundary in {self.history_secs - self.time_in_window:.1f}s",
            flush=True,
        )

        self.realtime_data = np.full((self.max_history, self.fft_size), -130.0)
        self.realtime_time = self.history_secs
        self.realtime_filled = False
        self._realtime_boundary = self.spec_boundary
        self.realtime_write_index = min(max(initial_index, 0), self.max_history - 1)

        self.accumulated_noise_floor = np.full(self.fft_size, -125.0)
        self.realtime_noise_floor = np.full(self.fft_size, -125.0)

        self.realtime_energy_buffer = np.full(self.max_history, np.nan)
        self.accumulated_energy_buffer = np.full(self.max_history, np.nan)
        self.energy_time_axis = np.arange(self.max_history) / self.blocks_per_sec
        self.energy_boundary = self.spec_boundary
        self.accumulated_energy_filled = False
        self.energy_write_index = min(max(initial_index, 0), self.max_history - 1)
        self.max_time = self.history_secs

        self.min_level = -90
        self.max_level = -30

        self.fft_bin_axis_mhz = np.fft.fftshift(
            np.fft.fftfreq(self.fft_size, 1 / self.sample_rate)
        ) / 1e6
        self.freq_axis = self.fft_bin_axis_mhz + self.center_freq_mhz
        self.display_center_freq_mhz = self.center_freq_mhz

        print(
            f"Frequency axis: {self.freq_axis[0]:.6f} to {self.freq_axis[-1]:.6f} MHz "
            f"(span: {(self.freq_axis[-1]-self.freq_axis[0])*1000:.1f} kHz)",
            flush=True,
        )
        print(f"Center requested: {self.center_freq_mhz:.6f} MHz", flush=True)

        self.setup_ui()
        self.setup_flex_client()

    def _map_energy_to_freq_band(self, energy_vals, freq_min, freq_max):
        """Map energy values into the bottom 10% of the periodogram height."""
        if len(energy_vals) == 0:
            return np.array([], dtype=np.float64)

        energy_min_db = float(self.min_level)
        energy_max_db = float(self.max_level)
        if energy_max_db <= energy_min_db:
            energy_max_db = energy_min_db + 1.0
        norm = (energy_vals - energy_min_db) / (energy_max_db - energy_min_db)
        norm = np.clip(norm, 0.0, 1.0)

        periodogram_height = freq_max - freq_min
        overlay_height = 0.10 * periodogram_height
        return freq_min + norm * overlay_height
