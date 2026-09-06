# Working on Postulo

Postulo is a self-hosted, AGPL-3.0 Django application that manages a job search from the
applicant's side: listings, applications and their timelines, companies, CVs and cover
letters, reminders, interviews. One person's data never reaches another's. Read
`docs/PLAN.md` for the architecture and `CONTRIBUTING.md` for the rules; this file is the
short version for an AI assistant, and it defers to both.

## What this project promises

Four commitments are stated in the README and are not negotiable in code:

- **Never paywalled.** No feature gate, no tier, no licence key, nothing that "unlocks".
- **Secure, because of what it holds.** Every query is owner-scoped (`for_user()`), every
  file is served through a permission check, the CSP allows no inline script, secrets
  are encrypted, and `tests/security/` grows with every feature that touches a boundary.
- **Usable by everyone.** Keyboard, screen readers, scripts off, both themes at WCAG 2.2
  AA; the browser suite runs axe-core over every page it visits.
- **Modular.** Anything that could reasonably vary is a plugin behind an entry point, and
  a plugin carries its own `locale/`.

## How the code is shaped

- `src/postulo/<app>/` per domain: `core`, `accounts`, `jobs`, `applications`,
  `documents`, `resume`, `plugins`, `api`. Models inherit `OwnedModel`; views use
  `OwnedObjectMixin` (a foreign object is a 404, never a 403).
- The event log is the truth: change status through `change_status`, record with
  `record_event`, never poke fields directly.
- Server-rendered templates with htmx and Tailwind v4 (`npm run build:css`; the compiled
  CSS is committed). All JavaScript lives in `static/js/app.js` and vendored files —
  the CSP forbids inline scripts. Django `{# #}` comments are single-line only.
- Every `next` redirect goes through `safe_next()`.
- Every user-facing string is wrapped for translation. After adding or changing one, run
  `uv run python scripts/messages.py extract` so every catalogue gets its slot; a new
  translation carries the `draft` flag until a speaker reviews it (`docs/TRANSLATING.md`).

## Before saying a change is done

```sh
uv run ruff check . && uv run ruff format --check .
uv run pytest                                    # in-memory SQLite, warnings are errors
uv run pytest -m e2e --browser chromium          # the critical path plus axe-core
uv run python scripts/messages.py extract --check && uv run python scripts/messages.py check
```

A feature ships with its tests, its wiki page (authored in `wiki/`) and a CHANGELOG
entry under *Unreleased*. Commit messages explain why, not what. Never create a release
tag; releases are a deliberate, separate act.

## Working with AI

AI-written code is welcome here on the same terms as any other: a person proposes it,
has read it, can explain it, and answers for it. The assistant is a tool the contributor
uses, not a contributor. Say in the pull request that a model helped, the way you would
credit a library, and review the output with the care you would give a stranger's patch —
especially around ownership scoping, file handling and anything the security tests cover.
