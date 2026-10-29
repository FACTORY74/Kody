# Kody — General-Purpose Mapping Application
#
# A Python-based mapping application with a JavaScript/HTML web interface and a plugin architecture for extending functionality.
#
# Copyright (c) 2026 Factory74
#
# SPDX-License-Identifier: MIT
#
# Distributed under the MIT License. See LICENSE for details.
# Provided "AS IS", without warranty of any kind.

"""Central Kody web application hosting the map user interface."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

ROOT_DIRECTORY = Path(__file__).resolve().parents[1]
WEB_DIRECTORY = ROOT_DIRECTORY / "src_web"

app = FastAPI(title="Kody Central Product", version="0.1.0")
app.mount("/", StaticFiles(directory=WEB_DIRECTORY, html=True), name="web")
