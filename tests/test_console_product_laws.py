# SPDX-License-Identifier: Apache-2.0
"""Mechanical checks for the console component library's product laws
(design_handoff_capsule_ledger_ux/README.md, "Product laws" §1-10 and the
Visual Language Spec's "Rule 2/3/5" callouts).

These are grep/parse-style tests over the static CSS/HTML, not visual
regression tests -- they catch the specific, stated failure modes: a
brick-colored blocked chip, a check mark leaking into a model-class mark,
two evaluation classes sharing one visual treatment.
"""
from __future__ import annotations

import re
from pathlib import Path

CONSOLE = Path(__file__).parent.parent / "capsule_ledger" / "console"
TOKENS_CSS = (CONSOLE / "tokens.css").read_text(encoding="utf-8")
COMPONENTS_CSS = (CONSOLE / "components.css").read_text(encoding="utf-8")
GALLERY_HTML = (CONSOLE / "gallery.html").read_text(encoding="utf-8")

BRICK_HEX = "#A93F2B"

# Rule 5: brick is reserved for verification failure. These are the only
# selectors in components.css allowed to reference brick -- the four
# failure-stage card pieces, the verify-ritual fail stage, and the one
# deliberate exception carried verbatim from the Visual Language Spec
# itself (staleness past its policy bound -- see components.css §4).
BRICK_ALLOWED_SELECTOR_SUBSTRINGS = (
    ".fail-card",
    ".fail-icon",
    ".fail-stage",
    ".verify-stage--fail",
    ".staleness-chip--stale-beyond-bound",
)


