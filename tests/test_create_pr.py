"""Verify template-conformant PR descriptions and repeatable publication without live API calls."""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "plugins/project/skills/github-create-pr/scripts/create_pr.py"
SPEC = importlib.util.spec_from_file_location("github_pr", SCRIPT)
assert SPEC is not None
assert SPEC.loader is not None
pr = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pr)

HEADINGS = pr.required_headings(ROOT / "template")
HEAD = "0123456789abcdef0123456789abcdef01234567"


def _body() -> str:
    return "\n\n".join(f"{heading}\n\nConcrete evidence." for heading in HEADINGS)


def _fake_git(*args: str) -> str:
    answers = {
        "remote": "git@github.com:example/project.git",
        "branch": "feat/example",
        "rev-parse": str(ROOT / "template"),
        "rev-list": "1",
    }
    return answers.get(args[0], "")


def test_headings_follow_the_repository_template() -> None:
    template = (ROOT / "template/.github/pull_request_template.md").read_text()
    assert HEADINGS[0] == "## Summary"
    assert all(heading in template for heading in HEADINGS)


def test_missing_template_falls_back_to_standard_sections(tmp_path: Path) -> None:
    assert pr.required_headings(tmp_path) == pr.DEFAULT_HEADINGS


@pytest.mark.parametrize("defect", ["empty", "order", "placeholder", "attribution"])
def test_description_rejects_incomplete_handoffs(tmp_path: Path, defect: str) -> None:
    body = _body()
    if defect == "empty":
        body = body.replace("Concrete evidence.", "", 1)
    elif defect == "order":
        body = body.replace("## Summary", "## Wrong section")
    elif defect == "placeholder":
        body += "\nTODO"
    else:
        body += "\nGenerated with Claude"
    path = tmp_path / "body.md"
    path.write_text(body)
    with pytest.raises(ValueError, match=r"description|attribution"):
        pr._description(path, HEADINGS)


def test_title_requires_conventional_commit() -> None:
    assert pr._title("feat(deliver): publish to GitHub")
    with pytest.raises(ValueError, match="Conventional Commit"):
        pr._title("Publish to GitHub")


def _arguments(monkeypatch, body: Path, *extra: str) -> None:
    monkeypatch.setattr(
        pr.sys,
        "argv",
        [
            "create_pr.py",
            "--title",
            "fix(deliver): create PR",
            "--description-file",
            str(body),
            "--execute",
            *extra,
        ],
    )


def test_retry_reuses_existing_pr_and_updates_description(tmp_path: Path, monkeypatch) -> None:
    body = tmp_path / "body.md"
    body.write_text(_body())
    output = tmp_path / "response.json"
    _arguments(monkeypatch, body, "--result-file", str(output))
    monkeypatch.setattr(pr, "_git", _fake_git)
    calls = []

    def fake_gh(*args: str) -> str:
        assert "--reviewer" not in args
        assert "--add-reviewer" not in args
        assert not any("requested_reviewers" in arg for arg in args)
        calls.append(args[:2])
        if args[:2] == ("pr", "list"):
            return json.dumps([{"number": 12, "title": "old", "body": "old"}])
        if args[:2] == ("pr", "view"):
            assert args[2] == "12"
            return json.dumps(
                {
                    "number": 12,
                    "url": "https://github.com/example/project/pull/12",
                    "state": "OPEN",
                    "headRefName": "feat/example",
                    "headRefOid": HEAD,
                    "baseRefName": "main",
                }
            )
        return ""

    monkeypatch.setattr(pr, "_gh", fake_gh)
    assert pr.main() == 0
    assert ("pr", "create") not in calls
    assert ("pr", "edit") in calls
    assert json.loads(output.read_text())["headRefOid"] == HEAD


def test_new_pr_is_created_once(tmp_path: Path, monkeypatch) -> None:
    body = tmp_path / "body.md"
    body.write_text(_body())
    _arguments(monkeypatch, body)
    monkeypatch.setattr(pr, "_git", _fake_git)
    calls = []

    def fake_gh(*args: str) -> str:
        assert "--reviewer" not in args
        assert "--add-reviewer" not in args
        assert not any("requested_reviewers" in arg for arg in args)
        calls.append(args[:2])
        if args[:2] == ("pr", "list"):
            return "[]"
        if args[:2] == ("pr", "create"):
            return "https://github.com/example/project/pull/7"
        if args[:2] == ("pr", "view"):
            assert args[2] == "7"
            return json.dumps({"url": "https://github.com/example/project/pull/7"})
        return ""

    monkeypatch.setattr(pr, "_gh", fake_gh)
    assert pr.main() == 0
    assert calls.count(("pr", "create")) == 1


def test_failed_push_does_not_create_pr(tmp_path: Path, monkeypatch) -> None:
    body = tmp_path / "body.md"
    body.write_text(_body())
    _arguments(monkeypatch, body, "--push")

    def fake_git(*args: str) -> str:
        if args[0] == "push":
            raise subprocess.CalledProcessError(1, args)
        return _fake_git(*args)

    monkeypatch.setattr(pr, "_git", fake_git)

    def fake_gh(*args: str) -> str:
        if args[0] == "pr":
            pytest.fail("An unsuccessful push must not create a PR")
        return ""

    monkeypatch.setattr(pr, "_gh", fake_gh)
    with pytest.raises(subprocess.CalledProcessError):
        pr.main()


def test_non_github_remote_is_rejected() -> None:
    with pytest.raises(ValueError, match="not GitHub"):
        pr._parse_remote("git@bitbucket.org:team/project.git")
