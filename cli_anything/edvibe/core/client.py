"""Edvibe WebSocket RPC client + API wrappers (reverse-engineered wire protocol).

Protocol notes (verified live, Sep 2026):
- Auth: the browser mints a UUIDv4 'Auth-Token' cookie client-side; every WS connection is
  authenticated with wss://proxy-a.edvibe.com/websocket?token=<Auth-Token>.
- Login is itself a WS RPC pair on AccountWsController (ProjectName "Users"):
  GetAccountRoles {Email, Password, Domain} -> [roleInts]
  Login {Email, Password, RememberMe, AuthToken, CurrentDomain, AccountRole, UserRole,
         ClientTime, DeviceType}
  Role enums: AccountRole: 0=Administrator 1=TeacherCurator 2=Student 3=PublishingHouseAdmin
              UserRole:    0=Teacher 1=Pupil 2=Admin 3=Blogger 4=SchoolAdmin 5=AdminCourses
                           6=PublishingHouseAdmin 7=AcademyAdmin
- Message: {Controller, Method, ProjectName, RequestId, Value: json-string}; response echoes RequestId.
- Materials domain (ProjectName "Books"):
  BookWsController.BookUpsert            -> creates a material (Book)
  BookWsController.GetPersonalBooks      {Page:{Skip,Take},IsInversion} -> {Items:[{Folder|Book}]}
  BookWsController.GetSchoolBooks        {FolderId?,Domain,SortClass,IsInversion}
  CourseWsController.GetBookCourses      {BookId,TeacherId,Domain} -> [{Id (course), ...}]
  LessonWsController.GetCourseLessons    {CourseId} -> [lessons]
  LessonWsController.GetLessonWithId     {LessonId} -> lesson incl. Sections[]
  LessonWsController.LessonUpsert        {Id:0,...,CourseId,BookId,...} -> lesson id
  GetExerciseWsController.LoadExercises  {IsTeacher,SectionId,LessonId,LessonSection} -> {SectionId,Items}
  SaveExerciseWsController.SaveExercise  {ClassId, Domain, ExerciseView: <model>, AiUsed,
                                          UsedNewConstructor, ClientTime, DeviceType} -> saved model
  Exercise Type 22 = Note (content block, HTML text in Note.Text)
"""
import datetime
import json
import os
import uuid

import requests
import websocket

from .pacing import PaceLimitError, Pacer

WS_BASE = "wss://proxy-a.edvibe.com/websocket"
BASE = "https://edvibe.com"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36")

# Exercise type constants (verified subset)
EX_NOTE = 22          # Note: free HTML content block
EX_TOPIC = 25         # Topic / header block with HTML text
EX_VIDEO = 3
EX_TEST = 5
EX_MAPPING = 6
EX_TEXT = None         # "Article/Text/Writing" type not yet mapped

SESSION_DEFAULT = os.path.join(os.path.expanduser("~"), ".edvibe", "session.json")


class EdvibeError(RuntimeError):
    """Raised when the Edvibe WS API returns IsSuccess=false or transport fails."""


