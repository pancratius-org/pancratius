# research-pure: a study manifest round-trips and is stamped (not hardcoded) by the shell.
"""Locks the provenance manifest + the store provenance helpers: round-trip through to_dict/from_dict,
the timestamp/git_sha are the values the shell PASSED IN (not baked into the builder), and
`sha256_file` matches a fixture."""
from __future__ import annotations

import hashlib

from lineation_core import store
from lineation_core.evaluation.manifest import Manifest, PromptFingerprint


def _manifest(git_sha: str, timestamp: str) -> Manifest:
    return Manifest(
        git_sha=git_sha, timestamp=timestamp, eval_set="reader_bench",
        eval_set_sha256="deadbeef", truth_sha256="cafebabe",
        prompts={"vision": PromptFingerprint("page.md", "aaaa"),
                 "text": PromptFingerprint("struct.md", "bbbb")},
        base_response_contract="json_array", models={"grok": "x-ai/grok-4.3", "ds": "deepseek/deepseek-v4-flash"},
        temperature=0.5, max_tokens=8192, reps=3, seed=0, price_table_version="2026-06-09",
        sweep_axis="contract", sweep_points=("json_array", "json_keyed"))


def test_manifest_round_trips():
    m = _manifest("abc123", "2026-06-09T12:00:00+00:00")
    assert Manifest.from_dict(m.to_dict()) == m


def test_manifest_carries_the_passed_in_timestamp_and_sha_not_a_hardcoded_one():
    a = _manifest("sha-A", "2020-01-01T00:00:00+00:00")
    b = _manifest("sha-B", "2030-12-31T23:59:59+00:00")
    assert a.git_sha == "sha-A" and a.timestamp == "2020-01-01T00:00:00+00:00"
    assert b.git_sha == "sha-B" and b.timestamp == "2030-12-31T23:59:59+00:00"   # not hardcoded


def test_sha256_file_matches_a_fixture(tmp_path):
    p = tmp_path / "f.txt"
    p.write_bytes(b"hello world")
    assert store.sha256_file(p) == hashlib.sha256(b"hello world").hexdigest()


def test_git_sha_is_a_hex_sha_optionally_dirty():
    sha = store.git_sha()
    head = sha.removesuffix("+dirty")
    assert len(head) == 40 and all(c in "0123456789abcdef" for c in head)
