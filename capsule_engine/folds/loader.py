# SPDX-License-Identifier: Apache-2.0
"""YAML front door for fold definitions: YAML text/file -> validated FoldDefinition."""
from __future__ import annotations

from pathlib import Path

import yaml

from .definition import FoldDefinition, parse_definition
from .errors import MALFORMED_DEFINITION, FoldDefinitionError


def load_definition_text(text: str) -> FoldDefinition:
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise FoldDefinitionError(MALFORMED_DEFINITION, f"invalid YAML: {exc}") from exc
    if data is None:
        raise FoldDefinitionError(MALFORMED_DEFINITION, "empty definition document")
    return parse_definition(data)


def load_definition_file(path: str | Path) -> FoldDefinition:
    return load_definition_text(Path(path).read_text())
