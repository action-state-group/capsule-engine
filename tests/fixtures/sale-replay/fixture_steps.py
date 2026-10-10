# SPDX-License-Identifier: Apache-2.0
"""Documented fixture steps for the sale-replay fixture (README.md).

``with_act_dispositions`` adds a sealed ``accept`` disposition to every act
of a sale the engine has already verified (``read_sale_bundle``), as
``tests/test_live_history.py`` adds one to every act of a live check's
history: capsulectl seals no disposition on an offer or commit act yet. The
capsules it changes no longer verify, and the replay does not verify them
again. Once capsulectl seals one, rebuild this fixture and delete the step:
it refuses a sale whose acts already seal a disposition.

``withhold_thread`` makes the copy an adjudicator receives when one buyer's
thread is withheld: that thread's ``sale_threads`` entry keeps only its
registration and ``member: missing``, and ``x-deal-sale/v0`` no longer
carries it. capsule-cli 9691acadf137 writes only the user's own copy.
"""
from __future__ import annotations

import copy
import dataclasses

from capsule_engine.report.sale_bundle import SALE_EXTENSION, SaleBundle

ACT_RECORD = "action-record/v0"


def with_act_dispositions(sale: SaleBundle) -> SaleBundle:
    """``sale`` with ``{"decision": "accept"}`` as the disposition of every
    carried act record."""
    threads = []
    for thread in sale.threads:
        records = []
        for record in thread.records:
            shown = thread.disclosed.get(record["capsule_id"]) or {}
            if shown.get("type") == ACT_RECORD:
                if "disposition" in record:
                    raise SystemExit("an act already seals a disposition: rebuild the fixture and delete this step")
                record = {**record, "disposition": {"decision": "accept"}}
            records.append(record)
        threads.append(dataclasses.replace(thread, records=tuple(records)))
    return dataclasses.replace(sale, threads=tuple(threads))


def withhold_thread(bundle: dict, thread_id: str) -> dict:
    """A copy of the sale bundle ``bundle`` with the present thread
    ``thread_id`` withheld."""
    bundle = copy.deepcopy(bundle)
    entries = bundle["extensions"]["x-deal-v0"]["sale_threads"]
    (index,) = [i for i, e in enumerate(entries) if e.get("thread_id") == thread_id and e["member"] == "present"]
    entries[index] = {"registration": entries[index]["registration"], "member": "missing"}
    del bundle["extensions"][SALE_EXTENSION]["threads"][thread_id]
    return bundle
