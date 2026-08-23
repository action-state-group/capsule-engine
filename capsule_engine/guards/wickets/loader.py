# SPDX-License-Identifier: Apache-2.0
"""YAML front door for wicket definitions: YAML text/file -> validated WicketDefinition."""
from __future__ import annotations

from pathlib import Path

import yaml

from .definition import WicketDefinition, parse_definition
from .errors import MALFORMED_DEFINITION, WicketDefinitionError

__all__ = ["load_definition_text", "load_definition_file"]


def load_definition_text(text: str) -> WicketDefinition:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise WicketDefinitionError(MALFORMED_DEFINITION, f"invalid YAML: {exc}") from exc
    if data is None:
        raise WicketDefinitionError(MALFORMED_DEFINITION, "empty definition document")
    return parse_definition(data)


def load_definition_file(path: str | Path) -> WicketDefinition:
    return load_definition_text(Path(path).read_text())
