# SPDX-License-Identifier: Apache-2.0
"""No shipped caps default may sit more than one order of magnitude above the
consumer default it stands in for.

The consumer defaults are 25.00 per purchase and 100.00 per rolling 7 days,
in minor units of a two-decimal currency (2_500 and 10_000). ``caps/2.0.0``
shipped 10_000_000 (100,000.00) for every class: the config was present and
wrong, and a consumer's cap could never fire. This test walks every ``caps``
wicket the repo ships -- the core catalog and every catalog pack -- so a new
definition is checked by default, and only an entry named in ``EXEMPT``, with
its reason, is skipped.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from capsule_engine.guards.wickets.catalog import Catalog as WicketCatalog
from capsule_engine.guards.wickets.definition import WicketDefinition
from capsule_engine.packs import load_pack_dir

PACKAGE_DIR = Path(__file__).parent.parent / "capsule_engine"
WICKET_CATALOG_DIR = PACKAGE_DIR / "guards" / "wickets" / "catalog_defs"
PACK_CATALOG_DIR = PACKAGE_DIR / "packs" / "catalog"

CONSUMER_PER_ACTION_MINOR = 2_500
CONSUMER_WINDOW_MINOR = 10_000
MAX_FACTOR = 10

EXEMPT = {
    "caps/1.0.0": "superseded and pinned: sealed records and a downstream port cite its digest",
    "caps/2.0.0": "superseded by caps/3.0.0 and pinned: sealed records and a downstream port cite its digest",
    "caps_holds/1.0.0": "reservation-aware money.transfer cap for the holds manifest, not a consumer default",
    "payments_safety.caps/1.0.0": "merchant payout pack, not a consumer default",
}


def _shipped_caps_wickets() -> list[WicketDefinition]:
    core = [e.definition for e in WicketCatalog(WICKET_CATALOG_DIR).list_entries() if e.definition.check == "caps"]
    packs = [
        w
        for pack_dir in sorted(p.parent for p in PACK_CATALOG_DIR.glob("*/pack.yaml"))
        for w in load_pack_dir(pack_dir).constraints
        if w.check == "caps"
    ]
    by_id = {w.wicket_id: w for w in (*core, *packs)}
    return [by_id[k] for k in sorted(by_id)]


def test_every_exemption_names_a_wicket_that_still_ships():
    assert set(EXEMPT) <= {w.wicket_id for w in _shipped_caps_wickets()}


@pytest.mark.parametrize("wicket", _shipped_caps_wickets(), ids=lambda w: w.wicket_id)
def test_shipped_caps_default_is_within_ten_times_the_consumer_default(wicket):
    if wicket.wicket_id in EXEMPT:
        pytest.skip(EXEMPT[wicket.wicket_id])
    limits = [
        ("caps_minor", wicket.config.get("caps_minor") or {}, CONSUMER_WINDOW_MINOR),
        ("per_action_minor", wicket.config.get("per_action_minor") or {}, CONSUMER_PER_ACTION_MINOR),
    ]
    over = {
        f"{field}.{action_class}": value
        for field, values, consumer in limits
        for action_class, value in values.items()
        if value > MAX_FACTOR * consumer
    }
    assert over == {}, f"{wicket.wicket_id} ships defaults over {MAX_FACTOR}x the consumer default"
