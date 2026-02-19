# FlexRadio DAXIQ Visualizer GUI

Real-time I/Q data visualization for FlexRadio with three synchronized displays.

## Features

### Three-Panel Display:
1. **Center**: Spectrogram (15 seconds of history x frequency)
   - Shows time-frequency representation of received signals
   - Color-coded power levels (dB)
   
2. **Right**: Noise Floor vs Frequency
   - Vertical plot aligned with spectrogram frequency axis
   - Shows averaged noise floor across the band
   
3. **Bottom**: Energy vs Time  
   - Shows total received power over time
   - Useful for monitoring signal activity

## Requirements

- Python 3
- PyQt5 (already installed)
- pyqtgraph (just installed)
- numpy (already installed)

## Usage

### Basic usage (center frequency 50.260 MHz, 48 kHz sample rate):
```bash
python3 flex_gui.py
```

### Custom frequency:
```bash
python3 flex_gui.py --freq 50.260 --rate 48000
```

## Before Running

1. Make sure FlexRadio is powered on and connected to your network
2. In SmartSDR, enable DAXIQ channel 1 on slice A:
   - Open a panadapter
   - Right-click on slice A
   - Enable DAXIQ channel 1

## How It Works

The GUI connects to your FlexRadio via:
- **Discovery**: Finds radio on network via UDP broadcast
- **Control**: TCP connection on port 4992 for commands
- **Data**: UDP port 4991 for I/Q streaming

The spectrogram is updated in real-time as IQ samples are received and processed with FFT.

## Files

- `flex_gui.py` - Compatibility launcher (still run this file)
- `daxiq_gui/visualizer.py` - `DAXIQVisualizer` class and shared state
- `daxiq_gui/ui.py` - UI layout and slider handlers
- `daxiq_gui/processing.py` - FFT and buffer processing pipeline
- `daxiq_gui/displays.py` - Plot/update rendering logic
- `daxiq_gui/runtime.py` - Flex client thread lifecycle and shutdown
- `daxiq_gui/app.py` - Application entrypoint (`main`)
- `flex_client.py` - FlexRadio DAXIQ client library
- `install_gui_deps.sh` - Dependency installation script

## Troubleshooting

If the GUI doesn't display data:
1. Check that DAXIQ is enabled in SmartSDR on slice A
2. Verify the radio is on the same network as your computer
3. Check that no firewall is blocking UDP port 4991

## Controls

- The display updates automatically every 100ms
- Close the window to stop streaming and disconnect
- Status bar shows packet count and elapsed time
