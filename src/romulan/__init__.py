"""Romulan — host client for the Piclone 65C02 system.

Romulan assembles 32 KB ROM images from annotated hex dumps and communicates
with the Raspberry Pi Pico firmware over USB serial. Submodules:

* :mod:`romulan.build_rom` — hex dump parser and ROM builder
* :mod:`romulan.upload_rom` — plain-text upload protocol and port detection
* :mod:`romulan.protocol_v1` — v1 JSON envelope helpers
* :mod:`romulan.hardware_api` — framed serial protocol client
* :mod:`romulan.main` — CLI entry point
"""
