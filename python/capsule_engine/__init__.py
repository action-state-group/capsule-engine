# SPDX-License-Identifier: Apache-2.0
"""capsule-engine: the thin single-node engine -- guard, append-only fold
compute, packs, policy activation, and the report
surfaces for AI agent actions. Ledger storage comes from
checkpointed-local-log (``cll.ledger``); the action-class registry is this
package's own ``registry``.
"""

from importlib.metadata import PackageNotFoundError as _PackageNotFoundError
from importlib.metadata import version as _metadata_version

# pyproject.toml is the one version source (Release · Prepare bumps only it);
# read it from the installed distribution instead of repeating it here.
try:
    __version__ = _metadata_version("capsule-engine")
except _PackageNotFoundError:  # imported from a source tree that is not installed
    __version__ = "0+unknown"