def _css_rules(css: str) -> list[tuple[str, str]]:
    """Parse ``selector { body }`` blocks, skipping ``@keyframes`` (whose
    "selectors" are percentages, not class names) and comments."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    rules = []
    for match in re.finditer(r"([^{}]+)\{([^{}]*)\}", css):
        selector, body = match.group(1).strip(), match.group(2)
        if selector.startswith("@") or re.match(r"^\d", selector) or selector in ("from", "to"):
            continue
        rules.append((selector, body))
    return rules


def test_brick_color_only_in_verification_failure_rules():
    offenders = []
    for selector, body in _css_rules(COMPONENTS_CSS):
        if "--asg-brick" in body or BRICK_HEX in body:
            if not any(sub in selector for sub in BRICK_ALLOWED_SELECTOR_SUBSTRINGS):
                offenders.append(selector)
    assert not offenders, f"brick color leaked into non-failure selectors: {offenders}"


def test_brick_color_defined_exactly_once_in_tokens():
    assert TOKENS_CSS.count(BRICK_HEX) == 1


def test_blocked_verdict_chip_never_uses_brick_or_red():
    for selector, body in _css_rules(COMPONENTS_CSS):
        if "verdict-chip--blocked" in selector or "verdict-glyph" in selector and "blocked" in selector:
            assert "--asg-brick" not in body
            assert BRICK_HEX not in body
            assert "red" not in body.lower()


def test_refusal_related_selectors_never_reference_brick():
    """Product law §5: refusals/blocks are NOT failures. Anything named for
    a refusal/block/held state must never touch brick."""
    for selector, body in _css_rules(COMPONENTS_CSS):
        if re.search(r"blocked|refus|--held", selector):
            assert "--asg-brick" not in body, f"{selector} references brick"
            assert BRICK_HEX not in body, f"{selector} references brick"


def test_gallery_blocked_chip_glyph_is_slashed_circle_not_checkmark():
    match = re.search(
        r'verdict-chip--blocked">\s*<span class="verdict-glyph">([^<]+)</span>', GALLERY_HTML
    )
    assert match, "blocked verdict chip not found in gallery"
    assert match.group(1) == "⊘"


def _eval_mark_glyph(html: str, variant: str) -> str:
    match = re.search(rf'eval-mark eval-mark--{variant}">([^<]*)</span>', html)
    assert match, f"eval-mark--{variant} not found in gallery"
    return match.group(1)


def test_deterministic_mark_glyph_is_checkmark():
    assert _eval_mark_glyph(GALLERY_HTML, "deterministic") == "✓"


def test_model_mark_glyph_is_never_a_checkmark():
    """The stated example check: no check-mark glyph inside a model-class
    mark. The tilde-in-a-dashed-square is the model class's own mark; a
    cited number inside the model's sentence legitimately embeds a
    deterministic chip (with its own check) elsewhere in the card -- this
    test targets only the model mark itself, not the whole card."""
    glyph = _eval_mark_glyph(GALLERY_HTML, "model")
    assert glyph == "≈"
    assert "✓" not in glyph


def test_manual_mark_glyph_is_initials_not_a_check_or_tilde():
    glyph = _eval_mark_glyph(GALLERY_HTML, "manual")
    assert glyph not in ("✓", "≈")
    assert glyph.isalpha()


def test_three_evaluation_marks_are_visually_distinct_by_css_not_just_label():
    """Never confusable (Rule 2) means the CSS itself -- not just the text
    label -- must differ: shape (border-radius) and fill/border treatment
    must not repeat across the three classes."""
    rules = dict(_css_rules(COMPONENTS_CSS))

    def prop(selector: str, name: str) -> str | None:
        body = rules.get(selector, "")
        match = re.search(rf"{name}\s*:\s*([^;]+);", body)
        return match.group(1).strip() if match else None

    deterministic = (
        prop(".eval-mark--deterministic", "background"),
        prop(".eval-mark--deterministic", "border-radius"),
    )
    model = (
        prop(".eval-mark--model", "border"),
        prop(".eval-mark--model", "border-radius"),
    )
    manual = (
        prop(".eval-mark--manual", "background"),
        prop(".eval-mark--manual", "border-radius"),
    )

    # deterministic is a solid sage fill with no border; model is borrowed
    # by nothing else and is dashed amber; manual is a circle (50%), the
    # only one of the three that is round.
    assert deterministic[0] == "var(--asg-sage)"
    assert prop(".eval-mark--deterministic", "border") is None
    assert model[0] == "2px dashed var(--asg-amber)"
    assert prop(".eval-mark--model", "background") is None
    assert manual[1] == "50%"
    assert deterministic[1] != manual[1]  # square chip vs. circle seal
    assert "var(--asg-sage)" not in (prop(".eval-mark--model", "background") or "")
    assert "var(--asg-amber)" not in (prop(".eval-mark--deterministic", "background") or "")


def _envelope_line_section() -> str:
    match = re.search(
        r'<section class="gallery-section" id="envelope-line">(.*?)</section>', GALLERY_HTML, re.S
    )
    assert match, "envelope-line section not found in gallery"
    return match.group(1)


def test_envelope_line_matches_pinned_literal_format():
    """Format pinned in capsule_ledger/cli/format.py:format_envelope_line --
    literal text, not a font choice. Every envelope-line div in the
    dedicated envelope-line gallery section must match exactly (this is
    stricter than "at least one line somewhere matches", so a corrupted
    example can't hide behind other correct ones)."""
    section = _envelope_line_section()
    lines = re.findall(r'<div class="envelope-line[^"]*">([^<]+)</div>', section)
    assert lines, "no envelope-line divs found in the envelope-line section"
    pattern = re.compile(r"^fold [^\s·]+ · records \d+–\d+ · checkpoint #\d+ · as of .+$")
    for line in lines:
        assert pattern.match(line), f"envelope line does not match pinned format: {line!r}"


def test_gallery_renders_every_required_component_and_state():
    required_markers = [
        'id="evaluation-classes"',
        'eval-mark--deterministic',
        'eval-mark--model',
        'eval-mark--manual',
        'id="envelope-line"',
        'id="rung-chip"',
        'rung-chip--held',
        'id="staleness-chip"',
        'staleness-chip--fresh',
        'staleness-chip--refreshing',
        'staleness-chip--stale-beyond-bound',
        'id="verdict-chips"',
        'verdict-chip--confirmed',
        'verdict-chip--dispatched',
        'verdict-chip--blocked',
        'verdict-chip--needs-decision',
        'verdict-chip--errored',
        'id="cli-echo"',
        'cli-echo',
        'id="brick-failure"',
        'digest_mismatch',
        'signature_invalid',
        'chain_gap',
        'inclusion_unproven',
        'verify-stage--pass',
        'verify-stage--fail',
        'verify-stage--skip',
    ]
    missing = [m for m in required_markers if m not in GALLERY_HTML]
    assert not missing, f"gallery.html is missing required component states: {missing}"


def test_gallery_makes_no_outbound_network_requests():
    """Matches the repo's existing verify-surface discipline
    (capsule_ledger/report/render.py): no external network calls in a page
    meant to be served locally."""
    for match in re.finditer(r'(?:href|src)\s*=\s*"([^"]+)"', GALLERY_HTML):
        url = match.group(1)
        assert not url.startswith(("http://", "https://", "//")), f"external resource: {url}"


def test_cli_echo_bar_uses_canonical_prefix():
    assert re.search(r'cli-echo">≡ capsule ', GALLERY_HTML)
