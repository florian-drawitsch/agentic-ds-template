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
        # board
        self.repo = {"owner": "acme", "name": "acme/widgets"}
        self.repo_error: str | None = None
        self.project_error: str | None = None
        self.error_type: type[Exception] = RuntimeError  # tests set the script's GhError
        self.boards: dict[str, list[dict[str, Any]]] = {}
        self.fields: dict[int, list[dict[str, Any]]] = {}
        self.links: list[tuple[str, int, str]] = []
        self.option_updates: list[list[dict[str, str]]] = []
        self.items: dict[str, str] = {}
        self.item_values: dict[str, str] = {}
        self.views: dict[str, list[dict[str, Any]]] = {}

    # test setup helpers
    def add_issue(
        self, number: int, labels: list[str], title: str = "t", state: str = "open"
    ) -> None:
        self.issues[number] = {
            "number": number,
            "title": title,
            "state": state,
            "labels": labels,
            "url": f"https://github.com/acme/widgets/issues/{number}",
        }

    def add_board(
        self, owner: str, number: int, title: str, options: list[str] | None = None
    ) -> None:
        url = f"https://github.com/users/{owner}/projects/{number}"
        board = {"id": f"P{number}", "number": number, "title": title, "url": url}
        self.boards.setdefault(owner, []).append(board)
        names = ["Todo", "In Progress", "Done"] if options is None else options
        builtin = ["Title", "Assignees", "Labels", "Linked pull requests", "Updated"]
        self.fields[number] = [
            *({"id": f"F-{n}", "name": n, "type": "ProjectV2Field"} for n in builtin),
            {
                "id": f"F{number}",
                "name": "Status",
                "type": "ProjectV2SingleSelectField",
                "options": [{"id": f"O-{n}", "name": n} for n in names],
            },
        ]
        self.views[board["id"]] = [
            {"id": f"V{number}-1", "name": "View 1", "layout": "TABLE_LAYOUT", "query": "", "f": []}
        ]

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
        self.runs[sha] = [{"name": "ci", "conclusion": checks}]

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

    def repository(self) -> dict[str, str]:
        if self.repo_error:
            raise self.error_type(self.repo_error)
        return dict(self.repo)

    def projects(self, owner: str) -> list[dict[str, Any]]:
        if self.project_error:
            raise self.error_type(self.project_error)
        return [dict(b) for b in self.boards.get(owner, [])]

    def create_project(self, owner: str, title: str) -> dict[str, Any]:
        number = 1 + max((b["number"] for bs in self.boards.values() for b in bs), default=0)
        self.add_board(owner, number, title)
        return dict(self.boards[owner][-1])

    def link_project(self, owner: str, number: int, repo: str) -> None:
        self.links.append((owner, number, repo))

    def project_fields(self, owner: str, number: int) -> list[dict[str, Any]]:
        return self.fields[number]

    def set_field_options(self, field_id: str, options: list[dict[str, str]]) -> None:
        self.option_updates.append(list(options))
        for fields in self.fields.values():
            for item in fields:
                if item["id"] == field_id:
                    item["options"] = [{"id": f"O-{o['name']}", "name": o["name"]} for o in options]

    def add_item(self, owner: str, number: int, url: str) -> str:
        return self.items.setdefault(url, f"I{len(self.items) + 1}")

    def set_item_option(self, project_id: str, item_id: str, field_id: str, option: str) -> None:
        self.item_values[item_id] = option

    def _number(self, project_id: str) -> int:
        return next(b["number"] for bs in self.boards.values() for b in bs if b["id"] == project_id)

    def project_views(self, project_id: str) -> list[dict[str, Any]]:
        return [dict(v) for v in self.views[project_id]]

    def create_view(self, project_id: str, name: str, layout: str, field_ids: list[str]) -> str:
        view_id = f"V{len(self.views[project_id]) + 1}-{name}"
        view = {"id": view_id, "name": name, "layout": layout, "query": "", "f": list(field_ids)}
        self.views[project_id].append(view)
        return view_id

    def update_view(
        self, view_id: str, name: str, layout: str, query: str, field_ids: list[str]
    ) -> None:
        view = next(v for vs in self.views.values() for v in vs if v["id"] == view_id)
        view.update(name=name, layout=layout, query=query, f=list(field_ids))
