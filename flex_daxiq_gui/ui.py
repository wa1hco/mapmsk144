"""UI layout and slider handlers for the DAXIQ visualizer."""

from PyQt5 import QtWidgets, QtCore
import pyqtgraph as pg


def setup_ui(self):
    """Create the three-panel display layout."""
    self.setWindowTitle(f'FlexRadio DAXIQ - {self.center_freq_mhz:.3f} MHz')
    self.setGeometry(100, 100, 1400, 900)

    menu_bar = self.menuBar()
    file_menu = menu_bar.addMenu("&File")

    self.source_action_group = QtWidgets.QActionGroup(self)
    self.source_action_group.setExclusive(True)

    self.source_flex_action = QtWidgets.QAction("Flex Radio", self)
    self.source_flex_action.setCheckable(True)
    self.source_flex_action.setChecked(True)
    self.source_flex_action.triggered.connect(self.on_select_source_flex)
    self.source_action_group.addAction(self.source_flex_action)
    file_menu.addAction(self.source_flex_action)

    self.source_wav_action = QtWidgets.QAction("WAV File", self)
    self.source_wav_action.setCheckable(True)
    self.source_wav_action.triggered.connect(self.on_select_source_wav)
    self.source_action_group.addAction(self.source_wav_action)
    file_menu.addAction(self.source_wav_action)

    central = QtWidgets.QWidget()
    self.setCentralWidget(central)
    layout = QtWidgets.QGridLayout(central)

    pg.setConfigOptions(antialias=True)

    self.spectrogram_plot = pg.PlotWidget(title="Accumulated (15 sec snapshot)")
    self.spectrogram_plot.setLabel('left', 'Frequency', units='MHz')
    self.spectrogram_plot.setLabel('bottom', 'Time', units='s')
    self.spectrogram_img = pg.ImageItem(axisOrder='col-major')
    self.spectrogram_plot.addItem(self.spectrogram_img)
    self.spectrogram_plot.setAspectLocked(False)
    self.accumulated_energy_curve = pg.PlotCurveItem(pen=pg.mkPen('g', width=2))
    self.spectrogram_plot.addItem(self.accumulated_energy_curve)

    self.realtime_plot = pg.PlotWidget(title="Real-time (15 sec)")
    self.realtime_plot.setLabel('left', 'Frequency', units='MHz')
    self.realtime_plot.setLabel('bottom', 'Time', units='s')
    self.realtime_img = pg.ImageItem(axisOrder='col-major')
    self.realtime_plot.addItem(self.realtime_img)
    self.realtime_plot.setAspectLocked(False)
    self.realtime_energy_curve = pg.PlotCurveItem(pen=pg.mkPen('g', width=2))
    self.realtime_plot.addItem(self.realtime_energy_curve)

    colors = [
        (0, 0, 0),
        (0, 0, 64),
        (0, 0, 128),
        (0, 64, 192),
        (0, 128, 255),
        (64, 192, 255),
        (128, 255, 255),
        (255, 255, 128),
        (255, 255, 255),
    ]

    positions = [0.0, 0.125, 0.25, 0.375, 0.5, 0.625, 0.75, 0.875, 1.0]
    colormap = pg.ColorMap(positions, colors)
    self.spectrogram_img.setColorMap(colormap)
    self.realtime_img.setColorMap(colormap)

    self.accumulated_noise_plot = pg.PlotWidget(title="Accumulated Noise Floor")
    self.accumulated_noise_plot.setLabel('bottom', 'Power', units='dB')
    self.accumulated_noise_plot.setLabel('left', 'Frequency', units='MHz')
    self.accumulated_noise_curve = self.accumulated_noise_plot.plot(pen='y', width=2)

    self.realtime_noise_plot = pg.PlotWidget(title="Real-time Noise Floor")
    self.realtime_noise_plot.setLabel('bottom', 'Power', units='dB')
    self.realtime_noise_plot.setLabel('left', 'Frequency', units='MHz')
    self.realtime_noise_curve = self.realtime_noise_plot.plot(pen='c', width=2)

    control_panel = QtWidgets.QWidget()
    control_layout = QtWidgets.QVBoxLayout(control_panel)
    control_layout.setContentsMargins(10, 10, 10, 10)
    control_layout.setSpacing(15)

    title_label = QtWidgets.QLabel("<b>Color Scale</b>")
    control_layout.addWidget(title_label)

    min_label = QtWidgets.QLabel(f"Min Level: {self.min_level} dB")
    self.min_level_label = min_label
    control_layout.addWidget(min_label)

    min_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    min_slider.setMinimum(-150)
    min_slider.setMaximum(-20)
    min_slider.setValue(self.min_level)
    min_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
    min_slider.setTickInterval(10)
    min_slider.valueChanged.connect(self.on_min_level_changed)
    control_layout.addWidget(min_slider)

    max_label = QtWidgets.QLabel(f"Max Level: {self.max_level} dB")
    self.max_level_label = max_label
    control_layout.addWidget(max_label)

    max_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
    max_slider.setMinimum(-100)
    max_slider.setMaximum(0)
    max_slider.setValue(self.max_level)
    max_slider.setTickPosition(QtWidgets.QSlider.TicksBelow)
    max_slider.setTickInterval(10)
    max_slider.valueChanged.connect(self.on_max_level_changed)
    control_layout.addWidget(max_slider)

    control_layout.addStretch()

    layout.addWidget(self.spectrogram_plot, 0, 0, 1, 1)
    layout.addWidget(self.accumulated_noise_plot, 0, 1, 1, 1)
    layout.addWidget(self.realtime_plot, 1, 0, 1, 1)
    layout.addWidget(self.realtime_noise_plot, 1, 1, 1, 1)
    layout.addWidget(control_panel, 2, 1, 1, 1)

    layout.setColumnStretch(0, 3)
    layout.setColumnStretch(1, 1)
    layout.setRowStretch(0, 3)
    layout.setRowStretch(1, 3)
    layout.setRowStretch(2, 0)

    self.statusBar().showMessage('Initializing...')
    self.tuned_freq_label = QtWidgets.QLabel("Tuned: --")
    self.tuned_freq_label.setStyleSheet("QLabel { font-weight: bold; padding: 0 10px; }")
    self.statusBar().addPermanentWidget(self.tuned_freq_label)
    self.utc_clock_label = QtWidgets.QLabel()
    self.utc_clock_label.setStyleSheet("QLabel { font-weight: bold; padding: 0 10px; }")
    self.statusBar().addPermanentWidget(self.utc_clock_label)

    self.update_timer = QtCore.QTimer()
    self.update_timer.timeout.connect(self.update_displays)
    self.update_timer.start(100)


def on_min_level_changed(self, value):
    self.min_level = value
    self.min_level_label.setText(f"Min Level: {value} dB")


def on_max_level_changed(self, value):
    self.max_level = value
    self.max_level_label.setText(f"Max Level: {value} dB")


def on_select_source_flex(self):
    self.source_mode = "flex"
    self.selected_wav_path = None
    self.statusBar().showMessage("Source selected: Flex Radio")


def on_select_source_wav(self):
    file_path, _ = QtWidgets.QFileDialog.getOpenFileName(
        self,
        "Select WAV File",
        "",
        "WAV Files (*.wav);;All Files (*)",
    )

    if not file_path:
        self.source_flex_action.setChecked(True)
        self.source_mode = "flex"
        self.statusBar().showMessage("Source selected: Flex Radio")
        return

    self.source_mode = "wav"
    self.selected_wav_path = file_path
    self.statusBar().showMessage(f"Source selected: WAV File ({file_path})")
