# SPDX-License-Identifier: Apache-2.0
"""Wicket definitions: declarative configuration for the guard checks
(``guards/checks/*.py``), digested the same way fold definitions are."""
from .catalog import Catalog, CatalogEntry, CatalogLoadError
from .definition import KNOWN_CHECKS, WicketDefinition, parse_definition
from .errors import WicketDefinitionError
from .loader import load_definition_file, load_definition_text

__all__ = [
    "Catalog",
    "CatalogEntry",
    "CatalogLoadError",
    "WicketDefinition",
    "KNOWN_CHECKS",
    "parse_definition",
    "load_definition_text",
    "load_definition_file",
    "WicketDefinitionError",
]
