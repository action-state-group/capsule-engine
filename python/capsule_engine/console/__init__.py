# SPDX-License-Identifier: Apache-2.0
"""The local console: `capsule console`'s data layer (`api.py`), HTTP wiring
(`server.py`), and the static UI it serves -- `tokens.css`/`components.css`
(the shared component library), plus `console.css`/`console.js`/`console.html`
(this screen's own layout and client logic). `gallery.html` next to these
files is the component-library review page, not part of the served UI.

LOCAL ONLY: this package serves localhost, reads a real ledger on disk, and
makes no outbound network calls anywhere in what it renders.
"""
