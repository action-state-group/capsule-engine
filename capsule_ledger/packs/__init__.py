# SPDX-License-Identifier: Apache-2.0
"""Starter packs: installable obligation -> deterministic-check -> capsule
recipes, layered on top of the existing guard/fold/manifest primitives.

See the module docstrings in ``schema.py`` (the parsed pack shape),
``loader.py`` (``pack.yaml`` -> ``PackDefinition``), ``install.py``
(``PackDefinition`` -> an installed, manifest-governed ``GuardEngine``), and
``pins.py`` (fail-closed registry-pin verification before install) for the
pieces. ``catalog/`` holds the packs this repo ships (``payments-safety``
today).
"""
from .enforce import accept_thresholds, enforce_pack
from .errors import PackDefinitionError, RegistryPinError
from .install import InstalledPack, build_engine, install_pack, record_pack_activation
from .loader import load_pack_dir
from .pins import load_pins_file, verify_pins
from .schema import ActionSemantic, Obligation, PackDefinition, PackFixtures, ProposerStub
from .thresholds import ThresholdProposal, load_proposals_file, propose_thresholds, write_proposals_file

__all__ = [
    "PackDefinition",
    "Obligation",
    "ActionSemantic",
    "ProposerStub",
    "PackFixtures",
    "load_pack_dir",
    "PackDefinitionError",
    "InstalledPack",
    "install_pack",
    "build_engine",
    "record_pack_activation",
    "load_pins_file",
    "verify_pins",
    "RegistryPinError",
    "ThresholdProposal",
    "propose_thresholds",
    "write_proposals_file",
    "load_proposals_file",
    "accept_thresholds",
    "enforce_pack",
]
