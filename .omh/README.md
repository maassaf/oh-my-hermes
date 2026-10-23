# `.omh/` — oh-my-hermes repo-local artifact store

This directory is where **oh-my-hermes (OMH)** records durable, repo-scoped
artifacts when a command that produces one runs inside a repository. It is a
data store, not code: nothing in here executes.

## What lands here

| Path | Written by | Contents |
| --- | --- | --- |
| `plans/<slug>.md` | `omh hermes plan --record` | Recorded implementation plans (the `ralplan` / `ulw-plan` recording path) |
| `plan-variants/` | `omh hermes plan-variant` | Alternative plan drafts |
| `research/<slug>/` | research workflows | Research dossiers, syntheses, manifests |
| `coding/dynamic-workflows/` | `omh coding dynamic-workflow` | Prepared workflow contracts and charts |

Outside a repository the same commands write to the user-scope OMH home
(`~/.omh`) instead.

## Commit policy

Repo-local `.omh/` contents are **operational artifacts, ignored by
default** — in user projects OMH drops a `.omh/.gitignore` (`*`) so
`git add -A` never sweeps them in; in this repository the top-level
`.gitignore` lists `.omh/`. Committing an artifact (for example a reviewed
proposal worth keeping) is an explicit decision: `git add -f` it. Files that
are already tracked stay tracked — gitignore only hides untracked files.

## Do not confuse with the neighbouring roots

Several agent products keep look-alike dot-directories. They are different
products with different owners. Working ON this repository *with* one of
those wrappers is perfectly normal — an oh-my-claudecode or oh-my-opencode
session developing oh-my-hermes will naturally create its own `.omc/` or
`.omo/` state here, and the top-level `.gitignore` keeps that local. The
rule is about **ownership of artifacts**, not presence of directories:
OMH's recorded artifacts belong in OMH's root, and another wrapper's
runtime state never gets committed as if it were OMH's.

| Root | Product | Notes |
| --- | --- | --- |
| `.omh/` | **oh-my-hermes** (this product) | Repo-local artifacts, described above |
| `~/.omh/` | oh-my-hermes user scope | Skills, runtime state, HUD/todo stores |
| `~/.hermes/` | **Hermes Agent** (the host) | Hermes' own home: `config.yaml`, `state.db`, `plugins/`. The installed OMH **plugin** lives at `~/.hermes/plugins/omh/` — an OMH-managed install *into* Hermes' home, refreshed only through `omh update`, never by hand-copying. OMH installs nothing into Hermes' terminal (`tui-widgets/`, `skins/`) any more; setup and update remove what earlier versions put there. OMH never patches Hermes itself (`~/.hermes/hermes-agent/`). |
| `.hermes/` (repo-local) | Hermes Agent | Rare repo-scoped Hermes state; not OMH's |
| `.omc/` | oh-my-claudecode | That wrapper's own state when a Claude Code session works on this repo — fine locally, gitignored; OMH never writes here |
| `.omo/` | oh-my-opencode | That wrapper's own state when an OpenCode session works on this repo — fine locally, gitignored. OMH only *reads* `~/.omo/omo.json[c]` to import category model preferences |
| `.loop/` | loop runtimes | Not OMH's |

If an agent proposes recording an **OMH artifact** (a plan, todo, research
dossier) into `.omc/`, `.omo/`, or `~/.hermes/hermes-agent/` — or
committing another wrapper's state as if it were OMH's — that is state-root
drift, the class of bug fixed in #1017 (plans written to `.omc/`) and #1021
(plans committed under `.omo/`). The correct target for OMH artifacts is
always this directory (via the named `omh` commands) or the OMH home.
