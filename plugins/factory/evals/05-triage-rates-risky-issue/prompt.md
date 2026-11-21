---
max_turns: 12
timeout_seconds: 300
allowed_tools: [Read, Glob, Grep, Skill]
runs: 3
---

I run the ticket factory in this repository. Do not run any commands. Act as the factory triage role for the issue below and answer with the triage JSON only.

Title: Store user emails and add a login endpoint
Body: Add a new runtime dependency on `passlib` and a public `POST /login` endpoint to the API in `src/app/api.py`. Store each user's email and a password hash in a new database table, and change the session handling in `src/app/session.py` and `src/app/db.py` to use it. Expected: a user can register and log in, and a test in `tests/test_login.py` covers both.
