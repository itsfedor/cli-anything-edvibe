# TEST.md — cli-anything-edvibe

## Part 1: Test plan (written before implementation)

### Inventory
- `test_core.py` — 6 unit tests (no network):
  - importer: title extraction, lone-h1 drop, h2 split level, sub-heading
    retention inside sections, style/onclick stripping, lead-intro merge
  - client_time format, session role picking, session-file roundtrip
- `test_full_e2e.py` — 5 live-API tests (gated on EDVIBE_EMAIL/EDVIBE_PASSWORD):
  1. login & whoami
  2. create material (BookUpsert) + verify it appears in GetPersonalBooks
  3. add lesson/unit + auto section (LessonUpsert, GetLessonWithId)
  4. add a Note exercise (SaveExerciseWsController) + verify via LoadExercises
  5. full `import_html` pipeline (material + lesson + 3 note blocks) + verify

### E2E safety contract
- Creates only fresh objects named `CLI-HARNESS-E2E …`; never deletes or edits
  pre-existing materials/lessons/exercises. Credentials only via env vars.

## Part 2: Results (appended after runs)

### Unit — run 2026-09-04 (offline)
```
6 passed in 0.07s
```
`test_core.py::test_title_extracted`, `test_sections_split_and_titled`,
`test_style_and_handlers_removed`, `test_client_time_format`,
`test_session_pick_role`, `test_session_file_roundtrip` — PASS

### E2E — run 2026-09-04 against a live school account
```
5 passed in 14.11s
```
test_001_login_and_whoami, test_002_create_material,
test_003_add_lesson_and_section, test_004_add_note_exercise,
test_005_import_html_end_to_end — PASS

### Manual acceptance — real handout import (2026-09-04)
```
cli-anything-edvibe --json import --html "handout.html"
```
Result: material #1206362, course #2866588, lesson #19490961, section #96130097,
5 Note exercises (Sections 1–4 + ADDITIONAL TEACHER RESOURCES), each carrying
the section's full HTML. Verified via `LoadExercises` and by rendering the
lesson editor page (screenshot `final_lesson_editor.png`).

### Coverage notes
- Exercise types beyond Note (Test, Video, Topic…) are read-only in this
  harness; creation of interactive exercise types is future work.
- Folders: listing works; creating a material *inside* an Edvibe folder is not
  yet exposed (needs FolderBookWsController mapping).
