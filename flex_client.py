"""Compatibility shim for legacy imports.

All FlexRadio client implementation now lives in ``flexclient.core``.
"""

if __name__ == "__main__":
    import runpy

    runpy.run_module("flexclient.core", run_name="__main__")
else:
    from flexclient.core import *
