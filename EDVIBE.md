# EDVIBE.md — target analysis (GUI/Web-app -> CLI mapping)

## Software
- **edvibe.com** — "next-gen language teaching platform" (formerly ProgressMe).
  Micro-frontend SPA (module federation). No public API; everything runs over a
  private WebSocket RPC gateway.

## Architecture findings (Sep 2026, live)

### Transport
- Config `https://edvibe.com/appWebSettings.json` is AES-128-ECB encrypted
  (the decryption key is intentionally not reproduced here) — it decrypts to
  shell remotes + `apiUrl`/cloud config.
- Real API = WebSocket `wss://proxy-a.edvibe.com/websocket?token=<Auth-Token>`
  (proxy host differs by data cloud; edvibe.com runs cloud `edb-a`).
- **Auth-Token is a client-minted UUIDv4 cookie** — no server handshake needed
  to mint it.
- WS request: `{"Controller","Method","ProjectName","RequestId","Value":<json string>}`;
  response matches `RequestId`, has `IsSuccess/Value/ErrorMessage/ErrorCode`.

### Auth (all over the same WS, ProjectName "Users")
1. `AccountWsController.GetAccountRoles {Email,Password,Domain}` -> `[roleInt]`
2. `AccountWsController.Login {Email,Password,RememberMe,AuthToken,CurrentDomain,
   AccountRole,UserRole,ClientTime,DeviceType}` -> user profile.
   AccountRole: 0 Admin, 1 TeacherCurator, 2 Student, 3 PublisherAdmin.
   UserRole: 4 SchoolAdmin (teacher-ish), 1 Pupil, 6 PublisherAdmin.

### Materials domain (ProjectName "Books")
- `BookWsController.GetPersonalBooks {Page:{Skip,Take},IsInversion}` ->
  `{Items:[{Folder:{Id,Name,CountMaterials}} | {Book:{Id,Name,...}}]}`
- `BookWsController.GetSchoolBooks {FolderId?,Domain,SortClass,IsInversion}`
- `BookWsController.BookUpsert` -> new material id (a "Book"/учебник)
- `CourseWsController.GetBookCourses {BookId,TeacherId,Domain}` -> courses
- `LessonWsController.LessonUpsert` -> lesson id; lesson auto-creates a
  "Section 1" (`LessonWsController.GetLessonWithId` -> `Sections[]`)
- UI route: `/cabinet/school/materials/personal`, material editor
  `/cabinet/school/materials/book/<id>/content`, lesson editor
  `/lesson-editor/book/<b>/lesson/<l>/section/<s>`.

### Exercises (ProjectName "Exercises")
- `GetExerciseWsController.LoadExercises {IsTeacher,SectionId,LessonId,LessonSection}` ->
  `{SectionId, Items:[exercise]}`
- `SaveExerciseWsController.SaveExercise` with payload
  `{ClassId, Domain, ExerciseView:{...}, AiUsed:false, UsedNewConstructor:false,
   ClientTime, DeviceType}` -> saved exercise. (DTO wrapper `{Data,ClassId}` is
   NOT used by the editor — that combination 500s.)
- Exercise `Type 22` = Note: content block whose `Note.Text` holds arbitrary
  HTML (`<h3>/<p>/<ul>/<ol>/<em>...`), `Note.ShowToPupil:true`. This is the
  vehicle used to import HTML handouts.
- Type map seen: 3 Videos, 5 Test, 6 MappingWords, 21 AddWordsToVocabulary,
  22 Note, 25 Topic, 27 Images, 31/32 (misc).

## GUI action -> harness command mapping
| UI action | API | CLI |
|---|---|---|
| Log in (email+password) | GetAccountRoles + Login | `whoami` (lazy auth) |
| Materials page list | GetPersonalBooks | `materials list` |
| Create button -> dialog -> Create | BookUpsert | `materials create` |
| Open material -> Add a lesson -> dialog | LessonUpsert (+ auto section) | `lesson add` |
| Lesson editor -> Add exercise -> New/Standard builder -> Article/Note -> Save | SaveExerciseWsController.SaveExercise | `content add-note`, `import --html` |

## Backend rules
- The "real software backend" is the live WS API; no synthetic reimplementation.
- No delete/update commands exist on purpose (user mandate: never delete
  materials). Undo/redo is not supported by the target for these operations.
