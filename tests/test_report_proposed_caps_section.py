# SPDX-License-Identifier: Apache-2.0
"""``build_dry_run_report_with_proposal``: the additive "would ALSO have
been held under a proposed cap" section (P2's dry-run report extension).
Purely additive over ``build_dry_run_report`` -- the base sections must be
identical either way; only the new ``caps_proposed`` section differs."""
from __future__ import annotations

from pathlib import Path

from capsule_ledger.folds.loader import load_definition_file
from capsule_ledger.report.build import build_dry_run_report, build_dry_run_report_with_proposal

PACK_DIR = Path(__file__).parent.parent / "capsule_ledger" / "packs" / "catalog" / "payments-safety"
FOLD_FILE = PACK_DIR / "folds" / "spend_weekly.yaml"
FIXTURE = PACK_DIR / "fixtures" / "mini_ledger.jsonl"


def _fold():
    return load_definition_file(FOLD_FILE)


def test_proposed_section_flags_the_action_a_tighter_cap_would_newly_hold():
    # Under a loose current cap, the pooled overlap-spend scenario allows.
    report = build_dry_run_report_with_proposal(
        [FIXTURE],
        caps_fold=_fold(),
        proposed_caps_minor={"money.transfer": 650_000},
        since=None,
        caps_minor={"money.transfer": 10_000_000},
    )
    proposed = next(s for s in report.guards if s.guard_id == "caps_proposed")
    # Two rows under this fixture: the pooled overlap-spend (treasury,
    # 650k+600k under the current loose cap) and the standalone
    # at-the-current-cap boundary payment (zeta, 1,000,000 alone) -- both
    # allow today, both exceed the tighter 650k proposal.
    by_agent = {row.agent: row for row in proposed.rows}
    assert set(by_agent) == {"checkout-shared-treasury@v1", "checkout-agent-zeta@v1"}
    assert by_agent["checkout-shared-treasury@v1"].amount_minor == 600_000
    assert by_agent["checkout-agent-zeta@v1"].amount_minor == 1_000_000
    for row in proposed.rows:
        assert row.capsule.get("capsule_id")  # a real, attached capsule, not just a label


def test_base_sections_are_unchanged_by_adding_a_proposal():
    base = build_dry_run_report(
        [FIXTURE], caps_fold=_fold(), since=None, caps_minor={"money.transfer": 10_000_000}
    )
    with_proposal = build_dry_run_report_with_proposal(
        [FIXTURE],
        caps_fold=_fold(),
        proposed_caps_minor={"money.transfer": 650_000},
        since=None,
        caps_minor={"money.transfer": 10_000_000},
    )
    assert with_proposal.guards[: len(base.guards)] == base.guards
    assert len(with_proposal.guards) == len(base.guards) + 1


def test_proposed_cap_at_or_above_everything_observed_holds_nothing_new():
    report = build_dry_run_report_with_proposal(
        [FIXTURE],
        caps_fold=_fold(),
        proposed_caps_minor={"money.transfer": 10_000_000},
        since=None,
        caps_minor={"money.transfer": 10_000_000},
    )
    proposed = next(s for s in report.guards if s.guard_id == "caps_proposed")
    assert proposed.rows == ()
