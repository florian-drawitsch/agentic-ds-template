"""In-memory GitHub for the factory tests; nothing here touches the network."""

from typing import Any

CONTROLLER = "factory-bot"


class FakeGitHub:
    """Same methods as the controller's `GitHub`, backed by dicts."""

    def __init__(self) -> None:
        self.label_names: list[str] = []
        self.issues: dict[int, dict[str, Any]] = {}
        self.timelines: dict[int, list[dict[str, Any]]] = {}
        self.comment_log: dict[int, list[dict[str, Any]]] = {}
        self.permissions: dict[str, str] = {}
        self.pulls: dict[str, dict[str, Any]] = {}
        self.review_log: dict[int, list[dict[str, Any]]] = {}
        self.runs: dict[str, list[dict[str, Any]]] = {}
        self.files: dict[int, list[str]] = {}
        self.created: list[str] = []

    # test setup helpers
    def add_issue(
        self, number: int, labels: list[str], title: str = "t", state: str = "open"
    ) -> None:
        self.issues[number] = {"number": number, "title": title, "state": state, "labels": labels}

    def label_event(self, number: int, label: str, login: str, bot: bool = False) -> None:
        event = {
            "event": "labeled",
            "label": {"name": label},
            "actor": {"login": login, "type": "Bot" if bot else "User"},
        }
        self.timelines.setdefault(number, []).append(event)

    def add_comment(self, number: int, body: str, login: str = CONTROLLER) -> None:
        self.comment_log.setdefault(number, []).append({"body": body, "user": {"login": login}})

    def add_pull(self, number: int, issue: int, checks: str = "success", sha: str = "") -> None:
        sha = sha or f"sha{number}"
        branch = f"feat/{issue}-x"
        self.pulls[branch] = {"number": number, "branch": branch, "sha": sha}
        self.runs[sha] = [{"conclusion": checks}]

    # GitHub interface
    def viewer(self) -> str:
        return CONTROLLER

    def labels(self) -> list[str]:
        return list(self.label_names)

    def create_label(self, name: str, color: str, description: str) -> None:
        assert color
        assert description
        self.label_names.append(name)
        self.created.append(name)

    def issues_with_label(self, label: str) -> list[dict[str, Any]]:
        return [dict(i) for i in self.issues.values() if label in i["labels"]]

    def issue(self, number: int) -> dict[str, Any]:
        return dict(self.issues[number])

    def timeline(self, number: int) -> list[dict[str, Any]]:
        return self.timelines.get(number, [])

    def comments(self, number: int) -> list[dict[str, Any]]:
        return self.comment_log.get(number, [])

    def permission(self, user: str) -> str:
        return self.permissions.get(user, "none")

    def pull_for_branch(self, prefix: str) -> dict[str, Any] | None:
        return next((p for b, p in self.pulls.items() if b.startswith(prefix)), None)

    def reviews(self, pr: int) -> list[dict[str, Any]]:
        return self.review_log.get(pr, [])

    def check_runs(self, sha: str) -> list[dict[str, Any]]:
        return self.runs.get(sha, [])

    def changed_files(self, pr: int) -> list[str]:
        return self.files.get(pr, [])

    def add_labels(self, number: int, labels: list[str]) -> None:
        self.issues[number]["labels"] += labels

    def remove_label(self, number: int, label: str) -> None:
        self.issues[number]["labels"].remove(label)

    def comment(self, number: int, body: str) -> None:
        self.add_comment(number, body)
