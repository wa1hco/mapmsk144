"""Flex client setup, background thread runtime loop, and shutdown handling."""

import queue

from PyQt5 import QtCore

from flex_client import FlexDAXIQ


def setup_flex_client(self):
    """Initialize FlexRadio client."""
    self.flex_client = FlexDAXIQ(
        center_freq_mhz=self.center_freq_mhz,
        sample_rate=self.sample_rate,
        dax_channel=1,
    )

    self.client_thread = QtCore.QThread()
    self.client_thread.run = self.run_flex_client
    self.client_thread.setTerminationEnabled(True)
    self.client_thread.start()


def run_flex_client(self):
    """Run FlexRadio client data collection."""
    try:
        print("Starting FlexRadio client...", flush=True)
        self.flex_client.start()
        print("FlexRadio client started, waiting for packets...", flush=True)

        while self.running:
            try:
                packet = self.flex_client.sample_queue.get(timeout=1.0)
                try:
                    self.process_iq_data(packet.samples, packet.timestamp_int, packet.timestamp_frac)
                except Exception as exc:
                    print(f"Error processing IQ data: {exc}", flush=True)
                    import traceback
                    traceback.print_exc()
            except queue.Empty:
                if not self.running:
                    break
                continue
            except Exception as exc:
                print(f"Queue get error: {exc}", flush=True)
                import traceback
                traceback.print_exc()
                continue
    except Exception as exc:
        print(f"FlexClient error: {exc}", flush=True)
        import traceback
        traceback.print_exc()
        print("FlexClient thread ended, but GUI will remain open", flush=True)


def _get_tuned_frequency_mhz(self):
    """Return current tuned frequency and source label from Flex client status."""
    tuned_freq_mhz = None
    tuned_source = None
    if hasattr(self, 'flex_client') and self.flex_client:
        dax_setup = getattr(self.flex_client, '_dax_setup', None)
        if dax_setup:
            slice_freq = getattr(dax_setup, 'slice_frequency_mhz', None)
            pan_freq = getattr(dax_setup, 'pan_frequency_mhz', None)
            if slice_freq is not None:
                tuned_freq_mhz = slice_freq
                tuned_source = 'Slice'
            elif pan_freq is not None:
                tuned_freq_mhz = pan_freq
                tuned_source = 'Pan'
    return tuned_freq_mhz, tuned_source


def closeEvent(self, event):
    """Clean up on window close."""
    print("Shutting down...")
    self.running = False

    if hasattr(self, 'update_timer'):
        self.update_timer.stop()

    if hasattr(self, 'flex_client'):
        try:
            self.flex_client.stop()
        except Exception:
            pass

    if hasattr(self, 'client_thread') and self.client_thread.isRunning():
        self.client_thread.quit()
        if not self.client_thread.wait(2000):
            print("Thread did not exit cleanly, terminating...")
            self.client_thread.terminate()
            self.client_thread.wait()

    print("Shutdown complete")
    event.accept()
