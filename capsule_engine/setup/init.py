# SPDX-License-Identifier: Apache-2.0
"""``capsule setup init`` (design §3.2/§6b): stand up an instance -- a
ledger, a signing key, and a ``.capsule-setup/`` scaffold for the
declarations this instance will observe/propose/confirm/enforce over.
Zero decisions, ~10 seconds, and per-tenant capable (design §5's Pattern A:
one physically-separate engine instance per tenant, nothing but digests
leaving the tenant's own boundary).

Deliberately does NOT install a starter pack (``capsule init --pack``,
``init_cmds.py``'s pre-existing, unrelated command): a declaration compiles
directly to a ``PlanDefinition`` object (``compiler/compile.py``), not to a
materialized wicket-catalog file, so there is nothing pack-shaped for this
verb to install. What this verb creates is exactly what every other setup
verb needs to exist first: a ledger to append to, a signer to sign with,
and ``declarations.DeclarationStore``'s directory.

Per-tenant mode reuses ``tenants.init_tenant`` wholesale (design's own
instruction: this is the right seam, already built by the tenant
provisioning kit) rather than duplicating its ledger/key/activation-capsule
bring-up here.
"""
from __future__ import annotations

import secrets as secrets_mod
from dataclasses import dataclass
from pathlib import Path

from capsule_ledger.ledger import LedgerStore

from ..envcompat import env_get
from ..guards.signing import LocalSigner, key_fingerprint
from ..tenants import InitResult as TenantInitResult
from ..tenants import init_tenant
from .declarations import DeclarationStore

__all__ = ["SETUP_DIRNAME", "InstanceInitResult", "KEY_ID_ENV", "SECRET_ENV", "setup_init"]

SETUP_DIRNAME = ".capsule-setup"
LEDGER_DIRNAME = "ledger"

KEY_ID_ENV = "CAPSULE_SETUP_SIGNING_KEY_ID"
SECRET_ENV = "CAPSULE_SETUP_SIGNING_SECRET"


@dataclass(frozen=True)
class InstanceInitResult:
    setup_dir: Path
    ledger_dir: Path
    declarations_dir: Path
    key_id: str
    secret: bytes
    generated_secret: bool
    key_fingerprint: str
    tenant: TenantInitResult | None = None


def setup_init(
    project_dir: str | Path,
    *,
    tenant_id: str | None = None,
    tenants_root: str | Path | None = None,
    key_id: str | None = None,
    secret: bytes | None = None,
    operator: str = "local",
    developer: str = "capsule-setup-init",
) -> InstanceInitResult:
    """Bring up one instance. ``tenant_id`` given -> delegate to the
    existing per-tenant provisioning kit (``tenants_root`` required in that
    mode, same as ``capsule tenant init``); otherwise a single local
    instance is created directly under ``<project_dir>/.capsule-setup/``."""
    if tenant_id is not None:
        if tenants_root is None:
            raise ValueError("tenants_root is required when tenant_id is given")
        result = init_tenant(
            tenants_root, tenant_id, key_id=key_id, secret=secret, operator=operator, developer=developer
        )
        declarations_dir = DeclarationStore(result.layout.root / SETUP_DIRNAME).directory
        return InstanceInitResult(
            setup_dir=result.layout.root,
            ledger_dir=result.layout.ledger_dir,
            declarations_dir=declarations_dir,
            key_id=result.key_id,
            secret=result.secret,
            generated_secret=result.generated_secret,
            key_fingerprint=key_fingerprint(result.key_id, result.secret),
            tenant=result,
        )

    setup_dir = Path(project_dir) / SETUP_DIRNAME
    ledger_dir = setup_dir / LEDGER_DIRNAME
    resolved_key_id = key_id or env_get(KEY_ID_ENV) or "capsule-setup-key"
    secret_text = secret
    if secret_text is None:
        env_secret = env_get(SECRET_ENV)
        secret_text = env_secret.encode("utf-8") if env_secret is not None else None
    generated_secret = secret_text is None
    resolved_secret = secret_text if secret_text is not None else secrets_mod.token_hex(32).encode("utf-8")

    # Zero decisions: opening a ``LedgerStore`` at a fresh path is exactly
    # "instance exists" (``ledger/store.py``'s own docstring -- auto-inits
    # if missing). Nothing is appended here; there is nothing to record yet
    # until ``observe`` starts producing emit-layer capsules.
    with LedgerStore(ledger_dir):
        pass

    declarations_dir = DeclarationStore(setup_dir).directory
    declarations_dir.mkdir(parents=True, exist_ok=True)

    return InstanceInitResult(
        setup_dir=setup_dir,
        ledger_dir=ledger_dir,
        declarations_dir=declarations_dir,
        key_id=resolved_key_id,
        secret=resolved_secret,
        generated_secret=generated_secret,
        key_fingerprint=key_fingerprint(resolved_key_id, resolved_secret),
        tenant=None,
    )


def signer_for(result: InstanceInitResult) -> LocalSigner:
    return LocalSigner(key_id=result.key_id, secret=result.secret)
