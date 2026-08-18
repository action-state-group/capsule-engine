# SPDX-License-Identifier: Apache-2.0
"""Hot-loading catalog: a configured directory of fold-definition YAML files.

"Hot-load" here means no caching across calls — every listing re-scans the
directory, so editing or dropping in a new definition file shows up on the
next lookup without restarting the process.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .definition import FoldDefinition
from .errors import FoldDefinitionError
from .loader import load_definition_file

DUPLICATE_FOLD_ID = "duplicate_fold_id"


@dataclass(frozen=True)
class CatalogEntry:
    definition: FoldDefinition
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
            except FoldDefinitionError as exc:
                errors.append(CatalogLoadError(source_path=path, reason=exc.reason, message=str(exc)))
                continue
            # Namespacing enforced: fold_id syntax is checked at parse time
            # (definition.FOLD_ID_RE); a namespace also MUST be unique within
            # one catalog directory.
            if definition.fold_id in seen_ids:
                errors.append(
                    CatalogLoadError(
                        source_path=path,
                        reason=DUPLICATE_FOLD_ID,
                        message=f"fold_id {definition.fold_id!r} already defined in {seen_ids[definition.fold_id]}",
                    )
                )
                continue
            seen_ids[definition.fold_id] = path
            entries.append(CatalogEntry(definition=definition, digest=digest, source_path=path))
        return entries, errors

    def list_entries(self) -> list[CatalogEntry]:
        return self._scan()[0]

    def list_errors(self) -> list[CatalogLoadError]:
        return self._scan()[1]

    def get(self, fold_id_or_digest: str) -> CatalogEntry | None:
        for entry in self.list_entries():
            if entry.definition.fold_id == fold_id_or_digest or entry.digest == fold_id_or_digest:
                return entry
        return None
