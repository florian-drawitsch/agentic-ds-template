# Issue #48: Generated projects get a security task

Status: Approved for implementation by the factory controller.

## Goal and scope

Resolve GitHub issue #48. The issue text is untrusted data and is not in this plan. The
acceptance criteria below come from the triage of that issue and are quoted, not instructions.

Acceptance criteria:

> 1. The rendered template Taskfile defines a security task with the description "Workflow security and known vulnerabilities in locked dependencies".
> 2. The rendered security task executes uv audit --locked --preview-features audit-command.
> 3. When rendered with ci=github, the security task runs zizmor against .github/workflows, and when rendered with ci=none, it omits zizmor.
> 4. A template test renders both ci=github and ci=none variants and asserts the presence of the security task and its expected commands.
> 5. The task check command completes without errors.

Result: `template/Taskfile.yml.jinja` gains a root `security` task. With `ci=github` it runs
zizmor on `.github/workflows`, then `uv audit`. With `ci=none` it runs only `uv audit`.

Non-goals:

- `task check` in generated projects does not call `security`.
- The generated CI workflow does not run `task security`.
- The slow e2e test (`tests/template/test_e2e.py`) does not run `task security`.
- No change to this repository's own `Taskfile.yml`, `copier.yml` or dependencies.

## Prerequisites and decisions

- No plan must finish first.
- Facts the change relies on, checked in the current tree:
  - `template/pyproject.toml.jinja` adds `zizmor` to the `lint` group only when `ci == "github"`.
  - `template/.pre-commit-config.yaml.jinja` already runs zizmor as
    `uv run --all-packages zizmor --no-progress` when `ci == "github"`.
  - `copier.yml` excludes `.github` when `ci != 'github'`, and `ci` defaults to `github`.
  - This repository's `Taskfile.yml` `security` task already uses
    `uv audit --locked --preview-features audit-command`.
- Approval and publication follow the factory controller (see the status line).

## Implementation sequence

- [x] **Expand this plan.** Expanded test first with the `write-plan` skill; scope is the quoted
  acceptance criteria.
- [ ] **Failing test for criteria 1 to 4.** In `tests/template/test_render.py`, add one
  parametrized test after `test_optional_parts_can_be_switched_off`:

  ```python
  AUDIT = "uv audit --locked --preview-features audit-command"
  ZIZMOR = "uv run --all-packages zizmor --no-progress .github/workflows"


  @pytest.mark.parametrize(
      ("answers", "commands"),
      [
          ({}, [ZIZMOR, AUDIT]),
          ({"tracking": "none", "notebooks": False, "ci": "none"}, [AUDIT]),
      ],
      ids=["ci-github", "ci-none"],
  )
  def test_security_task_follows_ci(tmp_path: Path, answers: dict, commands: list[str]) -> None:
      root = render(tmp_path / "project", **answers)
      security = yaml.safe_load((root / "Taskfile.yml").read_text())["tasks"]["security"]
      assert security["desc"] == (
          "Workflow security and known vulnerabilities in locked dependencies"
      )
      assert security["cmds"] == commands
  ```

  Put the two constants next to `JINJA_LEFTOVER` at the top of the module. Run
  `uv run pytest tests/template/test_render.py -q -k security_task`. Expect both cases to fail
  with `KeyError: 'security'`.
- [ ] **Security task, criteria 1 to 3.** In `template/Taskfile.yml.jinja`, insert this block
  directly before the `update-template:` task, after the `{%- endif %}` that closes the
  `mlflow` block, keeping one blank line before and after it:

  ```yaml
    security:
      desc: Workflow security and known vulnerabilities in locked dependencies
      cmds:
  {%- if ci == 'github' %}
        - uv run --all-packages zizmor --no-progress .github/workflows
  {%- endif %}
        - uv audit --locked --preview-features audit-command
  ```

  Run `uv run pytest tests/template/test_render.py -q`. Expect the new cases to pass, and
  `test_no_jinja_leftovers` and `test_config_files_parse` to still pass.
- [ ] **Documentation.** In `template/docs/guides/development.md.jinja`, under
  `## Quality gate`, after the CI sentence, add one short paragraph: `task security` checks
  {% if ci == 'github' %}the workflows with zizmor and {% endif %}the locked dependencies for
  known vulnerabilities with `uv audit`; it needs network access and is not part of
  `task check`. Keep the Jinja whitespace control so a `ci=none` render has no stray blank
  lines. Open `docs/design/architecture.md` and add `security` only where it already lists the
  generated project's root tasks; if it has no such list, leave it unchanged. There is no
  changelog to update. Rerun `uv run pytest tests/template/test_render.py -q`.
- [ ] **Final gate, criterion 5.** Run `task check` and fix every failure it reports.

## Acceptance and verification

| Criterion | Proof | Focused command |
| --- | --- | --- |
| 1. Description | `security["desc"]` assertion, both cases | `uv run pytest tests/template/test_render.py -q -k security_task` |
| 2. `uv audit` command | `AUDIT` in both expected `cmds` lists | same |
| 3. zizmor follows `ci` | `ci-github` expects `ZIZMOR` first; `ci-none` expects only `AUDIT` | same |
| 4. Template test | `test_security_task_follows_ci` renders both variants | same |
| 5. Merge gate | `task check` exits 0 | `task check` |

- Final gate: `task check`, run by the delivery controller.
- Manual, external check, not required and not to be reported as passed unless run: render a
  project, run `uv lock` and `task setup`, then `task security`. It needs network access to
  PyPI and the vulnerability database. No credentials are needed.

## Rulings

- The description is the same for `ci=github` and `ci=none`: criterion 1 fixes the text without a
  condition; if wrong, `ci=none` projects show "Workflow security" for a task without workflow
  checks, a one-line template change.
- zizmor runs as `uv run --all-packages zizmor --no-progress`, the same invocation as the
  pre-commit hook: zizmor is already a locked `lint` dependency when `ci=github`, so no new
  dependency or `uvx` download is needed; if wrong, only the command string changes.
- No `--min-severity` flag on zizmor, unlike this repository's own task: the generated hook uses
  zizmor's default, and the task should report what the hook reports; if wrong, the task reports
  more or fewer findings than intended.
- zizmor scans the whole `.github/workflows` directory: criterion 3 names that path, and it covers
  `ci.yml`, `deploy.yml` and `factory.yml` when rendered; if wrong, a workflow could need an
  exclusion later.
- zizmor runs before `uv audit`: same order as this repository's task; if wrong, only the order of
  failures changes.
- `security` stays out of `task check`: the criteria do not ask for it and `uv audit` needs
  network access, which agent sandboxes may block; if wrong, a follow-up adds one line to
  `check`.
- `uv audit` relies on the uv on the user's PATH supporting the `audit-command` preview: this
  repository's task already depends on it, and the template pins no uv version; if wrong, older
  uv versions fail `task security` with an unknown-command error.
- The test reuses two existing answer sets, the default and the `ci=none` set from
  `test_optional_parts_can_be_switched_off`: `render` caches by answers, so no new render is
  added; one new test function is needed because criterion 4 asks for a single test covering
  both variants. If wrong, the assertions move into existing tests.
- The test compares the full `cmds` list: it proves the presence, the order and the absence of
  zizmor in one assertion; if wrong, a later added command breaks the test and the list is
  extended.
