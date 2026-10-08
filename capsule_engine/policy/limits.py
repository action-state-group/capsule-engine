# SPDX-License-Identifier: Apache-2.0
"""Which ``caps`` limit is in force for a decision, and where it came from.

A user's limit lives in a policy profile (``profile.py``) that an activation
record binds (``activation.py``): the record carries the profile's values and
digest and is appended, signed, to the ledger. ``ResolvedManifest`` lays the
profile over the wicket's defaults as soon as it is installed; this module
adds the one rule that depends on *when* a value was activated:

* a value at or below the limit in force when it was activated takes effect
  at that activation's timestamp;
* a value above it takes effect ``COOLING_OFF`` (12 hours) after that
  timestamp, and until then the earlier limit stays in force;
* each activation supersedes any raise an earlier one left pending.

"Earlier limit" starts at the wicket's default, so a first profile that sets
a limit above the pack's default waits too, and so does removing a profile
whose limit was below it. Lower is tighter and higher is looser, as
``packs/contract_diff.py`` classifies a profile limit; that diff reports a
class set on only one side as ``changed`` because it does not read the pack
default, and here the default is known, so every change has a direction.

Which records count. The history is every activation in the ledger, in
append order, and it is read only when a profile is involved -- the install
pins one, or some activation carries one; otherwise every limit is the
wicket's default. When it is read:

* every record must verify under the engine's own signer
  (``events.event_signature_valid``), so a record appended by anything that
  does not hold the node's key is refused, not ignored;
* timestamps must not decrease in append order, so a record cannot be
  backdated ahead of one already appended;
* the latest record must cite the installed manifest and the profile digest
  that manifest pins.

A class a record's profile does not set reads the *installed* wicket's
default, including for records made under an earlier wicket. A pack upgrade
that changes a default is therefore not itself put through the cooling-off;
a value the operator set is, across any upgrade.

``CapsLimits.in_force`` is asked at two times -- the action's own timestamp
and the engine's clock -- and returns the lower of the two limits, so a
caller cannot reach a raise early by stamping its action later, or escape a
lowering by stamping it earlier. The value comes with its provenance --
``operator_profile`` with the digest of the profile that set it, or
``definition_default`` -- plus any activated raise still waiting out the
cooling-off. The caps check seals that provenance in its evidence
(``guards/checks/caps.py``'s ``limit_sources``).
"""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from itertools import pairwise

from capsule_ledger.ledger.api import LedgerAPI, ScanQuery

from ..events.capsule import event_signature_valid
from ..guards.checks.caps import (
    LIMIT_SOURCE_DEFAULT,
    LIMIT_SOURCE_PROFILE,
    LimitSource,
    PendingRaise,
    cap_for,
)
from ..guards.classes import resolve
from ..guards.signing import Signer
from .activation import EVENT_MANIFEST_ACTIVATED
from .errors import (
    ACTIVATION_OUT_OF_ORDER,
    ACTIVATION_UNVERIFIED,
    PROFILE_DIGEST_DRIFT,
    PROFILE_UNBOUND,
    PolicyManifestError,
)
from .profile import parse_profile
from .resolve import ResolvedManifest

__all__ = [
    "COOLING_OFF",
    "ActivationRecord",
    "CapsLimits",
    "LimitInForce",
    "activation_records",
    "caps_limits",
    "read_caps_limits",
]

COOLING_OFF = timedelta(hours=12)

# The caps config keys a profile may set (``profile.CONFIGURABLE["caps"]``).
_KEYS = ("caps_minor", "per_action_minor")


@dataclass(frozen=True)
class ActivationRecord:
    """One ``policy_manifest_activated`` capsule, as appended."""

    capsule: Mapping

    @property
    def timestamp(self) -> str:
        return self.capsule["timestamp"]

    @property
    def detail(self) -> Mapping:
        return self.capsule["asg_payload"]["detail"]


