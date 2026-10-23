# SPDX-License-Identifier: Apache-2.0
"""Opt-in-disclosed, aggregate-only instrumentation for the two packaging
arms, plus a reporting tool (``funnel.py``) over whatever telemetry has
been collected. See ``consent.py`` for the disclosure text and opt-in gate,
and each submodule's own docstring for the rest.
"""
from . import consent, events, funnel, record, sink, state

__all__ = ["consent", "events", "funnel", "record", "sink", "state"]
