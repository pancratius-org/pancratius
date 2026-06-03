# research-pure: the reproducibility contract for a gold run.
"""Every accepted gold line must be traceable to the exact inputs that produced it. A manifest
records them so a run is reproducible or rejected: source-DOCX digests, the frozen brief hash,
per-reader model ids, the gate thresholds, the RNG seed, the sampled region list, the raw-reply
hash, and whether the worktree was dirty at run time.

DOCX digest is a whole-file sha256 today; ARCHITECTURE §2.2 wants a sorted per-part/per-media
digest — note left where the upgrade lands.
"""
from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .types import Gates

SCHEMA = "lineation-gold/1"


def sha256_file(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _git(repo: Path, *args: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), *args],
            capture_output=True, text=True, timeout=10, check=True,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return None


def git_sha(repo: Path) -> str | None:
    return _git(repo, "rev-parse", "HEAD")


def git_dirty(repo: Path) -> bool | None:
    """True if the worktree has uncommitted changes (a dirty tree makes git_sha alone insufficient
    to reproduce the run). None if git is unavailable."""
    status = _git(repo, "status", "--porcelain")
    return bool(status) if status is not None else None


@dataclass(frozen=True, slots=True)
class Manifest:
    """The inputs that fully determine a gold run's accepted labels."""
    run_id: str
    schema: str = SCHEMA
    scorer_version: str = SCHEMA
    git_sha: str | None = None
    git_dirty: bool | None = None
    brief_sha256: str | None = None        # the frozen v5 reader brief
    raw_replies_sha256: str | None = None  # the panel's raw reply log (every model output)
    models: dict[str, str] = field(default_factory=dict)        # reader tag → OpenRouter model id
    docx_digests: dict[str, str] = field(default_factory=dict)  # book → sha256 (whole file; §2.2)
    gates: dict[str, object] = field(default_factory=dict)
    seed: int = 0
    sample_rids: tuple[str, ...] = ()

    def write(self, path: str | Path) -> None:
        Path(path).write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2, sort_keys=True))

    @classmethod
    def read(cls, path: str | Path) -> Manifest:
        d = json.loads(Path(path).read_text())
        d["models"] = dict(d.get("models", {}))
        d["docx_digests"] = dict(d.get("docx_digests", {}))
        d["gates"] = dict(d.get("gates", {}))
        d["sample_rids"] = tuple(d.get("sample_rids", ()))
        return cls(**d)


def build_manifest(
    *,
    run_id: str,
    repo: Path,
    brief: Path,
    models: Mapping[str, str],
    docx_paths: Mapping[str, Path],
    gates: Gates,
    seed: int,
    sample_rids: Sequence[str],
    raw_replies: Path | None = None,
) -> Manifest:
    return Manifest(
        run_id=run_id,
        git_sha=git_sha(repo),
        git_dirty=git_dirty(repo),
        brief_sha256=sha256_file(brief) if brief.exists() else None,
        raw_replies_sha256=sha256_file(raw_replies) if raw_replies and raw_replies.exists() else None,
        models=dict(models),
        docx_digests={book: sha256_file(p) for book, p in docx_paths.items() if p.exists()},
        gates=asdict(gates),
        seed=seed,
        sample_rids=tuple(sample_rids),
    )
