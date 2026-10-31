// SPDX-License-Identifier: Apache-2.0

//! capsule-engine in Rust, for a Mesh node:
//!
//! - `guard`: the asg/inference pack's checks a node runs before it buys one
//!   inference from another node, decided as the Go port (go/guard) and the
//!   Python reference (python/capsule_engine/guards) decide them, held to
//!   conformance-vectors/guard/v0/inference.json;
//! - `admission`: the admission evaluator (go/admission), which decides GO,
//!   ASK, LIMIT or NO about a counterparty under an admission pack, held to
//!   conformance-vectors/admission/v0.
//!
//! Both decide; their caller seals. Nothing in this crate writes a
//! report, a bundle or any other export, so no local-only field (AGENTS.md)
//! leaves the machine through it.

pub mod admission;
pub mod canon;
pub mod guard;
mod instant;
mod yaml;
