# import-pure: no filesystem mutation
"""Compat shim: the display-register pass lives in `pancratius.passes.register`."""

from pancratius.passes.register import fold_quote_registers as display_register_blocks

__all__ = ("display_register_blocks",)
