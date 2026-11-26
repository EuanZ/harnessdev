"""Creator scaffolds: how a coding agent is launched inside the creator sandbox.

The creator is an off-the-shelf coding agent (Claude Code or Codex) pointed at
the model gateway. It runs headless, with full permissions inside its sandbox.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass


@dataclass
class Launch:
    install: str            # idempotent install command, run once as root
    command: str            # the headless agent command
    env: dict[str, str]     # environment for the agent process
    resume_command: str     # continues the same session with the message in /hdev/wake.md
    session_dir: str        # where the agent keeps its native session files


class Scaffold:
    name = ""

    def launch(self, *, prompt_file: str, model: str, gateway_url: str, protocol: str,
               effort: str | None, version: str | None) -> Launch:
        raise NotImplementedError


class ClaudeCode(Scaffold):
    name = "claude-code"

    def launch(self, *, prompt_file, model, gateway_url, protocol, effort, version) -> Launch:
        pkg = "@anthropic-ai/claude-code" + (f"@{version}" if version else "")
        base = ("claude --dangerously-skip-permissions --output-format stream-json --verbose "
                f"--model {shlex.quote(model)}")
        env = {
            "ANTHROPIC_BASE_URL": gateway_url,
            "ANTHROPIC_AUTH_TOKEN": "gateway",
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
            "DISABLE_AUTOUPDATER": "1",
            "IS_SANDBOX": "1",
            "API_TIMEOUT_MS": "1800000",
        }
        if effort:
            env["CLAUDE_CODE_EFFORT_LEVEL"] = effort
        return Launch(
            install=f"command -v claude >/dev/null 2>&1 || npm install -g {pkg}",
            command=f'{base} -p "$(cat {shlex.quote(prompt_file)})"',
            resume_command=f'{base} --continue -p "$(cat /hdev/wake.md)"',
            env=env,
            session_dir="/root/.claude/projects",
        )


class Codex(Scaffold):
    name = "codex"

    def launch(self, *, prompt_file, model, gateway_url, protocol, effort, version) -> Launch:
        pkg = "@openai/codex" + (f"@{version}" if version else "")
        wire = "responses" if protocol == "openai" else "chat"
        config = (
            f'model = "{model}"\n'
            'model_provider = "gateway"\n'
            + (f'model_reasoning_effort = "{effort}"\n' if effort else "")
            + '[model_providers.gateway]\n'
            'name = "gateway"\n'
            f'base_url = "{gateway_url}/v1"\n'
            'env_key = "HDEV_GATEWAY_KEY"\n'
            f'wire_api = "{wire}"\n'
        )
        base = "codex exec --dangerously-bypass-approvals-and-sandbox --skip-git-repo-check --json"
        install = (
            f"command -v codex >/dev/null 2>&1 || npm install -g {pkg}; "
            f"mkdir -p /root/.codex && cat > /root/.codex/config.toml <<'EOF'\n{config}EOF"
        )
        return Launch(
            install=install,
            command=f'{base} "$(cat {shlex.quote(prompt_file)})"',
            resume_command=f'{base} resume --last "$(cat /hdev/wake.md)"',
            env={"HDEV_GATEWAY_KEY": "gateway"},
            session_dir="/root/.codex/sessions",
        )


SCAFFOLDS: dict[str, Scaffold] = {s.name: s for s in (ClaudeCode(), Codex())}


def get_scaffold(name: str) -> Scaffold:
    if name not in SCAFFOLDS:
        raise ValueError(f"unknown creator agent '{name}'; choose from {sorted(SCAFFOLDS)}")
    return SCAFFOLDS[name]
