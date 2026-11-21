"""The interactive `secrets` command: what it asks, what it stores, and what it never shows."""

import json
from pathlib import Path

import pytest

from tests.factory_fakes import FakeGitHub
from tests.helpers import load_script

SCRIPT = Path("plugins/factory/skills/factory/scripts/factory.py")
factory = load_script(SCRIPT, "factory_secrets")
ALL = {"FACTORY_GH_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN", "ANTHROPIC_API_KEY", "OPENAI_API_KEY"}


class Scripted:
    """A console that answers from lists and records what was opened or run."""

    def __init__(self, answers=(), secrets=(), interactive=True):
        self.answers, self.values = list(answers), list(secrets)
        self.opened, self.ran, self.prompts = [], [], []
        self.interactive = interactive

    def console(self):
        return factory.Console(
            ask=lambda prompt: self.prompts.append(prompt) or self.answers.pop(0),
            secret=lambda prompt: self.prompts.append(prompt) or self.values.pop(0),
            open=self.opened.append,
            run=lambda command: self.ran.append(list(command)) or 0,
            interactive=self.interactive,
        )


def gh():
    github = FakeGitHub()
    github.error_type = factory.GhError
    return github


def run(argv, github, console, tmp_path):
    return factory.main(argv, github=github, root=tmp_path, console=console)


def test_both_vendors_need_three_secrets_by_default(tmp_path):
    specs = factory.required_secrets(factory.load_config(tmp_path))
    assert [sorted(spec.names) for spec in specs] == [
        ["FACTORY_GH_TOKEN"],
        ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN"],
        ["OPENAI_API_KEY"],
    ]


def test_check_lists_missing_secrets_and_never_prompts(tmp_path, capsys):
    github, script = gh(), Scripted()
    assert run(["secrets", "--check"], github, script.console(), tmp_path) == 1
    out = capsys.readouterr().out
    assert "3 factory secret(s) missing" in out
    assert factory.TOKEN_URL in out
    assert "needs none" in out
    assert script.prompts == []
    assert github.secrets == {}


def test_non_interactive_run_prints_instructions_and_succeeds(tmp_path, capsys):
    script = Scripted(interactive=False)
    assert run(["secrets"], gh(), script.console(), tmp_path) == 0
    assert "task factory:secrets" in capsys.readouterr().out
    assert script.prompts == []


def test_interactive_run_sets_each_secret_without_printing_values(tmp_path, capsys):
    github = gh()
    script = Scripted(
        answers=["1", "1", "1"],
        secrets=["ghp_classic", "sk-ant-oat-subscription", "sk-openai"],
    )
    assert run(["secrets"], github, script.console(), tmp_path) == 0
    assert github.secrets == {
        "FACTORY_GH_TOKEN": "ghp_classic",
        "CLAUDE_CODE_OAUTH_TOKEN": "sk-ant-oat-subscription",
        "OPENAI_API_KEY": "sk-openai",
    }
    assert script.opened == [factory.TOKEN_URL, "https://platform.openai.com/api-keys"]
    assert script.ran == [["claude", "setup-token"]]
    out = capsys.readouterr().out
    for value in github.secrets.values():
        assert value not in out
    assert "All factory secrets are set." in out


def test_gh_login_choice_reuses_the_login_token_without_a_prompt(tmp_path):
    github = gh()
    github.secrets = {"ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "y"}
    script = Scripted(answers=["2"])
    assert run(["secrets"], github, script.console(), tmp_path) == 0
    assert github.secrets["FACTORY_GH_TOKEN"] == "gho_logintoken"
    assert script.opened == []


def test_skip_and_empty_values_store_nothing(tmp_path, capsys):
    github = gh()
    script = Scripted(answers=["s", "2", "1"], secrets=["", "  "])
    assert run(["secrets"], github, script.console(), tmp_path) == 0
    assert github.secrets == {}
    assert "3 still missing" in capsys.readouterr().out


def test_fine_grained_token_gets_a_board_warning(tmp_path, capsys):
    github = gh()
    github.secrets = {"ANTHROPIC_API_KEY": "x", "OPENAI_API_KEY": "y"}
    script = Scripted(answers=["1"], secrets=["github_pat_abc"])
    run(["secrets"], github, script.console(), tmp_path)
    assert "fine-grained tokens cannot reach a board" in capsys.readouterr().out


def test_present_secrets_need_nothing(tmp_path, capsys):
    github = gh()
    github.secrets = dict.fromkeys(ALL, "x")
    assert run(["secrets", "--check"], github, Scripted().console(), tmp_path) == 0
    assert "every factory secret is set" in capsys.readouterr().out


def test_single_vendor_config_asks_only_for_that_vendor(tmp_path):
    (tmp_path / "factory.toml").write_text('[deliver]\nharness = "codex"\nreviewer = "codex"\n')
    specs = factory.required_secrets(factory.load_config(tmp_path))
    assert set().union(*(spec.names for spec in specs)) == {"FACTORY_GH_TOKEN", "OPENAI_API_KEY"}


def test_init_points_to_the_secrets_step_only_when_needed(tmp_path, capsys):
    github = gh()
    assert factory.main(["init"], github=github, root=tmp_path) == 0
    assert "task factory:secrets" in capsys.readouterr().out
    github.secrets = dict.fromkeys(ALL, "x")
    factory.main(["init"], github=github, root=tmp_path)
    assert "task factory:secrets" not in capsys.readouterr().out
    github.secrets_error = "HTTP 403"
    assert factory.main(["init"], github=github, root=tmp_path) == 0


def test_secret_values_go_to_gh_on_stdin():
    calls = []

    class Recording(factory.GitHub):
        def _call(self, args, input=None):
            calls.append((list(args), input))
            return json.dumps([[{"name": "A"}]]) if args[0] == "api" else ""

    github = Recording()
    github.set_secret("OPENAI_API_KEY", "sk-secret")
    assert calls == [(["secret", "set", "OPENAI_API_KEY"], "sk-secret")]


@pytest.mark.parametrize(
    ("harness", "reviewer", "expected"),
    [
        ("claude", "gemini", ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "GEMINI_API_KEY"]),
        ("codex", "gemini", ["GEMINI_API_KEY", "OPENAI_API_KEY"]),
        ("claude", None, ["ANTHROPIC_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN", "OPENAI_API_KEY"]),
    ],
)
def test_secrets_cover_only_the_two_configured_vendors(harness, reviewer, expected):
    config = factory.Config(deliver=factory.DeliverConfig(harness=harness, reviewer=reviewer))
    names = sorted(name for spec in factory.required_secrets(config)[1:] for name in spec.names)
    assert names == expected
