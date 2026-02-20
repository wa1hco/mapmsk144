#!/usr/bin/env python3
"""FlexRadio DAXIQ GUI launcher."""

import logging
import signal
import sys

from PyQt5 import QtCore, QtWidgets

from daxiq_gui.visualizer import DAXIQVisualizer


def _configure_logging(level_name: str):
    level = getattr(logging, level_name.upper(), logging.INFO)
    root_logger = logging.getLogger()
    if not root_logger.handlers:
        logging.basicConfig(level=level, format='%(asctime)s %(levelname)s %(message)s')
    else:
        root_logger.setLevel(level)

    logging.getLogger('flex_client').setLevel(level)


def main():
    """Launch the DAXIQ visualizer GUI."""
    app = QtWidgets.QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(True)

    def _graceful_shutdown(_signum, _frame):
        QtCore.QTimer.singleShot(0, app.quit)

    signal.signal(signal.SIGINT, _graceful_shutdown)
    signal.signal(signal.SIGTERM, _graceful_shutdown)

    import argparse

    parser = argparse.ArgumentParser(description='FlexRadio DAXIQ Visualizer')
    parser.add_argument('--rate', type=int, default=48000,
                        help='Sample rate in Hz (default: 48000)')
    parser.add_argument('--bind-client-id', type=str, default=None,
                        help='GUI client UUID for `client bind client_id=<uuid>`')
    parser.add_argument('--bind-client', type=str, default=None,
                        help='Deprecated alias of --bind-client-id')
    parser.add_argument('--log-level', type=str, default='INFO',
                        choices=['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL'],
                        help='Logging verbosity (default: INFO)')
    args = parser.parse_args()

    _configure_logging(args.log_level)

    window = DAXIQVisualizer(
        sample_rate=args.rate,
        bind_client_id=args.bind_client_id or args.bind_client,
    )
    window.show()

    timer = QtCore.QTimer()
    timer.start(500)
    timer.timeout.connect(lambda: None)

    app._window = window

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
