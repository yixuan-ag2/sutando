#!/usr/bin/env python3
"""A proactive DM Slack REFUSES must be released, not deleted — behaviourally.

I had claimed this path could only be asserted structurally, because importing
`slack-bridge.py` pulls `slack_bolt` and resolves the operator's config dir.
That premise was wrong, and @Sutando-Pro's #2628 test is the existence proof:
stub the SDK modules in `sys.modules` BEFORE the import and the bridge loads
fine. The harness below is their technique, applied to the failure paths.

Covers what the structural sibling cannot: that `release_claim` actually runs on
a swallowed refusal and on a raise, and that a SUCCESS still consumes the file.

HERMETIC, and it proves it: the operator's real `results/` is snapshotted before
and compared after. That guard is not decoration — on 2026-08-04 an unisolated
suite wrote fixture data into this host's live owner-presence signal.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import tempfile
import types
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

_tmp_home = tempfile.mkdtemp(prefix="slack-release-cfg-")
os.environ["CLAUDE_CONFIG_DIR"] = _tmp_home
# The bridge exits at import without these (`slack-bridge.py:144`). Fake values
# are enough — every Slack call is stubbed below and no socket is opened.
os.environ.setdefault("SLACK_BOT_TOKEN", "xoxb-test-not-real")
os.environ.setdefault("SLACK_APP_TOKEN", "xapp-test-not-real")
_slack_cfg = Path(_tmp_home) / "channels" / "slack"
_slack_cfg.mkdir(parents=True, exist_ok=True)
# Seed access.json BEFORE import: `channel_access_path()` falls back to the real
# home when the canonical file is absent, so the env override alone would still
# read the developer's live allowlist. (Pro's finding on #2628.)
(_slack_cfg / "access.json").write_text(json.dumps({"allowFrom": ["UOWNER"]}))

for name in ("slack_bolt", "slack_bolt.adapter", "slack_bolt.adapter.socket_mode",
             "slack_sdk", "slack_sdk.errors"):
    if name not in sys.modules:
        m = types.ModuleType(name)
        if name == "slack_bolt":
            m.App = type("App", (), {"__init__": lambda self, **kw: None,
                                     "event": lambda self, *a, **k: (lambda fn: fn),
                                     "client": types.SimpleNamespace()})
        if name == "slack_bolt.adapter.socket_mode":
            m.SocketModeHandler = type("SocketModeHandler", (),
                                       {"__init__": lambda self, *a, **kw: None})
        if name == "slack_sdk.errors":
            m.SlackApiError = type("SlackApiError", (Exception,), {})
        sys.modules[name] = m


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


sb = _load("slack_bridge_release", REPO / "src" / "slack-bridge.py")
_LIVE_RESULTS = Path(sb.RESULTS_DIR)
_live_before = sorted(p.name for p in _LIVE_RESULTS.iterdir()) if _LIVE_RESULTS.exists() else None

FAILURES: list[str] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        print(f"  ok   {name}")
    else:
        FAILURES.append(f"{name}{(' — ' + detail) if detail else ''}")
        print(f"  FAIL {name} {detail}")


class _Tick(Exception):
    """Breaks result_watcher's infinite loop after one pass."""


def _drive(send_behaviour) -> tuple[list[str], list]:
    """Run ONE result_watcher pass over a temp results dir. Returns (files, sent)."""
    d = Path(tempfile.mkdtemp(prefix="slack-release-res-"))
    (d / "proactive-note.txt").write_text("the body")
    sb.RESULTS_DIR = d
    sb.STATE_DIR = d / "state"
    sb.STATE_DIR.mkdir(exist_ok=True)

    sent: list = []

    def _stub_send(ch, _ts, text, **_kw):
        sent.append((ch, text))
        return send_behaviour()          # True / False / raises

    sb._send_reply = _stub_send
    sb.mark_proactive_delivered = lambda *a, **kw: None
    sb.proactive_was_delivered = lambda *a, **kw: False
    sb.presenter_mode_active = lambda *a, **kw: False
    sb.resolve_proactive_owner_id = lambda *a, **kw: "UOWNER"
    sb.app = types.SimpleNamespace(client=types.SimpleNamespace(
        conversations_open=lambda users=None: {"channel": {"id": "D1"}}))

    orig_sleep = sb.time.sleep
    sb.time.sleep = lambda _s: (_ for _ in ()).throw(_Tick())
    try:
        sb.result_watcher()
    except (_Tick, Exception):
        pass
    finally:
        sb.time.sleep = orig_sleep
    return sorted(p.name for p in d.iterdir() if p.is_file()), sent


def main() -> int:
    print("slack proactive drain — release on failure, consume on success:")

    # --- Slack REFUSES (swallowed, returns False) --------------------------
    files, sent = _drive(lambda: False)
    # Assert the PROACTIVE send happened — not that it was the only send.
    # `result_watcher` also emits stall notices for pending replies in the same
    # pass, so a `len(sent) == 1` assertion measures unrelated traffic.
    check("refused: the proactive send was attempted",
          any(ch == "D1" and t == "the body" for ch, t in sent),
          f"{len(sent)} sends, none matching the proactive body")
    check("refused: the body SURVIVES as .txt — not deleted",
          "proactive-note.txt" in files, f"left: {files}")
    check("refused: NOT left stranded as .sending",
          "proactive-note.sending" not in files, f"left: {files}")

    # --- Slack RAISES ------------------------------------------------------
    def _boom():
        raise RuntimeError("chat_postMessage exploded")
    files, _ = _drive(_boom)
    check("raised: the body SURVIVES as .txt", "proactive-note.txt" in files, f"left: {files}")
    check("raised: NOT left stranded as .sending",
          "proactive-note.sending" not in files, f"left: {files}")

    # --- POSITIVE CONTROL: success must still CONSUME ----------------------
    # Without this, an implementation that never deletes anything passes every
    # assertion above while re-sending each proactive message forever.
    files, sent = _drive(lambda: True)
    check("POSITIVE CONTROL — delivered: the file is consumed",
          "proactive-note.txt" not in files and "proactive-note.sending" not in files,
          f"left: {files}")

    # --- hermetic ----------------------------------------------------------
    after = sorted(p.name for p in _LIVE_RESULTS.iterdir()) if _LIVE_RESULTS.exists() else None
    check("HERMETIC — the operator's real results/ is untouched",
          after == _live_before,
          f"before={_live_before if _live_before is None else len(_live_before)} "
          f"after={after if after is None else len(after)}")

    print()
    if FAILURES:
        print(f"FAILED ({len(FAILURES)}):")
        for f in FAILURES:
            print(f"  - {f}")
        return 1
    print("slack proactive release: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
