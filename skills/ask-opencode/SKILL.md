---
name: ask-opencode
license: MIT
description: Delegate a bounded task from Codex to local OpenCode via `opencode run`, especially for independent code review, repo exploration, architecture critique, or a scoped edit when using OpenCode as a second agent. Use when the user says ask opencode, call opencode, use opencode as subagent, or wants Codex to make OpenCode help.
---

# ask-opencode

Use this skill to have Codex call local OpenCode as an external helper process.

## Default rule

Prefer read-only delegation unless the user explicitly wants OpenCode to edit files.

## Command

Run the bundled wrapper from this skill directory:

```bash
python3 /home/cagedbird/.codex/skills/ask-opencode/scripts/ask_opencode.py [options] -- "prompt"
```

The wrapper calls:

```bash
opencode run --format json --dir <dir> ... <prompt>
```

prints only OpenCode text output, and deletes the created OpenCode session by default so one-shot delegation does not pollute OpenCode history.

## History behavior

Default behavior is ephemeral: after extracting output, the wrapper deletes the created OpenCode session with `opencode session delete <sessionID>` and removes exact session-id residue such as `session_diff/<sessionID>.json` under OpenCode state/cache roots.

Only pass `--keep-session` when the user explicitly wants the OpenCode conversation to remain visible in OpenCode history.

On Ctrl-C or timeout, the wrapper terminates the OpenCode child process group and still attempts session cleanup from partial JSON output or the generated unique title.

## Model selection

The wrapper supports explicit OpenCode model selection:

```bash
python3 /home/cagedbird/.codex/skills/ask-opencode/scripts/ask_opencode.py \
  --model deepseek/deepseek-v4-flash \
  -- "<prompt>"
```

Use any model string accepted by `opencode run --model`, usually `provider/model`. Examples from the local OpenCode config include:

- `deepseek/deepseek-v4-flash` for cheap/fast helper work
- `deepseek/deepseek-v4-pro` for harder review, architecture, or debugging

If the user names a model, always pass `--model <provider/model>`.

## Modes

### Read-only diagnosis / review / exploration

Use this for independent opinions, code review, root-cause guesses, repo mapping, and plan critique:

```bash
python3 /home/cagedbird/.codex/skills/ask-opencode/scripts/ask_opencode.py \
  --mode readonly \
  --model deepseek/deepseek-v4-flash \
  --dir "$PWD" \
  -- "只读检查当前仓库：<task>。不要修改文件。输出结论和证据路径。"
```

Default: no explicit OpenCode agent. The wrapper passes `--dangerously-skip-permissions` because `opencode run` is non-interactive; keep the prompt read-only. Pass `--agent <primary-agent>` only when you know that agent works with `opencode run`.

### Edit mode

Use only when the user explicitly allows OpenCode to modify files. Keep scope narrow:

```bash
python3 /home/cagedbird/.codex/skills/ask-opencode/scripts/ask_opencode.py \
  --mode edit \
  --model deepseek/deepseek-v4-flash \
  --dir "$PWD" \
  -- "只允许修改 <files>。完成 <task>。最后列出改动和验证。"
```

Default: no explicit OpenCode agent; passes `--dangerously-skip-permissions` so OpenCode can complete the scoped edit. Pass `--agent <primary-agent>` only when needed.

## Good prompts

- Choose `--model` explicitly when model quality/cost matters.
- State read-only vs edit clearly.
- Restrict target files or directories.
- Ask for exact file paths and concise evidence.
- For edit mode, tell OpenCode to avoid unrelated cleanup.

## Guardrails

- Do not run edit mode for broad or destructive tasks.
- Do not let Codex and OpenCode edit the same files concurrently.
- After edit mode, Codex must inspect `git diff` and run the relevant verification itself before claiming completion.
- If OpenCode output is empty, rerun with `--raw-json` only for debugging.
- Do not pass `--keep-session` for subagent-style one-shot calls unless the user asks to preserve OpenCode history.
- On Ctrl-C/timeout, expect exit code 130/124 and no persistent OpenCode session when cleanup succeeds.
