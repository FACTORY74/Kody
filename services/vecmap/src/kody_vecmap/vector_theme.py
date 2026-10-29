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

"""Runtime Vector-map paint theme."""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


THEME_FILE = Path(__file__).resolve().parents[4] / "config" / "vecmap-theme.json"


class VectorStyle(BaseModel):
    z_index: int = Field(alias="zIndex")
    fill: str | None = None
    stroke: str | None = None
    line_width: float | None = Field(default=None, alias="lineWidth", gt=0)
    line_dash: list[float] | None = Field(default=None, alias="lineDash")
    point_radius: float | None = Field(default=None, alias="pointRadius", gt=0)
    label: bool = False
    font: str | None = None
    label_fill: str | None = Field(default=None, alias="labelFill")
    label_stroke: str | None = Field(default=None, alias="labelStroke")
    label_stroke_width: float | None = Field(default=None, alias="labelStrokeWidth", gt=0)
    label_offset_x: float | None = Field(default=None, alias="labelOffsetX")
    label_offset_y: float | None = Field(default=None, alias="labelOffsetY")
    text_align: str | None = Field(default=None, alias="textAlign")
    text_baseline: str | None = Field(default=None, alias="textBaseline")


class VectorTheme(BaseModel):
    schema_version: int = Field(alias="schemaVersion", ge=1)
    styles: dict[str, VectorStyle] = Field(min_length=1)


def load_vector_theme(path: Path = THEME_FILE) -> VectorTheme:
    try:
        return VectorTheme.model_validate_json(path.read_text(encoding="utf-8"))
    except FileNotFoundError as error:
        raise RuntimeError(f"Vector theme is missing: {path}") from error
    except ValueError as error:
        raise RuntimeError(f"Vector theme is invalid: {path}") from error
