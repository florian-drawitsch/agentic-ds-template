# agent-kit

Skills and profile for coding agents, distributed as a plugin marketplace for Claude Code and
Codex.

Patterns proven in dozens of data science projects with leading companies in various industries,
including pharma, automotive, banking and retail.

## Contents (planned)

A curated marketplace for Claude Code and Codex:

| Plugin | Source | Purpose |
| --- | --- | --- |
| `superpowers` | [obra/superpowers](https://github.com/obra/superpowers), pinned | Brainstorming, planning, test-driven execution, review, finishing branches |
| `project` | this repository | `adr`, `kickoff`, `template-update` for projects generated from the template |

Only plugins that work in both harnesses are included. `profile/` holds personal writing rules,
which `task install-profile` installs at user level for both harnesses.

Projects generated from [ds-project-template](../ds-project-template) register this marketplace
at a pinned ref.

## Status

Design phase. See [docs/adr](docs/adr/index.md).

## License

[MIT](LICENSE)
