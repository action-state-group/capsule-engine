# SPDX-License-Identifier: Apache-2.0
"""Hot-loading catalog: a configured directory of wicket-definition YAML files
(mirrors ``folds/catalog.py`` exactly -- same hot-load contract, same
lookup-by-id-or-digest shape)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .definition import WicketDefinition
from .errors import WicketDefinitionError
from .loader import load_definition_file

DUPLICATE_WICKET_ID = "duplicate_wicket_id"

__all__ = ["CatalogEntry", "CatalogLoadError", "Catalog"]


@dataclass(frozen=True)
class CatalogEntry:
    definition: WicketDefinition
    digest: str
    source_path: Path


@dataclass(frozen=True)
class CatalogLoadError:
    source_path: Path
    reason: str
    message: str


class Catalog:
    def __init__(self, directory: str | Path) -> None:
        self.directory = Path(directory)

    def _scan(self) -> tuple[list[CatalogEntry], list[CatalogLoadError]]:
        entries: list[CatalogEntry] = []
        errors: list[CatalogLoadError] = []
        if not self.directory.is_dir():
            return entries, errors

        paths = sorted(p for p in self.directory.iterdir() if p.suffix in (".yaml", ".yml"))
        seen_ids: dict[str, Path] = {}
        for path in paths:
            try:
                definition = load_definition_file(path)
                digest = definition.definition_digest()
            except WicketDefinitionError as exc:
                errors.append(CatalogLoadError(source_path=path, reason=exc.reason, message=str(exc)))
                continue
            if definition.wicket_id in seen_ids:
                errors.append(
                    CatalogLoadError(
                        source_path=path,
                        reason=DUPLICATE_WICKET_ID,
                        message=f"wicket_id {definition.wicket_id!r} already defined in {seen_ids[definition.wicket_id]}",
                    )
                )
                continue
            seen_ids[definition.wicket_id] = path
            entries.append(CatalogEntry(definition=definition, digest=digest, source_path=path))
        return entries, errors

    def list_entries(self) -> list[CatalogEntry]:
        return self._scan()[0]

    def list_errors(self) -> list[CatalogLoadError]:
        return self._scan()[1]

    def get(self, wicket_id_or_digest: str) -> CatalogEntry | None:
        for entry in self.list_entries():
            if entry.definition.wicket_id == wicket_id_or_digest or entry.digest == wicket_id_or_digest:
                return entry
        return None
