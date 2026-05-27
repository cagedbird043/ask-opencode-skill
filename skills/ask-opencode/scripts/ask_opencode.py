#!/usr/bin/env python3
"""Small Codex wrapper for delegating one task to OpenCode."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ask local OpenCode to do one bounded task.")
    p.add_argument("prompt", nargs="*", help="Prompt text. Use -- before it if needed.")
    p.add_argument("--mode", choices=["readonly", "edit", "custom"], default="readonly")
    p.add_argument("--dir", default=os.getcwd(), help="Working directory for opencode.")
    p.add_argument("--agent", help="Optional OpenCode primary agent name.")
    p.add_argument("--model", help="Optional provider/model override, e.g. deepseek/deepseek-v4-flash.")
    p.add_argument("--timeout", type=int, default=600, help="Seconds before killing opencode.")
    p.add_argument("--title", default="codex-ask-opencode", help="OpenCode session title.")
    p.add_argument("--file", action="append", default=[], help="Attach file to opencode; may repeat.")
    p.add_argument("--raw-json", action="store_true", help="Print raw OpenCode JSON events instead of extracted text.")
    p.add_argument("--pure", action="store_true", help="Run OpenCode without external plugins.")
    p.add_argument("--no-skip-permissions", action="store_true", help="Do not pass --dangerously-skip-permissions. Non-interactive runs may return no text if approval is needed.")
    p.add_argument("--keep-session", action="store_true", help="Keep the OpenCode session in OpenCode history. Default is to delete it after output extraction.")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    if not args.prompt:
        print("ask_opencode: missing prompt", file=sys.stderr)
        return 2

    opencode = shutil.which("opencode")
    if not opencode:
        print("ask_opencode: opencode not found in PATH", file=sys.stderr)
        return 127

    workdir = Path(args.dir).expanduser().resolve()
    if not workdir.exists() or not workdir.is_dir():
        print(f"ask_opencode: --dir is not a directory: {workdir}", file=sys.stderr)
        return 2

    prompt = " ".join(args.prompt).strip()
    # Do not force an OpenCode agent by default: user-defined primary agents can
    # be config-specific, and some subagent names cannot be used with `run`.
    # Callers may still pass --agent explicitly.
    agent = args.agent

    cmd = [opencode, "run", "--format", "json", "--dir", str(workdir), "--title", args.title]
    if args.pure:
        cmd.append("--pure")
    if agent:
        cmd += ["--agent", agent]
    if args.model:
        cmd += ["--model", args.model]
    for f in args.file:
        cmd += ["--file", f]
    # opencode run is non-interactive. Without auto-approval it can emit only a
    # step_start event and no text when a permission gate is encountered. This
    # flag still respects permissions that are explicitly denied by OpenCode config.
    if not args.no_skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    cmd.append(prompt)

    try:
        proc = subprocess.run(cmd, text=True, capture_output=True, timeout=args.timeout)
    except subprocess.TimeoutExpired as e:
        print(f"ask_opencode: timeout after {args.timeout}s", file=sys.stderr)
        if e.stdout:
            print(e.stdout, end="")
        if e.stderr:
            print(e.stderr, end="", file=sys.stderr)
        return 124

    texts: list[str] = []
    session_ids: list[str] = []
    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        sid = event.get("sessionID")
        if isinstance(sid, str) and sid.startswith("ses_") and sid not in session_ids:
            session_ids.append(sid)
        part = event.get("part") if isinstance(event, dict) else None
        if isinstance(part, dict):
            psid = part.get("sessionID")
            if isinstance(psid, str) and psid.startswith("ses_") and psid not in session_ids:
                session_ids.append(psid)
        if event.get("type") == "text" and isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)

    cleanup_rc = 0
    if not args.keep_session and session_ids:
        cleanup_roots = [
            Path.home() / ".local/share/opencode/storage",
            Path.home() / ".local/state/opencode",
            Path.home() / ".cache/opencode",
            Path("/tmp/opencode"),
        ]
        for sid in session_ids:
            deleted = subprocess.run([opencode, "session", "delete", sid], text=True, capture_output=True)
            if deleted.returncode != 0:
                cleanup_rc = deleted.returncode or 1
                msg = (deleted.stderr or deleted.stdout or "").strip()
                print(f"ask_opencode: warning: failed to delete opencode session {sid}: {msg}", file=sys.stderr)
            # `opencode session delete` removes the visible session record but
            # currently leaves session_diff/<sessionID>.json behind. Remove exact
            # session-id residues under OpenCode-owned state/cache roots.
            for root in cleanup_roots:
                if not root.exists():
                    continue
                for residue in root.rglob(f"*{sid}*"):
                    try:
                        if residue.is_dir():
                            shutil.rmtree(residue)
                        else:
                            residue.unlink()
                    except FileNotFoundError:
                        pass
                    except Exception as exc:
                        cleanup_rc = cleanup_rc or 1
                        print(f"ask_opencode: warning: failed to remove residue {residue}: {exc}", file=sys.stderr)

    if args.raw_json:
        if proc.stdout:
            print(proc.stdout, end="")
        if proc.stderr:
            print(proc.stderr, end="", file=sys.stderr)
        return cleanup_rc or proc.returncode

    output = "".join(texts).strip()
    if output:
        print(output)
        if cleanup_rc:
            return cleanup_rc
        return 0 if proc.returncode == 1 else proc.returncode

    if proc.stderr.strip():
        print(proc.stderr.strip(), file=sys.stderr)
    print("ask_opencode: no text output from opencode; rerun with --raw-json for debugging", file=sys.stderr)
    return cleanup_rc or proc.returncode or 1


if __name__ == "__main__":
    raise SystemExit(main())
