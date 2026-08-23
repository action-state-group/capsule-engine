# SPDX-License-Identifier: Apache-2.0
"""The 6-metric funnel report: a reporting tool over already-collected
telemetry events, not the collection itself. Renders exactly the six
metrics this package's instrumentation emits, as raw counts/rates -- and
nothing else. No pass/worry/fail banding, no thresholds, no verdict:
turning a rate into a judgment is a decision made elsewhere, later,
against a table this repository does not carry. See ``events.py``'s module
docstring for the same point made about the events themselves.

Two arms are always both present in the output (even a synthetic run with
data for only one arm shows the other at 0/0) because the report's own
shape -- what gets rendered -- must not depend on which arm happens to have
data; only the numbers may vary.
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass

from ..packaging import FULL, GUARDS_ONLY
from .events import ALLOWED_FIELDS

ARMS = (GUARDS_ONLY, FULL)

__all__ = ["ArmMetric", "FunnelReport", "compute_funnel", "render_funnel_report"]


@dataclass(frozen=True)
class ArmMetric:
    """A raw count out of a raw count. ``rate`` is arithmetic only --
    ``None`` (not 0) when the denominator is 0, so a report never implies
    "0%" for a metric that simply has no data yet."""

    numerator: int
    denominator: int

    @property
    def rate(self) -> float | None:
        return (self.numerator / self.denominator) if self.denominator else None


@dataclass(frozen=True)
class FunnelReport:
    m1_activation: dict[str, ArmMetric]
    m2_enforcement_on: dict[str, ArmMetric]
    m3_day14_alive: dict[str, ArmMetric]
    m4_evidence_tax: float | None
    m5_evidence_pull: ArmMetric
    m6_viral_unit: int


def _validate(raw: dict) -> None:
    """Same schema check ``sink.emit`` applies on the way out -- re-applied
    on the way in, so a funnel run over an untrusted/hand-edited events file
    still cannot smuggle ledger-shaped content into the report."""
    if set(raw) - {"report_token"} - ALLOWED_FIELDS:
        raise ValueError(f"telemetry event carries unexpected fields: {set(raw) - ALLOWED_FIELDS - {'report_token'}}")


def compute_funnel(raw_events: Iterable[dict]) -> FunnelReport:
    installs_seen: dict[str, set[str]] = defaultdict(set)  # arm -> install_ids
    activated: dict[str, set[str]] = defaultdict(set)
    enforced: dict[str, set[str]] = defaultdict(set)
    alive: dict[str, set[str]] = defaultdict(set)
    evidence_pulled: set[str] = set()
    report_opens: dict[str, int] = defaultdict(int)

    for raw in raw_events:
        _validate(raw)
        metric = raw.get("metric")
        arm = raw.get("arm")
        install_id = raw.get("install_id")
        value = raw.get("value")

        if metric == "m6_report_opened":
            token = raw.get("report_token")
            if token:
                report_opens[token] += 1
            continue

        if arm not in ARMS or not install_id:
            continue
        if metric == "install_seen":
            installs_seen[arm].add(install_id)
        elif metric == "m1_activation" and value:
            activated[arm].add(install_id)
        elif metric == "m2_enforcement_on" and value:
            enforced[arm].add(install_id)
        elif metric == "m3_day14_alive" and value:
            alive[arm].add(install_id)
        elif metric == "m5_evidence_pull" and value:
            evidence_pulled.add(install_id)

    m1 = {arm: ArmMetric(len(activated[arm]), len(installs_seen[arm])) for arm in ARMS}
    m2 = {arm: ArmMetric(len(enforced[arm]), len(activated[arm])) for arm in ARMS}
    m3 = {arm: ArmMetric(len(alive[arm]), len(activated[arm])) for arm in ARMS}

    guards_only_rate = m1["guards-only"].rate
    full_rate = m1["full"].rate
    m4 = (full_rate / guards_only_rate) if guards_only_rate else None

    m5 = ArmMetric(len(evidence_pulled & activated["full"]), len(activated["full"]))

    m6 = sum(1 for count in report_opens.values() if count >= 2)

    return FunnelReport(
        m1_activation=m1,
        m2_enforcement_on=m2,
        m3_day14_alive=m3,
        m4_evidence_tax=m4,
        m5_evidence_pull=m5,
        m6_viral_unit=m6,
    )


def _fmt_rate(metric: ArmMetric) -> str:
    if metric.rate is None:
        return f"n/a ({metric.numerator}/{metric.denominator})"
    return f"{metric.rate:.1%} ({metric.numerator}/{metric.denominator})"


def render_funnel_report(report: FunnelReport) -> str:
    """Plain text: the six metrics, and nothing else."""
    lines = ["capsule-ledger two-arm funnel report", "=" * 33, ""]

    def arm_line(label: str, metrics: dict[str, ArmMetric]) -> None:
        lines.append(label)
        for arm in ARMS:
            lines.append(f"  {arm:<11} {_fmt_rate(metrics[arm])}")

    arm_line("M1 activation (>=1 guard configured near install)", report.m1_activation)
    lines.append("")
    arm_line("M2 enforcement-on (dry_run -> enforce, of activated)", report.m2_enforcement_on)
    lines.append("")
    arm_line("M3 day-14-alive (guard evaluated recently, of activated)", report.m3_day14_alive)
    lines.append("")
    lines.append("M4 evidence tax (full M1 rate / guards-only M1 rate)")
    lines.append(f"  {report.m4_evidence_tax:.2f}" if report.m4_evidence_tax is not None else "  n/a")
    lines.append("")
    lines.append("M5 evidence-pull (full arm only, of activated)")
    lines.append(f"  {_fmt_rate(report.m5_evidence_pull)}")
    lines.append("")
    lines.append("M6 viral unit (share link opened by a distinct second party)")
    lines.append(f"  {report.m6_viral_unit} distinct instance(s)")
    lines.append("")
    return "\n".join(lines)
