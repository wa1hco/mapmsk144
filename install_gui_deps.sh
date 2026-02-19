#!/bin/bash
# Install dependencies for DAXIQ GUI

echo "Installing PyQt5 and pyqtgraph for DAXIQ GUI..."

# Install PyQt5 and pyqtgraph
pip3 install --user PyQt5 pyqtgraph numpy

echo ""
echo "Installation complete!"
echo ""
echo "To run the GUI:"
echo "  python3 flex_gui.py"
echo ""
echo "Or with custom frequency:"
echo "  python3 flex_gui.py --freq 50.260 --rate 48000"
