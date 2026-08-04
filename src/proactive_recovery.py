"""Restart recovery for proactively delivered result files.

Messaging bridges claim ``proactive-*.txt`` by renaming it to ``.sending``.
This module restores claims stranded by a crash so every adapter applies the
same collision, race, and failure policy at startup.
"""

from __future__ import annotations

from pathlib import Path


def release_claim(claim: Path, reason: str) -> bool:
    """Return an in-flight ``.sending`` claim to the polling stream.

    A bridge claims a proactive file by renaming it to ``.sending`` BEFORE it
    tries to deliver. Every poller scans ``.txt`` only, so a claim that is kept
    but not released is invisible until the next restart's orphan sweep — the
    file is parked, not retried. Deleting it instead destroys the message.

    Releasing is therefore the correct move for EVERY non-delivery, including
    "this bridge has no owner configured": proactive files are not addressed to
    a particular bridge, so one that cannot deliver must put the file back for
    one that can, rather than consuming it.

    Same collision policy as the startup sweep: if the ``.txt`` name is already
    taken, leave the claim alone and say so — the startup sweep is the single
    place allowed to resolve that, and silently clobbering would lose whichever
    copy is newer.

    Returns True when the claim was released.
    """
    target = claim.with_suffix(".txt")
    try:
        if target.exists():
            print(
                f"  [proactive] NOT releasing {claim.name} ({reason}): "
                f"{target.name} already exists — left as .sending for the "
                f"startup sweep",
                flush=True,
            )
            return False
        claim.rename(target)
        print(
            f"  [proactive] released {claim.name} → {target.name} ({reason}); "
            f"another poll can retry it",
            flush=True,
        )
        return True
    except FileNotFoundError:
        return False
    except Exception as exc:  # pragma: no cover - filesystem-level failure
        print(f"  [proactive] failed to release {claim.name} ({reason}): {exc}", flush=True)
        return False


def recover_orphan_sending_files(results_dir: Path) -> int:
    """Restore orphan ``proactive-*.sending`` claims to the polling stream."""
    if not results_dir.exists():
        return 0

    recovered = 0
    for orphan in results_dir.iterdir():
        if not (orphan.name.startswith("proactive-") and orphan.suffix == ".sending"):
            continue

        target = orphan.with_suffix(".txt")
        try:
            if target.exists():
                print(
                    f"  [startup] skipping orphan recovery: {target.name} "
                    f"already exists (collision with {orphan.name})",
                    flush=True,
                )
                continue
            orphan.rename(target)
            recovered += 1
            print(f"  [startup] recovered orphan {orphan.name} → {target.name}", flush=True)
        except FileNotFoundError:
            # Another process recovered the same claim first.
            pass
        except Exception as exc:
            print(f"  [startup] failed to recover {orphan.name}: {exc}", flush=True)

    if recovered:
        print(f"  [startup] recovered {recovered} orphan .sending file(s)", flush=True)
    return recovered
