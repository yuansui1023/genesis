"""Agilent/HP 34401A digital multimeter driver package."""

from .driver import INSTRUMENT_TYPE_KEY, Agilent34401AInstrument

__all__ = ["Agilent34401AInstrument", "INSTRUMENT_TYPE_KEY"]
