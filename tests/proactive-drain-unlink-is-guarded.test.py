#!/usr/bin/env python3
"""The proactive drain must not delete a message whose send FAILED.

Sibling of #2626, which fixed this in `discord-bridge.py`. The same shape was
present in `telegram-bridge.py` and `slack-bridge.py`: the `unlink` sat OUTSIDE
the try that guards the send, so a DM the API rejected was caught, logged, and
the file removed anyway — destroying a message on the owner's notification path.

STRUCTURAL, and deliberately so — stated plainly rather than letting green imply
more. Exercising the real drain means importing the bridge, which pulls
`telegram`/`slack_bolt` and resolves the operator's config dir at import time,
the exact hazard the isolation PRs (#2612 #2614 #2615 #2618 #2619 #2620) exist to
prevent. This asserts the property that made the bug possible — *is the unlink
reachable when the send raised?* — over the parsed source.

The predicate is proven able to FAIL: `test_predicate_detects_the_pre_fix_shape`
runs it against the original code and requires a violation. Without that, a
predicate that always returns "guarded" would pass every assertion here.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        FAILURES.append(f"{name}{(' — ' + detail) if detail else ''}")
        print(f"  FAIL {name} {detail}")


def _unlink_calls(node: ast.AST) -> list[int]:
    return [n.lineno for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
            and n.func.attr == "unlink"]


def _mentions_send(node: ast.AST) -> bool:
    for n in ast.walk(node):
        if isinstance(n, ast.Call):
            name = getattr(n.func, "attr", None) or getattr(n.func, "id", "") or ""
            if "send" in name.lower():
                return True
    return False


def unguarded_proactive_unlinks(src: str) -> list[int]:
    """Lines where an unlink can run even though the guarded send raised.

    For every `try` whose body performs a send, an unlink is SAFE only when it
    is inside that try's body — reached only if no statement before it raised.
    An unlink in the handler, or after the try/except at the enclosing level, is
    reachable on the failure path and is what this test forbids.
    """
    tree = ast.parse(src)
    bad: list[int] = []
    for parent in ast.walk(tree):
        body = getattr(parent, "body", None)
        if not isinstance(body, list):
            continue
        for i, stmt in enumerate(body):
            if not (isinstance(stmt, ast.Try) and _mentions_send(stmt)):
                continue
            # (a) unlink inside a handler -> runs precisely when the send failed
            for h in stmt.handlers:
                bad += _unlink_calls(h)
            # (b) unlink in the SAME block after the try -> runs regardless
            for later in body[i + 1:]:
                bad += _unlink_calls(later)
    return sorted(set(bad))


PRE_FIX = '''
for f in files:
    try:
        send_reply(owner, text)
    except Exception as e:
        print("failed", e)
    f.unlink(missing_ok=True)
'''

POST_FIX = '''
for f in files:
    try:
        send_reply(owner, text)
        f.unlink(missing_ok=True)
    except Exception as e:
        print("failed", e)
'''

OVER_FIXED = '''
for f in files:
    try:
        send_reply(owner, text)
    except Exception as e:
        print("failed", e)
'''


def main() -> int:
    print("proactive drain — unlink must be unreachable on the failure path:")

    # --- POSITIVE CONTROL: the predicate must detect the ORIGINAL bug --------
    # Asserted non-empty rather than at an exact line: the fixture's line
    # numbering is an artefact of how the literal is indented, not a property
    # worth pinning, and pinning it made this control fail for the wrong reason.
    hits = unguarded_proactive_unlinks(PRE_FIX)
    check("POSITIVE CONTROL — predicate flags the pre-fix shape", hits != [],
          f"got {hits}; without this every assertion below is vacuous")
    check("predicate accepts the fixed shape",
          unguarded_proactive_unlinks(POST_FIX) == [])

    # --- the real files ------------------------------------------------------
    # `discord-bridge.py` is deliberately NOT asserted here. Its `poll_proactive`
    # instance is being fixed in #2626 (not yet merged), so asserting it would
    # make this PR red for a defect another PR owns. Its `poll_approved` carries
    # a THIRD instance (main:3974) that #2626 does not touch — its diff is
    # `@@ -4997,9 +4997,49 @@` only. Reported to that PR's author rather than
    # fixed here, to keep one concern per PR and avoid racing their branch.
    for rel in ("src/telegram-bridge.py", "src/slack-bridge.py"):
        p = REPO / rel
        if not p.exists():
            check(f"{rel} exists", False, "file missing")
            continue
        bad = unguarded_proactive_unlinks(p.read_text())
        check(f"{rel}: no unlink reachable on a failed send", bad == [],
              f"unguarded unlink at line(s) {bad}")

    # --- do NOT over-fix into "never clean up" -------------------------------
    # Removing the unlink entirely also passes the check above, and would
    # re-send every proactive message forever. Both bridges must still delete
    # on a path that succeeded.
    check("over-fixed shape (no unlink at all) is not what we shipped",
          unguarded_proactive_unlinks(OVER_FIXED) == [])
    for rel in ("src/telegram-bridge.py", "src/slack-bridge.py"):
        src = (REPO / rel).read_text()
        tree = ast.parse(src)
        survives = False
        for t in ast.walk(tree):
            if isinstance(t, ast.Try) and _mentions_send(t):
                if any(_unlink_calls(b) for b in t.body):
                    survives = True
        check(f"{rel}: an unlink REMAINS on the success path", survives,
              "cleanup was removed entirely — messages would re-send forever")

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("proactive drain: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
