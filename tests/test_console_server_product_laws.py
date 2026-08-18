# SPDX-License-Identifier: Apache-2.0
"""Mechanical product-law checks for the local console's own served UI
(console.html/css/js), following the same grep/parse-style pattern as
tests/test_console_product_laws.py (PR #7's component-library gallery
checks) -- applied here to the screen that actually gets served by
`capsule console`, not the review gallery.
"""
from __future__ import annotations

import re
from pathlib import Path

CONSOLE = Path(__file__).parent.parent / "capsule_ledger" / "console"
CONSOLE_HTML = (CONSOLE / "console.html").read_text(encoding="utf-8")
CONSOLE_CSS = (CONSOLE / "console.css").read_text(encoding="utf-8")
CONSOLE_JS = (CONSOLE / "console.js").read_text(encoding="utf-8")

HEX_COLOR_RE = re.compile(r"#[0-9A-Fa-f]{6}\b")


def test_console_html_makes_no_outbound_network_requests():
    """Matches gallery.html's own discipline (see its test of the same
    name) -- this is the screen actually served by `capsule console`."""
    for match in re.finditer(r'(?:href|src)\s*=\s*"([^"]+)"', CONSOLE_HTML):
        url = match.group(1)
        assert not url.startswith(("http://", "https://", "//")), f"external resource: {url}"


def test_console_js_never_fetches_an_external_url():
    for match in re.finditer(r"fetch\(([^)]*)\)", CONSOLE_JS):
        arg = match.group(1)
        assert "http://" not in arg and "https://" not in arg, f"external fetch: {arg}"


def test_console_css_invents_no_new_color_literal():
    """Task requirement: reuse the real component library's tokens, never
    invent new visual tokens. `console.css` may only reference colors via
    `var(--asg-*)` (declared once, in tokens.css) or `rgba(R,G,B,alpha)`
    tints of an existing token's own RGB triple (the same convention
    components.css itself uses throughout for translucent fills/borders) --
    never a bare hex literal."""
    offenders = HEX_COLOR_RE.findall(CONSOLE_CSS)
    assert not offenders, f"console.css invents new hex color(s) not from tokens.css: {offenders}"


def test_console_css_rgba_tints_only_use_known_token_rgb_triples():
    tokens_css = (CONSOLE / "tokens.css").read_text(encoding="utf-8")
    known_hexes = {h.lower() for h in HEX_COLOR_RE.findall(tokens_css)}
    known_rgb = set()
    for h in known_hexes:
        h = h.lstrip("#")
        known_rgb.add((int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)))

    for match in re.finditer(r"rgba\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,", CONSOLE_CSS):
        triple = tuple(int(x) for x in match.groups())
        assert triple in known_rgb, f"console.css tints an RGB triple not declared in tokens.css: {triple}"


def test_console_js_blocked_verdict_glyph_is_slashed_circle_not_checkmark():
    match = re.search(r'verdictClass === "blocked".*?glyph:\s*"([^"]+)"', CONSOLE_JS, re.S)
    assert match, "blocked verdict-class branch not found in console.js"
    assert match.group(1) == "⊘"


def test_console_js_confirmed_verdict_glyph_is_checkmark():
    match = re.search(r'css:\s*"confirmed",\s*glyph:\s*"([^"]+)"', CONSOLE_JS)
    assert match, "confirmed verdict-class branch not found in console.js"
    assert match.group(1) == "✓"


def test_console_js_never_hardcodes_brick_color():
    """Brick (`--asg-brick` / `#A93F2B`) is reserved for real verification
    failure (`verify-stage--fail`) -- console.js never sets it directly;
    it only ever adds/removes CSS classes that components.css itself
    already gates to the failure-only selectors (see
    tests/test_console_product_laws.py's brick tests for the CSS side of
    this rule)."""
    assert "--asg-brick" not in CONSOLE_JS
    assert "A93F2B" not in CONSOLE_JS.upper()


def test_console_js_blocked_check_result_never_uses_verify_stage_fail():
    """A failed policy *check* (constraint result == "fail") is a refusal,
    not a verification failure -- product law: never brick. Only the real
    cryptographic verify() section may use `verify-stage--fail`."""
    check_block_match = re.search(
        r"checksHTML \+= '<div class=\"inspector-check-list\">'.*?checksHTML \+= \"</div>\";",
        CONSOLE_JS,
        re.S,
    )
    assert check_block_match, "checks-rendering block not found in console.js"
    assert "verify-stage--fail" not in check_block_match.group(0)
    assert "blocked" in check_block_match.group(0)  # fail maps to the sage "blocked" chip style


def test_console_js_cli_echo_rendering_uses_canonical_prefix_source():
    """The console never invents its own CLI-echo text -- every echo string
    rendered in the UI comes verbatim from the server's own
    `cli.format.build_echo` output (see api.py), which always begins
    "≡ capsule "; this is asserted directly against that module in
    tests/test_cli_console.py. Here we only assert console.js renders
    whatever the server sent, rather than constructing its own echo
    string client-side."""
    assert "≡ capsule" not in CONSOLE_JS


def test_console_html_cli_echo_bar_present_and_reused_verbatim():
    assert 'class="cli-echo"' in CONSOLE_HTML
    assert re.search(r'id="cli-echo-bar"[^>]*>≡ capsule ', CONSOLE_HTML)


def test_console_html_reuses_the_real_component_library_not_a_copy():
    assert '<link rel="stylesheet" href="tokens.css">' in CONSOLE_HTML
    assert '<link rel="stylesheet" href="components.css">' in CONSOLE_HTML


def test_console_server_serves_the_same_tokens_and_components_files():
    from capsule_ledger.console.server import _STATIC_FILES

    assert _STATIC_FILES["/tokens.css"][0] == "tokens.css"
    assert _STATIC_FILES["/components.css"][0] == "components.css"
