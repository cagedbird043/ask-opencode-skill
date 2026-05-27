# ask-opencode-skill

Codex skill for delegating a bounded one-shot task to local OpenCode via `opencode run`.

The skill is intentionally Codex-oriented: it treats OpenCode like an ephemeral helper process, extracts text output, and deletes the created OpenCode session by default so one-shot calls do not pollute OpenCode history.

## Install

```bash
gh skill install cagedbird043/ask-opencode-skill skills/ask-opencode --agent codex --scope user
```

## Requirements

- `opencode` available in `PATH`
- OpenCode providers configured locally
- Python 3
