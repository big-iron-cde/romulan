"""Romulan — host client for the Piclone 65C02 system.

Romulan assembles 32 KB ROM images from annotated hex dumps or 6502
assembly source and communicates with the Raspberry Pi Pico firmware
over USB serial. Submodules:

* :mod:`romulan.assemble` — two-pass 6502/65C02 assembler
* :mod:`romulan.build_rom` — input format detection, parsers and ROM builder
* :mod:`romulan.upload_rom` — Pico serial port auto-detection
* :mod:`romulan.protocol_v1` — v1 JSON envelope helpers
* :mod:`romulan.hardware_api` — framed serial protocol client
* :mod:`romulan.main` — CLI entry point
"""
