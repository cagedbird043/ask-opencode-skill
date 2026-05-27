#!/usr/bin/env python3
"""Small Codex wrapper for delegating one task to OpenCode."""
from __future__ import annotations

import argparse
import json
import os
import signal
import shutil
import subprocess
import sys
import time
from pathlib import Path


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ask local OpenCode to do one bounded task.")
    p.add_argument("prompt", nargs="*", help="Prompt text. Use -- before it if needed.")
    p.add_argument("--mode", choices=["readonly", "edit", "custom"], default="readonly")
    p.add_argument("--dir", default=os.getcwd(), help="Working directory for opencode.")
    p.add_argument("--agent", help="Optional OpenCode primary agent name.")
    p.add_argument("--model", help="Optional provider/model override, e.g. deepseek/deepseek-v4-flash.")
    p.add_argument("--timeout", type=int, default=600, help="Seconds before killing opencode.")
    p.add_argument("--title", help="OpenCode session title. Defaults to a unique ephemeral title.")
    p.add_argument("--file", action="append", default=[], help="Attach file to opencode; may repeat.")
    p.add_argument("--raw-json", action="store_true", help="Print raw OpenCode JSON events instead of extracted text.")
    p.add_argument("--pure", action="store_true", help="Run OpenCode without external plugins.")
    p.add_argument("--no-skip-permissions", action="store_true", help="Do not pass --dangerously-skip-permissions. Non-interactive runs may return no text if approval is needed.")
    p.add_argument("--keep-session", action="store_true", help="Keep the OpenCode session in OpenCode history. Default is to delete it after output extraction.")
    return p.parse_args()


def parse_session_ids(stdout: str) -> tuple[list[str], list[str]]:
    texts: list[str] = []
    session_ids: list[str] = []
    for line in stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        sid = event.get("sessionID")
        if isinstance(sid, str) and sid.startswith("ses_") and sid not in session_ids:
            session_ids.append(sid)
        part = event.get("part")
        if isinstance(part, dict):
            psid = part.get("sessionID")
            if isinstance(psid, str) and psid.startswith("ses_") and psid not in session_ids:
                session_ids.append(psid)
        if event.get("type") == "text" and isinstance(part, dict):
            text = part.get("text")
            if isinstance(text, str):
                texts.append(text)
    return texts, session_ids


def stop_process_group(proc: subprocess.Popen[str], sig: int) -> None:
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        return
    except Exception:
        try:
            proc.send_signal(sig)
        except Exception:
            return


def terminate_process_group(proc: subprocess.Popen[str]) -> None:
    stop_process_group(proc, signal.SIGTERM)
    try:
        proc.wait(timeout=5)
        return
    except subprocess.TimeoutExpired:
        pass
    stop_process_group(proc, signal.SIGKILL)
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def discover_sessions_by_title(opencode: str, title: str, workdir: Path) -> list[str]:
    found: list[str] = []
    try:
        proc = subprocess.run(
            [opencode, "session", "list", "--format", "json", "--max-count", "100"],
            text=True,
            capture_output=True,
            timeout=20,
        )
    except Exception:
        return found
    if proc.returncode != 0:
        return found
    try:
        sessions = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return found
    if not isinstance(sessions, list):
        return found
    for item in sessions:
        if not isinstance(item, dict):
            continue
        sid = item.get("id")
        if (
            isinstance(sid, str)
            and sid.startswith("ses_")
            and item.get("title") == title
            and Path(str(item.get("directory", ""))).expanduser() == workdir
            and sid not in found
        ):
            found.append(sid)
    return found


def cleanup_sessions(opencode: str, session_ids: list[str], keep_session: bool) -> int:
    if keep_session or not session_ids:
        return 0
    cleanup_roots = [
        Path.home() / ".local/share/opencode/storage",
        Path.home() / ".local/state/opencode",
        Path.home() / ".cache/opencode",
        Path("/tmp/opencode"),
    ]
    cleanup_rc = 0
    for sid in session_ids:
        deleted = subprocess.run([opencode, "session", "delete", sid], text=True, capture_output=True)
        if deleted.returncode != 0:
            cleanup_rc = deleted.returncode or 1
            msg = (deleted.stderr or deleted.stdout or "").strip()
            print(f"ask_opencode: warning: failed to delete opencode session {sid}: {msg}", file=sys.stderr)
        # `opencode session delete` removes the visible session record but can
        # leave session_diff/<sessionID>.json behind. Remove exact session-id
        # residues under OpenCode-owned state/cache roots.
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
    return cleanup_rc


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
    agent = args.agent
    auto_title = args.title is None
    title = args.title or f"codex-ask-opencode-{os.getpid()}-{int(time.time())}"

    cmd = [opencode, "run", "--format", "json", "--dir", str(workdir), "--title", title]
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

    stdout = ""
    stderr = ""
    return_code = 0
    interrupted = False
    timed_out = False
    proc: subprocess.Popen[str] | None = None

    try:
        proc = subprocess.Popen(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,
        )
        stdout, stderr = proc.communicate(timeout=args.timeout)
        return_code = proc.returncode or 0
    except subprocess.TimeoutExpired:
        timed_out = True
        return_code = 124
        if proc is not None:
            terminate_process_group(proc)
            out, err = proc.communicate()
            stdout += out or ""
            stderr += err or ""
    except KeyboardInterrupt:
        interrupted = True
        return_code = 130
        if proc is not None:
            terminate_process_group(proc)
            out, err = proc.communicate()
            stdout += out or ""
            stderr += err or ""

    texts, session_ids = parse_session_ids(stdout)
    if auto_title and not session_ids:
        session_ids.extend(discover_sessions_by_title(opencode, title, workdir))
    cleanup_rc = cleanup_sessions(opencode, session_ids, args.keep_session)

    if args.raw_json:
        if stdout:
            print(stdout, end="")
        if stderr:
            print(stderr, end="", file=sys.stderr)
        if timed_out:
            print(f"ask_opencode: timeout after {args.timeout}s; child process terminated", file=sys.stderr)
        if interrupted:
            print("ask_opencode: interrupted; child process terminated", file=sys.stderr)
        return cleanup_rc or return_code

    output = "".join(texts).strip()
    if output:
        print(output)
        if timed_out:
            print(f"ask_opencode: timeout after {args.timeout}s; partial output shown", file=sys.stderr)
        if interrupted:
            print("ask_opencode: interrupted; partial output shown", file=sys.stderr)
        return cleanup_rc or (0 if return_code == 1 else return_code)

    if stderr.strip():
        print(stderr.strip(), file=sys.stderr)
    if timed_out:
        print(f"ask_opencode: timeout after {args.timeout}s; child process terminated", file=sys.stderr)
    elif interrupted:
        print("ask_opencode: interrupted; child process terminated", file=sys.stderr)
    else:
        print("ask_opencode: no text output from opencode; rerun with --raw-json for debugging", file=sys.stderr)
    return cleanup_rc or return_code or 1


if __name__ == "__main__":
    raise SystemExit(main())