@dataclass(frozen=True)
class LimitInForce:
    value_minor: int
    source: LimitSource


@dataclass(frozen=True)
class _Step:
    # Both ``None`` for the wicket's default before any activation.
    activated_at: datetime | None
    effective_at: datetime | None
    value_minor: int
    # ``None``: the wicket's default.
    profile_digest: str | None


def _parse_ts(ts: str) -> datetime:
    """An ISO-8601 timestamp; one with no UTC offset is read as UTC, the
    engine's convention for every timestamp it writes."""
    dt = datetime.fromisoformat(ts[:-1] + "+00:00" if ts.endswith("Z") else ts)
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


def _format_ts(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical(action_class: str) -> str:
    ac = resolve(action_class)
    return ac.name if ac is not None else action_class


def _source_name(step: _Step) -> str:
    return LIMIT_SOURCE_DEFAULT if step.profile_digest is None else LIMIT_SOURCE_PROFILE


def _at(steps: tuple[_Step, ...], when: datetime) -> LimitInForce:
    """The limit in force at ``when``, and the raise pending then, if any:
    one activated by ``when`` that takes effect after it."""
    current = steps[0]
    pending: _Step | None = None
    for step in steps:
        if step.effective_at is None or step.effective_at <= when:
            current = step
        elif step.activated_at is not None and step.activated_at <= when:
            pending = step
    source = LimitSource(limit_source=_source_name(current))
    if current.profile_digest is not None:
        source["profile_digest"] = current.profile_digest
    if pending is not None and pending.effective_at is not None and pending.value_minor > current.value_minor:
        raise_ = PendingRaise(
            value_minor=pending.value_minor,
            effective_at=_format_ts(pending.effective_at),
            limit_source=_source_name(pending),
        )
        if pending.profile_digest is not None:
            raise_["profile_digest"] = pending.profile_digest
        source["pending_raise"] = raise_
    return LimitInForce(value_minor=current.value_minor, source=source)


@dataclass(frozen=True)
class CapsLimits:
    """Per caps key (``caps_minor`` / ``per_action_minor``), per canonical
    action class, the limit's steps in activation order. ``effective_at`` is
    non-decreasing along each tuple."""

    steps: Mapping[str, Mapping[str, tuple[_Step, ...]]]

    def in_force(self, key: str, action_class: str | None, *, at: str, now: str) -> LimitInForce | None:
        """The ``key`` limit for ``action_class``: the lower of the limits in
        force at ``at`` (the action's timestamp) and ``now`` (the engine's
        clock), the one at ``at`` on a tie. ``None`` when the installed wicket
        sets no such limit for the class."""
        by_class = self.steps.get(key, {})
        if action_class is None or cap_for(dict.fromkeys(by_class, 0), action_class) is None:
            return None
        steps = by_class[_canonical(action_class)]
        at_action = _at(steps, _parse_ts(at))
        at_clock = _at(steps, _parse_ts(now))
        return at_clock if at_clock.value_minor < at_action.value_minor else at_action


def activation_records(ledger: LedgerAPI) -> list[ActivationRecord]:
    """Every ``policy_manifest_activated`` record in ``ledger``, in append order."""
    return [
        ActivationRecord(capsule=record.capsule)
        for record in ledger.scan(ScanQuery(action_type="fyi"))
        if (record.capsule.get("asg_payload") or {}).get("event") == EVENT_MANIFEST_ACTIVATED
    ]


def _profile_values(detail: Mapping, pack_names: set[str]) -> tuple[str | None, dict[str, dict[str, int]]]:
    """The profile digest an activation cites and the caps values it sets,
    key -> class -> value. The values are re-digested: a record whose values
    do not digest to the digest it cites is refused."""
    profile = detail.get("profile")
    if profile is None:
        return None, {}
    parsed = parse_profile(profile["values"])
    if parsed.profile_digest() != profile["profile_digest"]:
        raise PolicyManifestError(
            PROFILE_DIGEST_DRIFT,
            f"activation cites profile {profile['profile_digest']}, but its values digest to {parsed.profile_digest()}",
        )
    values: dict[str, dict[str, int]] = {}
    for entry in parsed.packs:
        if entry.pack in pack_names:
            for key, by_class in entry.parameters.get("caps", {}).items():
                values.setdefault(key, {}).update(by_class)
    return profile["profile_digest"], values


def _check_history(resolved: ResolvedManifest, activations: Sequence[ActivationRecord], signer: Signer) -> None:
    """Refuse a history this module cannot read limits from (module docstring)."""
    for record in activations:
        if not event_signature_valid(record.capsule, signer):
            raise PolicyManifestError(
                ACTIVATION_UNVERIFIED,
                f"activation {record.capsule.get('capsule_id')} does not verify under key {signer.key_id!r}",
            )
    for earlier, later in pairwise(activations):
        if _parse_ts(later.timestamp) < _parse_ts(earlier.timestamp):
            raise PolicyManifestError(
                ACTIVATION_OUT_OF_ORDER,
                f"activation {later.capsule.get('capsule_id')} at {later.timestamp} is appended after one at "
                f"{earlier.timestamp}",
            )
    latest = activations[-1].detail if activations else None
    cited = (latest.get("profile") or {}).get("profile_digest") if latest is not None else None
    if latest is None or latest.get("manifest_digest") != resolved.manifest_digest or cited != resolved.manifest.profile_digest:
        raise PolicyManifestError(
            PROFILE_UNBOUND,
            f"installed manifest {resolved.manifest_digest} (profile {resolved.manifest.profile_digest}) is not the "
            f"one the latest activation binds ({latest.get('manifest_digest') if latest else 'none recorded'}, "
            f"profile {cited}); a profile's limits apply only once an activation binds them",
        )


def caps_limits(resolved: ResolvedManifest, activations: Sequence[ActivationRecord], signer: Signer) -> CapsLimits:
    """The caps limits of ``resolved`` over time, from the ledger's activation
    records (``activation_records``), each verified under ``signer``. Raises
    ``PolicyManifestError`` when a profile is involved and the history cannot
    be read (module docstring)."""
    defaults = resolved.wicket_config("caps")
    pack_names = {p.pack_id.rsplit("/", 1)[0] for p in resolved.manifest.packs}
    involved = resolved.profile is not None or any(r.detail.get("profile") is not None for r in activations)
    history: list[tuple[datetime, str | None, dict[str, dict[str, int]]]] = []
    if involved:
        _check_history(resolved, activations, signer)
        history = [(_parse_ts(r.timestamp), *_profile_values(r.detail, pack_names)) for r in activations]

    steps: dict[str, dict[str, tuple[_Step, ...]]] = {}
    for key in _KEYS:
        for action_class, default in (defaults.get(key) or {}).items():
            schedule = [_Step(activated_at=None, effective_at=None, value_minor=default, profile_digest=None)]
            for activated_at, digest, values in history:
                set_by_profile = action_class in values.get(key, {})
                value = values[key][action_class] if set_by_profile else default
                # Drop any raise not yet in force: this activation supersedes it.
                schedule = [s for s in schedule if s.effective_at is None or s.effective_at <= activated_at]
                effective_at = activated_at if value <= schedule[-1].value_minor else activated_at + COOLING_OFF
                schedule.append(
                    _Step(
                        activated_at=activated_at,
                        effective_at=effective_at,
                        value_minor=value,
                        profile_digest=digest if set_by_profile else None,
                    )
                )
            steps.setdefault(key, {})[_canonical(action_class)] = tuple(schedule)
    return CapsLimits(steps=steps)


def read_caps_limits(resolved: ResolvedManifest, ledger: LedgerAPI, signer: Signer) -> CapsLimits:
    """``caps_limits`` over ``ledger``'s activation records as they stand now."""
    return caps_limits(resolved, activation_records(ledger), signer)
