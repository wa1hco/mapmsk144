#!/usr/bin/env python3
"""FlexRadio DAXIQ GUI launcher."""

import signal
import sys

from PyQt5 import QtCore, QtWidgets

from daxiq_gui.visualizer import DAXIQVisualizer


def main():
    """Launch the DAXIQ visualizer GUI."""
    signal.signal(signal.SIGINT, signal.SIG_DFL)

    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    import argparse

    parser = argparse.ArgumentParser(description='FlexRadio DAXIQ Visualizer')
    parser.add_argument('--freq', type=float, default=50.260,
                        help='Center frequency in MHz (default: 50.260)')
    parser.add_argument('--rate', type=int, default=48000,
                        help='Sample rate in Hz (default: 48000)')
    args = parser.parse_args()

    window = DAXIQVisualizer(
        center_freq_mhz=args.freq,
        sample_rate=args.rate,
    )
    window.show()

    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    app._window = window

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
