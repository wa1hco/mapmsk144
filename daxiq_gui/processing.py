"""FFT and buffer update pipeline for incoming IQ sample data."""

import time

import numpy as np


def process_iq_data(self, iq_samples, timestamp_int, timestamp_frac):
    """Process IQ samples and update data buffers using wall clock time."""
    self.sample_buffer = np.concatenate([self.sample_buffer, iq_samples])

    while len(self.sample_buffer) >= self.fft_size:
        block = self.sample_buffer[:self.fft_size]
        self.sample_buffer = self.sample_buffer[self.fft_size:]

        window = np.hanning(self.fft_size)
        spectrum = np.fft.fft(block * window)
        spectrum = np.fft.fftshift(spectrum)

        window_gain = np.sqrt(np.mean(window**2))
        magnitude = np.abs(spectrum) / (self.fft_size * window_gain)

        full_scale = 32768.0
        power_db = 20 * np.log10(magnitude / full_scale + 1e-12)

        current_wall_time = time.time()
        time_in_window = current_wall_time % self.history_secs

        spec_boundary = int(current_wall_time / self.history_secs)
        if spec_boundary != self.spec_boundary:
            self.spectrogram_data = self.spec_staging.copy()
            self.spec_staging_filled = True
            self.accumulated_noise_floor = np.percentile(self.spec_staging, 10, axis=0)

            self.spec_staging = np.full((self.max_history, self.fft_size), -130.0)
            self.spec_boundary = spec_boundary
            self.spec_write_index = min(max(int(time_in_window * self.blocks_per_sec), 0), self.max_history - 1)

            self.next_boundary = current_wall_time + (self.history_secs - (current_wall_time % self.history_secs))

        if 0 <= self.spec_write_index < self.max_history:
            self.spec_staging[self.spec_write_index] = power_db
        self.spec_write_index += 1

        realtime_boundary = int(current_wall_time / self.history_secs)
        if realtime_boundary != self._realtime_boundary:
            self.realtime_data = np.full((self.max_history, self.fft_size), -130.0)
            self._realtime_boundary = realtime_boundary
            self.realtime_write_index = min(max(int(time_in_window * self.blocks_per_sec), 0), self.max_history - 1)

        if 0 <= self.realtime_write_index < self.max_history:
            self.realtime_data[self.realtime_write_index] = power_db
            self.realtime_filled = True
        self.realtime_write_index += 1

        total_energy = float(np.max(power_db))

        energy_boundary = int(current_wall_time / self.history_secs)
        if energy_boundary != self.energy_boundary:
            self.accumulated_energy_buffer = self.realtime_energy_buffer.copy()
            self.accumulated_energy_filled = np.any(~np.isnan(self.accumulated_energy_buffer))
            self.realtime_energy_buffer = np.full(self.max_history, np.nan)
            self.energy_boundary = energy_boundary
            self.energy_write_index = min(max(int(time_in_window * self.blocks_per_sec), 0), self.max_history - 1)

        if 0 <= self.energy_write_index < self.max_history:
            self.realtime_energy_buffer[self.energy_write_index] = total_energy
        self.energy_write_index += 1
