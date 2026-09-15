---
name: "cli-anything-edvibe"
description: "Use when creating, listing or importing Edvibe (edvibe.com) school materials programmatically — materials, lessons (units), HTML note content. Never deletes anything."
---

# cli-anything-edvibe

CLI harness over Edvibe's private WebSocket RPC API for school materials.
Install: `pip install -e .` from the repo root.
Credentials: `EDVIBE_EMAIL` / `EDVIBE_PASSWORD` env or `--email/--password`
(session token cached in `~/.edvibe/session.json`, mode 600).

## Commands

- `cli-anything-edvibe whoami`
- `cli-anything-edvibe materials list [--json]` — folders & books
- `cli-anything-edvibe materials create "Name" [--description ...]`
- `cli-anything-edvibe lesson add --material <bookId> --name "Unit 1"`
  → prints lesson_id + auto section_id
- `cli-anything-edvibe lesson show --lesson <id>` — sections + exercises
- `cli-anything-edvibe content add-note --lesson <id> --section <id> --file f.html`
- `cli-anything-edvibe import --html handout.html` — HTML file → new material
  with one lesson containing one Note per <h2> section (h1 title dropped)
- bare `cli-anything-edvibe` starts a REPL

Add `--json` for machine-readable output.

## Rules
- **NEVER delete or edit existing materials/lessons/exercises** — the harness
  has no delete command by design (user mandate). Tests create only objects
  named `CLI-HARNESS-*`.
- All mutations are additive: create material → get course id → create lesson
  → auto section → SaveExercise (Type 22 Note, HTML in `Note.Text`).

## Wire notes (short)
WS `wss://proxy-a.edvibe.com/websocket?token=<uuid4>`; login = two RPCs on
`AccountWsController` (GetAccountRoles → Login). Materials in ProjectName
"Books" (BookWsController.BookUpsert / LessonUpsert), exercises in
ProjectName "Exercises" (SaveExerciseWsController.SaveExercise with
`{ClassId, Domain, ExerciseView, AiUsed:false, UsedNewConstructor:false,
ClientTime, DeviceType}`).