def client_time():
    now = datetime.datetime.now()
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{now.microsecond // 1000:03d}"


def _load_session(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_session(path, data):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


class EdvibeSession:
    """Authenticated WS RPC session to Edvibe.

    email/password used only when no valid session file exists; the token is
    persisted (never the password).
    """

    def __init__(self, email=None, password=None, domain="edvibe.com",
                 session_path=None, ws_base=WS_BASE):
        self.email = email
        self.password = password
        self.domain = domain
        self.session_path = session_path or SESSION_DEFAULT
        self.ws_base = ws_base
        self.token = None
        self.user = None
        self._ws = None
        self._http = requests.Session()
        self._http.headers["User-Agent"] = UA
        self._pacer = Pacer()

    # ------------------------------------------------------------------ auth
    def _mint_token(self):
        try:
            self._http.get(f"{BASE}/login", timeout=30)
        except requests.RequestException:
            pass  # token is client-generated; page fetch is best-effort
        self.token = str(uuid.uuid4())
        return self.token

    def _connect(self):
        if self._ws is not None:
            try:
                self._ws.ping()
                return self._ws
            except Exception:
                self._ws = None
        if not self.token:
            raise EdvibeError("No auth token; call authenticate() first")
        self._ws = websocket.create_connection(
            f"{self.ws_base}?token={self.token}",
            origin=f"https://{self.domain}",
            timeout=45,
        )
        return self._ws

    def rpc(self, controller, method, project, value=None):
        try:
            self._pacer.wait()
        except PaceLimitError as exc:
            raise EdvibeError(str(exc)) from exc
        rid = str(uuid.uuid4())
        msg = {"Controller": controller, "Method": method, "ProjectName": project,
               "RequestId": rid}
        if value is not None:
            msg["Value"] = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        payload = json.dumps(msg, ensure_ascii=False, separators=(",", ":"))
        for attempt in (1, 2):
            ws = self._connect()
            try:
                ws.send(payload)
                while True:
                    raw = ws.recv()
                    try:
                        resp = json.loads(raw)
                    except ValueError:
                        continue
                    if resp.get("RequestId") == rid:
                        if not resp.get("IsSuccess", True) and resp.get("Class") == "ConnectionController" and resp.get("Method") == "SetPage":
                            return resp
                        return resp
            except Exception as exc:  # transport error -> reconnect once
                self._ws = None
                if attempt == 2:
                    raise EdvibeError(f"WS transport failure on {controller}.{method}: {exc}") from exc
        raise EdvibeError("unreachable")

    def authenticate(self):
        """Load a persisted token or mint + WS-login. Fills self.user."""
        data = _load_session(self.session_path)
        if data.get("token") and data.get("user"):
            self.token = data["token"]
            self.user = data["user"]
            try:
                cu = self.get_current_user()
                self.user = cu if isinstance(cu, dict) else self.user
                return self.user
            except EdvibeError:
                self.token = None
                self._ws = None
        if not (self.email and self.password):
            raise EdvibeError("No session and no credentials: pass --email/--password "
                              "or set EDVIBE_EMAIL/EDVIBE_PASSWORD")
        self.token = self._mint_token()
        resp = self.rpc("AccountWsController", "GetAccountRoles", "Users",
                        {"Email": self.email, "Password": self.password,
                         "Domain": self.domain})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"GetAccountRoles failed: "
                              f"{resp.get('ErrorMessage')} ({resp.get('ErrorCode')})")
        roles = resp.get("Value") or []
        # choose a role the UI would pick: teacher-ish roles first
        ar, ur = self._pick_role(roles)
        payload = {"Email": self.email, "Password": self.password, "RememberMe": True,
                   "AuthToken": self.token, "InviteGroupCode": None,
                   "CurrentDomain": self.domain, "AccountRole": ar, "UserRole": ur,
                   "ClientTime": client_time(), "DeviceType": "desktop"}
        resp = self.rpc("AccountWsController", "Login", "Users", payload)
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"Login failed: {resp.get('ErrorMessage')} ({resp.get('ErrorCode')})")
        self.user = resp.get("Value")
        _save_session(self.session_path, {"token": self.token, "user": self.user})
        return self.user

    @staticmethod
    def _pick_role(roles):
        for ar, ur in ((1, 4), (0, 4), (2, 1), (3, 6)):
            if ar in roles:
                return ar, ur
        if roles:
            return roles[0], 4
        raise EdvibeError(f"No usable roles returned: {roles}")

    def logout(self):
        self._ws = None
        self.token = None
        self.user = None
        try:
            os.remove(self.session_path)
        except OSError:
            pass

    def get_current_user(self):
        resp = self.rpc("AccountWsController", "GetCurrentUser", "Users",
                        {"LoadOtherRolesInfo": True})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"GetCurrentUser failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    # ------------------------------------------------------------ materials
    def list_materials(self, take=100):
        """Personal/school materials: list of {'kind': 'folder'|'book', ...}."""
        out, skip = [], 0
        while True:
            resp = self.rpc("BookWsController", "GetPersonalBooks", "Books",
                            {"Page": {"Skip": skip, "Take": min(take, 24)},
                             "IsInversion": False})
            if not resp.get("IsSuccess"):
                raise EdvibeError(f"GetPersonalBooks failed: {resp.get('ErrorMessage')}")
            items = (resp.get("Value") or {}).get("Items", [])
            out.extend(items)
            if len(items) < 24 or len(out) >= take:
                break
            skip += 24
        return out

    def create_material(self, name, description="", language_id=1):
        """Create an empty personal/school material (Book). Returns its id."""
        u = self.user
        payload = {
            "Name": name, "LanguageId": language_id, "Description": description,
            "SchoolId": u.get("SchoolId"), "IsEnableGroupingLessons": False,
            "NoPhotoColor": 2, "UserId": u.get("Id"), "UserRole": u.get("Role"),
            "Number": 0, "Tags": {"BookId": 0, "TagAge": [], "TagLevel": [], "TagType": []},
            "IsCreateSchoolBook": True, "ClientTime": client_time(),
            "DeviceType": "desktop",
        }
        resp = self.rpc("BookWsController", "BookUpsert", "Books", payload)
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"BookUpsert failed: {resp.get('ErrorMessage')}")
        val = resp.get("Value")
        return val.get("Id") if isinstance(val, dict) else val

    def get_course_id(self, book_id):
        resp = self.rpc("CourseWsController", "GetBookCourses", "Books",
                        {"BookId": book_id, "TeacherId": self.user.get("Id"),
                         "Domain": self.domain})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"GetBookCourses failed: {resp.get('ErrorMessage')}")
        courses = resp.get("Value") or []
        if not courses:
            raise EdvibeError(f"Book {book_id} has no course; cannot add lessons")
        return courses[0].get("Id")

    def list_lessons(self, course_id):
        resp = self.rpc("LessonWsController", "GetCourseLessons", "Books",
                        {"CourseId": course_id})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"GetCourseLessons failed: {resp.get('ErrorMessage')}")
        lessons = resp.get("Value") or []
        return lessons if isinstance(lessons, list) else lessons.get("Items", [])

    def create_lesson(self, book_id, course_id, name):
        """Add a lesson (unit) inside a material. Returns (lesson_id, section_id)."""
        lessons = self.list_lessons(course_id)
        number = max([l.get("Number", 0) for l in lessons] + [-1]) + 1
        payload = {
            "Id": 0, "Number": number, "NoPhotoColor": 2, "Name": name,
            "CourseId": course_id, "BookId": book_id,
            "Tags": {"TagAge": [], "TagLevel": [], "TagType": [], "TagSkills": [],
                     "TagTime": [], "Vocabulary": "", "Grammar": "",
                     "Functions": "", "Other": ""},
            "DeviceType": "desktop", "ClientTime": client_time(),
        }
        resp = self.rpc("LessonWsController", "LessonUpsert", "Books", payload)
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"LessonUpsert failed: {resp.get('ErrorMessage')}")
        val = resp.get("Value")
        lesson_id = val.get("Id") if isinstance(val, dict) else val
        lesson = self.get_lesson(lesson_id)
        sections = lesson.get("Sections") or []
        if not sections:
            raise EdvibeError(f"Lesson {lesson_id} has no auto-created section")
        return lesson_id, sections[0].get("Id")

    def get_lesson(self, lesson_id):
        resp = self.rpc("LessonWsController", "GetLessonWithId", "Books",
                        {"LessonId": lesson_id})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"GetLessonWithId failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    def load_exercises(self, section_id, lesson_id):
        resp = self.rpc("GetExerciseWsController", "LoadExercises", "Exercises",
                        {"IsTeacher": True, "SectionId": section_id,
                         "LessonId": lesson_id, "LessonSection": 0})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"LoadExercises failed: {resp.get('ErrorMessage')}")
        val = resp.get("Value") or {}
        return val.get("Items", []) if isinstance(val, dict) else val

    def add_note(self, lesson_id, section_id, html_text, name="", number=None):
        """Add a Note (Type 22) content block with arbitrary HTML text."""
        if number is None:
            existing = self.load_exercises(section_id, lesson_id)
            number = max([e.get("Number", 0) for e in existing] + [-1]) + 1
        view = {
            "Id": 0, "Number": number, "IsShowExerciseNumber": False,
            "Name": name, "IsHideExercise": False, "IsHidePupil": False,
            "JsonData": json.dumps({"IsAutoCheck": False}),
            "Type": EX_NOTE, "Words": [], "PracticeNumber": 0,
            "inHomework": False, "IsLessonPage": False,
            "LessonSectionId": section_id, "LessonId": lesson_id,
            "Note": {"Id": 0, "Name": name, "Text": html_text},
        }
        return self.save_exercise(view, lesson_id, section_id)

    def add_section(self, lesson_id, name, sort_id):
        """Add a lesson section (UI: + section). Returns section dict
        {StageSectionId, LessonId, StageSectionName, SortId}."""
        resp = self.rpc("LessonSectionWsController", "AddStageSection", "Books",
                        {"LessonId": lesson_id, "SortId": sort_id,
                         "StageSectionName": name})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"AddStageSection failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    def edit_section(self, lesson_id, section_id, name, sort_id):
        """Rename / reorder an existing lesson section."""
        resp = self.rpc("LessonSectionWsController", "EditStageSection", "Books",
                        {"LessonId": lesson_id, "StageSectionId": section_id,
                         "StageSectionName": name, "SortId": sort_id})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"EditStageSection failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    def delete_section(self, section_id):
        resp = self.rpc("LessonSectionWsController", "DeleteStageSection", "Books",
                        {"StageSectionId": section_id})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"DeleteStageSection failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    def save_exercise(self, view, lesson_id, section_id):
        """Save any ExerciseView (from builders.build_*) into a lesson
        section. Returns the saved exercise model."""
        view.setdefault("LessonId", lesson_id)
        view.setdefault("LessonSectionId", section_id)
        payload = {"ClassId": None, "Domain": self.domain, "ExerciseView": view,
                   "AiUsed": False, "UsedNewConstructor": False,
                   "ClientTime": client_time(), "DeviceType": "desktop"}
        resp = self.rpc("SaveExerciseWsController", "SaveExercise", "Exercises", payload)
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"SaveExercise failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    # ------------------------------------------------- school / students
    def list_pupils(self, search=None, take=15, skip=0):
        """School student list (cabinet/school/students page).
        Returns {'Pupils': [...], 'Count', 'TotalCount'}."""
        payload = {"Skip": skip, "Take": take, "GetListType": 0,
                   "IsInversion": False, "SortClass": 1}
        if search:
            payload["SearchTerm"] = search
        resp = self.rpc("PupilService", "GetPupilsList", "School", payload)
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"GetPupilsList failed: {resp.get('ErrorMessage')}")
        return resp.get("Value") or {}

    def find_pupil(self, name):
        """Search a pupil by name; prefers exact (case-insensitive) match,
        then prefix, then first result. Returns the pupil dict or None."""
        val = self.list_pupils(search=name, take=50)
        pupils = list(val.get("Pupils") or [])
        if not pupils:
            return None
        nm = (name or "").strip().casefold()
        exact = [p for p in pupils if (p.get("FullName") or "").strip().casefold() == nm]
        prefix = [p for p in pupils if (p.get("FullName") or "").strip().casefold().startswith(nm)]
        return (exact or prefix or pupils)[0]

    def resolve_pupil(self, name_or_id):
        """Pupil by id (digits) or by name search."""
        s = str(name_or_id).strip()
        if s.isdigit():
            detail = self.get_pupil_detail(int(s))
            profile = detail.get("Profile") or {}
            return {"Id": profile.get("UserId") or int(s),
                    "FullName": profile.get("Name"), "Email": profile.get("Email")}
        return self.find_pupil(s)

    def get_pupil_detail(self, pupil_id):
        """Pupil profile + classes (student profile page)."""
        resp = self.rpc("PupilService", "GetPupilDetail", "School",
                        {"PupilId": pupil_id})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"GetPupilDetail failed: {resp.get('ErrorMessage')}")
        return resp.get("Value") or {}

    def get_group_detail(self, group_id):
        """Class detail incl. CurrentLessonId (classroom marker)."""
        resp = self.rpc("GroupService", "GetGroupDetail", "School",
                        {"Id": group_id})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"GetGroupDetail failed: {resp.get('ErrorMessage')}")
        return (resp.get("Value") or {}).get("Group") or {}

    def classroom(self, pupil_id, class_id=None):
        """Classroom summary for a pupil: profile, class, current lesson."""
        detail = self.get_pupil_detail(pupil_id)
        groups = detail.get("Groups") or []
        group = None
        if class_id:
            group = next((g for g in groups if g.get("GroupId") == class_id), None)
        if group is None and groups:
            group = groups[0]
        out = {"profile": detail.get("Profile") or {}, "groups": groups,
               "group": group, "group_detail": None, "current_lesson_id": None}
        if group:
            gd = self.get_group_detail(group.get("GroupId"))
            out["group_detail"] = gd
            out["current_lesson_id"] = gd.get("CurrentLessonId")
        return out

    # ------------------------------------------------- homework microservice
    # Homework = separate microservice (ProjectName "Homework"). A homework
    # object (HomeworkLessonId) belongs to a (lesson, pupil/class) pair; its
    # exercises are loaded via LoadHomeworkGroupExercises, appended via
    # AddExercisesToHomework, and are saved through SaveExercise with
    # ExerciseView.HomeworkLessonId + inHomework=true (no LessonSectionId).
    def homework_lessons(self, lesson_id, pupil_id, class_id):
        """Pupil's homework overview. Items carry Id = HomeworkLessonId,
        LessonId (the lesson), ExercisesCount, ..."""
        resp = self.rpc("HomeworkWsController", "GetHomeworkLessons", "Homework",
                        {"LessonId": lesson_id, "PupilId": pupil_id,
                         "ClassId": class_id, "UserId": self.user.get("Id")})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"GetHomeworkLessons failed: {resp.get('ErrorMessage')}")
        val = resp.get("Value") or {}
        return val.get("Items", []) if isinstance(val, dict) else val

    def find_homework_lesson(self, lesson_id, pupil_id, class_id):
        """The homework object for (lesson, pupil) or None."""
        for it in self.homework_lessons(lesson_id, pupil_id, class_id):
            if it.get("LessonId") == lesson_id:
                return it
        return None

    def load_homework_exercises(self, lesson_id, pupil_id, class_id,
                                homework_lesson_id):
        """Exercises visible in the pupil's homework (ordered by Number)."""
        resp = self.rpc("HomeworkWsController", "LoadHomeworkGroupExercises",
                        "Homework", {"LessonId": lesson_id, "PupilId": pupil_id,
                                     "ClassId": class_id,
                                     "HomeworkLessonId": homework_lesson_id})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"LoadHomeworkGroupExercises failed: {resp.get('ErrorMessage')}")
        val = resp.get("Value") or {}
        return val.get("Items", []) if isinstance(val, dict) else val

    def save_homework_exercise(self, view, homework_lesson_id, number=None):
        """Save an ExerciseView INTO a pupil's homework sheet (homework mode:
        HomeworkLessonId + inHomework=true, no LessonSectionId/LessonId)."""
        view = dict(view)
        view.pop("LessonId", None)
        view.pop("LessonSectionId", None)
        view["inHomework"] = True
        view["HomeworkLessonId"] = homework_lesson_id
        view["IsShowExerciseNumber"] = False
        if number is not None:
            view["Number"] = number
        payload = {"ClassId": None, "Domain": self.domain, "ExerciseView": view,
                   "AiUsed": False, "UsedNewConstructor": False,
                   "ClientTime": client_time(), "DeviceType": "desktop"}
        resp = self.rpc("SaveExerciseWsController", "SaveExercise", "Exercises",
                        payload)
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"SaveExercise (homework) failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    def add_exercises_to_homework(self, lesson_id, pupil_id, class_id,
                                  exercise_ids, group_name=""):
        """Attach exercises to the pupil's homework (ordered as given)."""
        resp = self.rpc("HomeworkWsController", "AddExercisesToHomework",
                        "Homework", {"LessonId": lesson_id, "PupilId": pupil_id,
                                     "ClassId": class_id,
                                     "HomeworkGroupName": group_name,
                                     "ExercisesId": [int(i) for i in exercise_ids]})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"AddExercisesToHomework failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    def remove_exercises_from_homework(self, lesson_id, pupil_id, class_id,
                                       homework_lesson_id, exercise_ids):
        """Detach exercises from the pupil's homework sheet."""
        resp = self.rpc("HomeworkWsController", "RemoveExercisesFromHomework",
                        "Homework", {"LessonId": lesson_id, "ClassId": class_id,
                                     "HomeworkLessonId": homework_lesson_id,
                                     "ExercisesId": [int(i) for i in exercise_ids],
                                     "PupilId": pupil_id})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"RemoveExercisesFromHomework failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    def update_homework_count(self, homework_lesson_id):
        """Server-side recompute of the homework exercise count."""
        resp = self.rpc("HomeworkWsController", "UpdateHomeworkGroupExercisesCount",
                        "Homework", {"HomeworkLessonId": homework_lesson_id})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"UpdateHomeworkGroupExercisesCount failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    def mark_homework_updates(self, homework_lesson_id, pupil_id):
        """Flag the homework as updated (pupil sees the NEW badge)."""
        resp = self.rpc("HomeworkWsController", "MarkHomeworkLessonHasUpdates",
                        "Homework", {"HomeworkLessonId": homework_lesson_id,
                                     "PupilId": pupil_id,
                                     "TeacherId": self.user.get("Id")})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"MarkHomeworkLessonHasUpdates failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    # -- unit pin / homework provisioning ---------------------------------
    def select_lesson(self, lesson_id, class_id):
        """Pin a lesson as the class's current unit — the classroom's
        "Pin this unit to use in class" (SelectLesson). On FIRST select the
        server provisions the pupil material AND the homework sheet for the
        class's pupils, so this is step 1 of giving homework for any lesson
        (including personal-material / serial lessons). Idempotent: repeat
        selects return IsFirstSelect=False and change nothing."""
        resp = self.rpc("LessonWsController", "SelectLesson", "MainWs",
                        {"LessonId": lesson_id, "ClassId": class_id,
                         "DeviceType": "desktop", "ClientTime": client_time()})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"SelectLesson failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    def link_lesson(self, lesson_id, class_id):
        """Attach a lesson to the class statistics (fires together with the
        unit pin in the UI)."""
        resp = self.rpc("ClassStatisticService", "LinkLesson", "Statistic",
                        {"ClassId": class_id, "LessonId": lesson_id})
        if not resp.get("IsSuccess"):
            raise EdvibeError(f"LinkLesson failed: {resp.get('ErrorMessage')}")
        return resp.get("Value")

    def default_section_id(self, lesson_id):
        """The lesson's main (non-homework) section id."""
        lesson = self.get_lesson(lesson_id) or {}
        for sec in (lesson.get("Sections") or []):
            if not sec.get("IsHomework"):
                return sec.get("Id")
        return None


    def import_html(self, html_file, material_name=None, lesson_name="Lesson 1",
                    description=""):
        """Full pipeline: HTML handout -> material (Book) + lesson + Note blocks.

        Returns dict with material_id, course_id, lesson_id, section_id,
        notes (list of exercise ids).
        """
        from .importer import html_to_sections
        import html as _html
        title, sections = html_to_sections(html_file)
        name = material_name or title or f"Imported {html_file}"
        book_id = self.create_material(name, description=description)
        course_id = self.get_course_id(book_id)
        lesson_id, section_id = self.create_lesson(book_id, course_id, lesson_name)
        notes = []
        for sec in sections:
            if sec["title"]:
                note_html = (f"<h3>{_html.escape(sec['title'])}</h3>\n" + sec["html"])
            else:
                note_html = sec["html"]
            saved = self.add_note(lesson_id, section_id, note_html)
            notes.append(saved)
        return {"material_id": book_id, "course_id": course_id,
                "lesson_id": lesson_id, "section_id": section_id, "notes": notes}
