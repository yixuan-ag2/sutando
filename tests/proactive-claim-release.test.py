#!/usr/bin/env python3
"""`release_claim` — returning an in-flight `.sending` claim to the poll stream.

BEHAVIOURAL, not structural. The sibling test
(`proactive-drain-unlink-is-guarded.test.py`) can only assert the shape of the
bridge source, because importing a bridge pulls `telegram`/`slack_bolt` and the
operator's config dir. This policy lives in dependency-light `src/` precisely so
it can be exercised for real — filesystem in a tmpdir, nothing else.

Why the policy exists (@john-the-dev on #2627): a bridge claims a proactive file
by renaming it `.sending` BEFORE delivering. Every poller scans `.txt` only. So
"keep the file on failure" without releasing it parks the message until the next
restart's orphan sweep — the retry it promises never happens. And deleting it,
which is what both bridges did, destroys the message outright.
"""
from __future__ import annotations

import importlib.util
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("pr", REPO / "src" / "proactive_recovery.py")
pr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pr)

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        FAILURES.append(f"{name}{(' — ' + detail) if detail else ''}")
        print(f"  FAIL {name} {detail}")


def poller_would_see(d: Path) -> list[str]:
    """Exactly what both bridges scan for: proactive-*.txt."""
    return sorted(f.name for f in d.iterdir()
                  if f.name.startswith("proactive-") and f.suffix == ".txt")


def main() -> int:
    print("proactive claim release:")

    with tempfile.TemporaryDirectory() as td:
        d = Path(td)

        # --- the happy path: a claim comes BACK -----------------------------
        claim = d / "proactive-abc.sending"
        claim.write_text("the message body")
        check("PRECONDITION — a .sending claim is invisible to the poller",
              poller_would_see(d) == [], f"saw {poller_would_see(d)}")

        released = pr.release_claim(claim, "send raised: boom")
        check("release returns True", released is True)
        check("the body SURVIVES — not deleted",
              (d / "proactive-abc.txt").exists())
        # read via a guard, not `.read_text()` directly: against a no-op
        # implementation the file is absent and a bare read RAISES, which
        # reports one symptom and hides every later assertion. A test's job
        # when the code regresses is to name what broke, not to abort.
        def _text(p: Path) -> str:
            try:
                return p.read_text()
            except OSError:
                return "<ABSENT>"
        check("content is intact",
              _text(d / "proactive-abc.txt") == "the message body",
              f"got {_text(d / 'proactive-abc.txt')!r}")
        check("the claim name is gone", not claim.exists())
        check("THE POINT — the poller can now see it again",
              poller_would_see(d) == ["proactive-abc.txt"],
              f"saw {poller_would_see(d)}")

        # --- collision: never clobber --------------------------------------
        # A same-named .txt can exist if a producer recreated a deterministic
        # filename while this claim was in flight. Releasing over it would lose
        # whichever copy is newer; the startup sweep is the one place allowed to
        # resolve that, and it uses the identical rule.
        c2 = d / "proactive-dup.sending"
        c2.write_text("in flight")
        (d / "proactive-dup.txt").write_text("newer producer output")
        r2 = pr.release_claim(c2, "no owner in allowFrom")
        check("collision: returns False", r2 is False)
        check("collision: the existing .txt is NOT overwritten",
              _text(d / "proactive-dup.txt") == "newer producer output")
        check("collision: the claim is left in place for the startup sweep",
              c2.exists() and _text(c2) == "in flight")

        # --- a vanished claim must not raise --------------------------------
        r3 = pr.release_claim(d / "proactive-gone.sending", "send raised: x")
        check("missing claim: returns False without raising", r3 is False)

        # --- POSITIVE CONTROL ----------------------------------------------
        # Every assertion above still passes against a release_claim that always
        # returns False and does nothing, EXCEPT this one and the happy path.
        # Stated explicitly so a future no-op implementation cannot pass quietly.
        c4 = d / "proactive-ctl.sending"
        c4.write_text("x")
        pr.release_claim(c4, "control")
        check("POSITIVE CONTROL — release actually MOVES the file",
              (d / "proactive-ctl.txt").exists() and not c4.exists(),
              "a no-op implementation fails here")

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("proactive claim release: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
