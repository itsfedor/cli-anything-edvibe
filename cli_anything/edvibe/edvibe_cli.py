"""cli-anything-edvibe — Click CLI for edvibe.com materials.

One-shot subcommands plus a REPL when invoked without a subcommand; --json for
machine-readable output. Credentials: --email/--password or EDVIBE_EMAIL /
EDVIBE_PASSWORD env vars, or run `login` for an interactive first-time setup
(password never stored; session token cached in ~/.edvibe/session.json).
"""
import getpass
import json
import os
import shlex
import sys
import time

import click

from .core.client import SESSION_DEFAULT, EdvibeError, EdvibeSession

CONTEXT_SETTINGS = {"help_option_names": ["-h", "--help"]}


def _creds(ctx):
    cfg = ctx.obj or {}
    email = cfg.get("email") or os.environ.get("EDVIBE_EMAIL")
    password = cfg.get("password") or os.environ.get("EDVIBE_PASSWORD")
    return email, password


def get_session(ctx, login=True):
    cfg = ctx.obj or {}
    email, password = _creds(ctx)
    sess = EdvibeSession(email=email, password=password,
                         session_path=cfg.get("session"))
    if login:
        sess.authenticate()
    return sess


def emit(ctx, data, human=None):
    if (ctx.obj or {}).get("json"):
        click.echo(json.dumps(data, ensure_ascii=False, indent=1))
    else:
        click.echo(human(data) if human else json.dumps(data, ensure_ascii=False))


# ---------------------------------------------------------------------- CLI
@click.group(invoke_without_command=True, context_settings=CONTEXT_SETTINGS)
@click.option("--email", envvar="EDVIBE_EMAIL", help="Edvibe login email")
@click.option("--password", envvar="EDVIBE_PASSWORD", help="Edvibe password")
@click.option("--session", default=None, help="Session cache path (default ~/.edvibe/session.json)")
@click.option("--json", "as_json", is_flag=True, help="Machine-readable JSON output")
@click.pass_context
def cli(ctx, email, password, session, as_json):
    """CLI-Anything harness for edvibe.com (materials & lessons)."""
    ctx.ensure_object(dict)
    ctx.obj["email"] = email
    ctx.obj["password"] = password
    ctx.obj["session"] = session
    ctx.obj["json"] = as_json
    if ctx.invoked_subcommand is None:
        ctx.invoke(repl)


# ---------------------------------------------------------------- status
@cli.command("whoami")
@click.pass_context
def whoami(ctx):
    """Show the authenticated Edvibe user."""
    sess = get_session(ctx)
    u = sess.user or sess.get_current_user()
    emit(ctx, {"id": u.get("Id"), "name": u.get("FullName"), "email": u.get("Email"),
               "school_id": u.get("SchoolId"), "role": u.get("Role")},
         human=lambda d: f"{d['name']} <{d['email']}> id={d['id']} school={d['school_id']} role={d['role']}")


@cli.command("logout")
@click.pass_context
def logout(ctx):
    """Forget the cached session token."""
    sess = get_session(ctx, login=False)
    sess.logout()
    emit(ctx, {"ok": True}, human=lambda d: "Logged out.")


@cli.command("login")
@click.option("--check", "check_only", is_flag=True, help="Just verify the cached session")
@click.pass_context
def login(ctx, check_only):
    """Log in to Edvibe and cache the session. Run this YOURSELF in your own
    terminal: the password is never stored and never shown to an agent."""
    sess = get_session(ctx, login=False)
    email, password = _creds(ctx)
    if check_only:
        try:
            u = sess.authenticate()
        except EdvibeError as exc:
            emit(ctx, {"ok": False, "error": str(exc)},
                 human=lambda d: f"NOT logged in: {d['error']}")
            raise SystemExit(1)
        emit(ctx, {"ok": True, "id": u.get("Id"), "name": u.get("FullName")},
             human=lambda d: f"Session OK: {d['name']} (id {d['id']})")
        return
    if not (email or password):
        try:
            u = sess.authenticate()
            emit(ctx, {"ok": True, "already": True, "name": u.get("FullName")},
                 human=lambda d: f"Already logged in as {d['name']}. "
                                 f"Run `logout` first to switch accounts.")
            return
        except EdvibeError:
            pass
    else:
        sess.logout()  # explicit creds: force a fresh login
    if not email:
        email = click.prompt("Edvibe email")
    if not password:
        password = getpass.getpass("Edvibe password (hidden, not stored): ")
    sess.email, sess.password = email.strip(), password
    try:
        u = sess.authenticate()
    except EdvibeError as exc:
        emit(ctx, {"ok": False, "error": str(exc)},
             human=lambda d: f"Login failed: {d['error']}")
        raise SystemExit(1)
    emit(ctx, {"ok": True, "id": u.get("Id"), "name": u.get("FullName")},
         human=lambda d: f"Logged in as {d['name']} (id {d['id']}). "
                         f"Session cached in {SESSION_DEFAULT}.")


@cli.command("doctor")
@click.pass_context
def doctor(ctx):
    """Check the local setup: python, dependencies, session, live connection."""
    checks = []

    def add(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": str(detail)})

    add("python", sys.version_info >= (3, 9), sys.version.split()[0])
    for mod in ("requests", "websocket", "click"):
        try:
            __import__(mod)
            add(f"dep:{mod}", True, "ok")
        except Exception as exc:
            add(f"dep:{mod}", False, str(exc))
    path = (ctx.obj or {}).get("session") or SESSION_DEFAULT
    add("session-file", os.path.exists(path), path)
    sess = get_session(ctx, login=False)
    try:
        u = sess.authenticate()
        add("login", True, f"{u.get('FullName')} (id {u.get('Id')})")
    except EdvibeError as exc:
        add("login", False, str(exc))
    ok = all(c["ok"] for c in checks)

    def _fmt(d):
        lines = [f"[{'OK' if c['ok'] else 'FAIL'}] {c['check']}: {c['detail']}"
                 for c in d["checks"]]
        if not d["ok"]:
            lines.append("Fix: run `cli-anything-edvibe login` in your own terminal.")
        return "\n".join(lines)

    emit(ctx, {"ok": ok, "checks": checks}, human=_fmt)
    if not ok:
        raise SystemExit(1)


# -------------------------------------------------------------- materials
@cli.group("materials")
@click.pass_context
def materials(ctx):
    """Material (Book) operations."""


@materials.command("list")
@click.option("--take", default=100, show_default=True)
@click.pass_context
def materials_list(ctx, take):
    """List personal materials (folders & books)."""
    sess = get_session(ctx)
    items = sess.list_materials(take=take)
    rows = []
    for it in items:
        if "Folder" in it:
            f = it["Folder"]
            rows.append({"kind": "folder", "id": f.get("Id"), "name": f.get("Name"),
                         "count": f.get("CountMaterials")})
        else:
            b = it["Book"]
            rows.append({"kind": "book", "id": b.get("Id"), "name": b.get("Name"),
                         "school": b.get("IsSchoolBook"), "own": b.get("IsMySelfBook")})
    emit(ctx, {"total": len(rows), "items": rows},
         human=lambda d: "\n".join(
             f"[{r['kind']:6}] {r['id']:>9}  {r['name']}"
             + (f"  ({r['count']} materials)" if r["kind"] == "folder" else "")
             for r in d["items"]))


@materials.command("create")
@click.argument("name")
@click.option("--description", default="")
@click.option("--language", default=1, type=int, help="LanguageId (1 = English)")
@click.pass_context
def materials_create(ctx, name, description, language):
    """Create a new material (Book). Never deletes anything."""
    sess = get_session(ctx)
    book_id = sess.create_material(name, description=description, language_id=language)
    emit(ctx, {"material_id": book_id, "name": name},
         human=lambda d: f"Created material #{d['material_id']}: {d['name']}")


# ----------------------------------------------------------------- lesson
@cli.group("lesson")
@click.pass_context
def lesson(ctx):
    """Lesson (unit inside a material) operations."""


@lesson.command("add")
@click.option("--material", "material_id", required=True, type=int)
@click.option("--name", required=True)
@click.pass_context
def lesson_add(ctx, material_id, name):
    """Add a lesson (unit) to a material."""
    sess = get_session(ctx)
    course_id = sess.get_course_id(material_id)
    lesson_id, section_id = sess.create_lesson(material_id, course_id, name)
    emit(ctx, {"material_id": material_id, "course_id": course_id,
               "lesson_id": lesson_id, "section_id": section_id, "name": name},
         human=lambda d: f"Added lesson #{d['lesson_id']} '{d['name']}' "
                         f"(section #{d['section_id']}) to material #{d['material_id']}")


@lesson.command("show")
@click.option("--lesson", "lesson_id", required=True, type=int)
@click.pass_context
def lesson_show(ctx, lesson_id):
    """Show a lesson with its sections and exercises."""
    sess = get_session(ctx)
    les = sess.get_lesson(lesson_id)
    sections = list(les.get("Sections") or [])
    hw = les.get("HomeworkSection")
    if isinstance(hw, dict) and hw.get("Id"):
        sections.append(hw)
    out = {"lesson_id": lesson_id, "name": les.get("Name"), "number": les.get("Number"),
           "sections": []}
    for sec in sections:
        exercises = sess.load_exercises(sec.get("Id"), lesson_id)
        out["sections"].append({"section_id": sec.get("Id"), "name": sec.get("Name"),
                                "exercises": [{"id": e.get("Id"), "type": e.get("Type"),
                                               "name": (e.get("Name") or "")[:60]}
                                              for e in exercises]})
    emit(ctx, out, human=lambda d: _fmt_lesson(d))


@lesson.command("build")
@click.option("--file", "spec_file", required=True, type=click.Path(exists=True),
              help="Lesson spec JSON (see teacher-kit references/lesson-spec.md)")
@click.option("--lesson", "lesson_id", type=int, default=None,
              help="Append into an existing lesson id")
@click.option("--material", "material_id", type=int, default=None,
              help="Create a new lesson inside this material (Book) id")
@click.option("--name", default=None, help="Lesson name (for a new lesson)")
@click.option("--description", default=None, help="Lesson description")
@click.option("--dry-run", is_flag=True, help="Validate and show the plan only")
@click.pass_context
def lesson_build(ctx, spec_file, lesson_id, material_id, name, description, dry_run):
    """Build a full lesson (sections + exercises) from a JSON spec. Additive only:
    never edits or deletes anything that already exists."""
    from .core import lesson_spec
    with open(spec_file, "r", encoding="utf-8") as f:
        spec = json.load(f)
    if dry_run:
        try:
            res = lesson_spec.plan(spec)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        emit(ctx, res, human=_fmt_plan)
        return
    if not lesson_id and not material_id:
        raise click.UsageError("pass --material (new lesson) or --lesson (existing)")
    sess = get_session(ctx)
    if name is None:
        name = spec.get("lesson_name")
    if description is None:
        description = spec.get("description")
    try:
        res = lesson_spec.build_lesson(sess, spec, material_id=material_id,
                                       lesson_id=lesson_id, name=name,
                                       description=description)
    except ValueError as exc:
        raise click.ClickException(str(exc))
    emit(ctx, res, human=_fmt_build)


def _fmt_plan(d):
    lines = [f"PLAN: {d.get('lesson_name') or '(existing lesson)'} — "
             f"{d['total_exercises']} exercises / {len(d['sections'])} sections"]
    for s in d["sections"]:
        lines.append(f"  {s['name']} ({len(s['exercises'])}):")
        for e in s["exercises"]:
            lines.append(f"    - {e['type']}: {(e['name'] or '')[:60]}")
    return "\n".join(lines)


def _fmt_build(d):
    total = sum(len(s["exercise_ids"]) for s in d["sections"])
    lines = [f"Lesson #{d['lesson_id']}" + (" (new)" if d["created"] else ""),
             f"Built {total} exercises in {len(d['sections'])} sections:"]
    for s in d["sections"]:
        lines.append(f"  '{s['name']}' #{s['section_id']} — "
                     f"{len(s['exercise_ids'])} exercises")
    if d.get("description_error"):
        lines.append(f"  description update failed: {d['description_error']}")
    return "\n".join(lines)


def _fmt_lesson(d):
    lines = [f"Lesson #{d['lesson_id']}: {d['name']}"]
    for sec in d["sections"]:
        lines.append(f"  Section #{sec['section_id']} '{sec['name']}' "
                     f"({len(sec['exercises'])} exercises)")
        for e in sec["exercises"]:
            lines.append(f"     - #{e['id']} type={e['type']} {e['name']}")
    return "\n".join(lines)


# ---------------------------------------------------------------- content
@cli.group("content")
@click.pass_context
def content(ctx):
    """Exercise/content operations."""


@content.command("add-note")
@click.option("--lesson", "lesson_id", required=True, type=int)
@click.option("--section", "section_id", required=True, type=int)
@click.option("--text", default=None, help="Raw HTML for the note")
@click.option("--file", "html_file", default=None, help="HTML file (whole content)")
@click.option("--name", default="", help="Optional exercise title")
@click.pass_context
def content_add_note(ctx, lesson_id, section_id, text, html_file, name):
    """Add a Note content block (Type 22) with HTML to a lesson section."""
    if text is None and html_file is None:
        raise click.UsageError("Provide --text or --file")
    if html_file:
        with open(html_file, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    sess = get_session(ctx)
    saved = sess.add_note(lesson_id, section_id, text, name=name)
    eid = saved.get("Id") if isinstance(saved, dict) else saved
    emit(ctx, {"exercise_id": eid, "lesson_id": lesson_id, "section_id": section_id},
         human=lambda d: f"Added note exercise #{d['exercise_id']}")


@content.command("show")
@click.option("--lesson", "lesson_id", required=True, type=int)
@click.option("--section", "section_id", required=True, type=int)
@click.pass_context
def content_show(ctx, lesson_id, section_id):
    """List exercises in a lesson section."""
    sess = get_session(ctx)
    items = sess.load_exercises(section_id, lesson_id)
    emit(ctx, {"exercises": items},
         human=lambda d: "\n".join(
             f"#{e.get('Id')} type={e.get('Type')} "
             f"name={(e.get('Name') or '')[:70]}" for e in d["exercises"]))


# --------------------------------------------------------------- students
@cli.group("students")
@click.pass_context
def students(ctx):
    """School students (pupils) operations."""


@students.command("list")
@click.option("--search", default=None, help="Search term (student name)")
@click.option("--take", default=25, show_default=True)
@click.pass_context
def students_list(ctx, search, take):
    """List school students (cabinet/school/students)."""
    sess = get_session(ctx)
    val = sess.list_pupils(search=search, take=take)
    pupils = val.get("Pupils") or []
    rows = [{"id": p.get("Id"), "name": p.get("FullName"),
             "email": p.get("Email"), "active": p.get("IsActive")}
            for p in pupils]
    emit(ctx, {"total": val.get("TotalCount"), "count": val.get("Count"),
               "shown": len(rows), "items": rows},
         human=lambda d: "\n".join(
             f"{r['id']:>9}  {r['name']}  <{r['email']}>" for r in d["items"])
         + f"\n({d['shown']} shown / {d.get('total')} total)")


@students.command("show")
@click.argument("pupil_id", type=int)
@click.pass_context
def students_show(ctx, pupil_id):
    """Show a student profile and their classes."""
    sess = get_session(ctx)
    d = sess.get_pupil_detail(pupil_id)
    profile = d.get("Profile") or {}
    groups = d.get("Groups") or []
    out = {"pupil_id": pupil_id, "name": profile.get("Name"),
           "email": profile.get("Email"), "timezone": profile.get("Timezone"),
           "groups": [{"class_id": g.get("GroupId"), "name": g.get("Name"),
                       "lessons_count": g.get("LessonsCount")}
                      for g in groups]}
    emit(ctx, out, human=lambda d: (
        f"{d['name']} <{d['email']}> tz={d['timezone']}\n"
        + "\n".join(f"  class #{g['class_id']} '{g['name']}' "
                    f"({g['lessons_count']} lessons)" for g in d["groups"])))


# -------------------------------------------------------------- classroom
@cli.group("classroom")
@click.pass_context
def classroom(ctx):
    """Student classroom (current lesson, homework)."""


@classroom.command("show")
@click.option("--student", required=True,
              help="Student name (as on Edvibe) or pupil id")
@click.option("--class-id", "class_id", type=int, default=None,
              help="Class id (default: the student's first class)")
@click.pass_context
def classroom_show(ctx, student, class_id):
    """Open a student's classroom: current lesson + homework overview."""
    sess = get_session(ctx)
    pupil = sess.resolve_pupil(student)
    if not pupil:
        raise click.ClickException(f"student not found: {student}")
    pid = pupil.get("Id")
    c = sess.classroom(pid, class_id=class_id)
    group = c.get("group") or {}
    gid = group.get("GroupId")
    lesson_id = c.get("current_lesson_id")
    lesson = sess.get_lesson(lesson_id) if lesson_id else None
    hw_item = None
    hw_items = []
    if lesson_id and gid:
        hw_item = sess.find_homework_lesson(lesson_id, pid, gid)
        if hw_item:
            hw_items = sess.load_homework_exercises(lesson_id, pid, gid,
                                                    hw_item.get("Id"))
    hw_section = (lesson or {}).get("HomeworkSection") or {}
    out = {"pupil": {"id": pid, "name": pupil.get("FullName")},
           "class": {"id": gid, "name": group.get("Name")},
           "current_lesson": {"id": lesson_id,
                              "name": (lesson or {}).get("Name")},
           "homework_section_id": hw_section.get("Id"),
           "homework": {"id": (hw_item or {}).get("Id"),
                        "exercises": len(hw_items)},
           "homework_items": [{"number": e.get("Number"), "id": e.get("Id"),
                               "type": e.get("Type"),
                               "name": (e.get("Name") or "")[:70]}
                              for e in hw_items]}

    def _fmt(d):
        lines = [f"{d['pupil']['name']} (id {d['pupil']['id']})",
                 f"class #{d['class']['id']} '{d['class']['name']}'",
                 f"current lesson #{d['current_lesson']['id']} "
                 f"'{d['current_lesson']['name']}'",
                 f"homework #{d['homework']['id']} — "
                 f"{d['homework']['exercises']} exercises "
                 f"(section id {d['homework_section_id']})"]
        for e in d["homework_items"]:
            lines.append(f"   {e['number']:>2}. #{e['id']} type={e['type']} {e['name']}")
        return "\n".join(lines)

    emit(ctx, out, human=_fmt)


# --------------------------------------------------------------- homework
@cli.group("homework")
@click.pass_context
def homework(ctx):
    """Homework microservice (per-student homework sheets)."""


@homework.command("show")
@click.option("--lesson", "lesson_id", required=True, type=int)
@click.option("--pupil", "pupil_id", required=True, type=int)
@click.option("--class-id", "class_id", required=True, type=int)
@click.option("--homework-lesson", "hw_id", type=int, default=None,
              help="Homework object id (default: auto-find for the lesson)")
@click.pass_context
def homework_show(ctx, lesson_id, pupil_id, class_id, hw_id):
    """List the exercises in a student's homework sheet."""
    sess = get_session(ctx)
    if not hw_id:
        item = sess.find_homework_lesson(lesson_id, pupil_id, class_id)
        if not item:
            raise click.ClickException(
                "no homework found for this lesson/pupil; pass --homework-lesson")
        hw_id = item.get("Id")
    items = sess.load_homework_exercises(lesson_id, pupil_id, class_id, hw_id)
    rows = [{"id": i.get("Id"), "number": i.get("Number"), "type": i.get("Type"),
             "name": (i.get("Name") or "")[:70]} for i in items]
    emit(ctx, {"homework_lesson_id": hw_id, "count": len(rows), "items": rows},
         human=lambda d: (
             f"Homework #{d['homework_lesson_id']} — {d['count']} exercises\n"
             + "\n".join(f"  {r['number']:>2}. #{r['id']} type={r['type']} {r['name']}"
                         for r in d["items"])))


@homework.command("add")
@click.option("--file", "spec_file", required=True, type=click.Path(exists=True),
              help="Homework spec JSON (see core/homework_spec.py)")
@click.option("--dry-run", is_flag=True, help="Validate and show the plan only")
@click.pass_context
def homework_add(ctx, spec_file, dry_run):
    """Append exercises from a JSON spec to a student's homework sheet.
    Additive only: existing homework exercises are never touched."""
    from .core import homework_spec
    with open(spec_file, "r", encoding="utf-8") as f:
        spec = json.load(f)
    if dry_run:
        try:
            res = homework_spec.plan(spec)
        except ValueError as exc:
            raise click.ClickException(str(exc))
        emit(ctx, res, human=_fmt_hw_plan)
        return
    sess = get_session(ctx)
    log_lines = []
    try:
        res = homework_spec.build_homework(sess, spec,
                                           log=lambda s: log_lines.append(s))
    except ValueError as exc:
        raise click.ClickException(str(exc))
    res["log"] = log_lines

    def _fmt(d):
        lines = [f"Homework #{d['homework_lesson_id']} — added "
                 f"{len(d['saved'])} exercises ({d['before_count']} -> "
                 f"{d['after_count']})",
                 *d.get("log", []),
                 f"verified: {'OK' if d['verified'] else 'MISSING ' + str(d['missing_ids'])}"]
        return "\n".join(lines)

    emit(ctx, res, human=_fmt)


def _collect_exercise_ids(sess, lesson_id, section_id, exercises_csv):
    """Section exercises (sorted by Number) + extra ids, de-duplicated."""
    ids = []
    if section_id:
        sec_items = sess.load_exercises(section_id, lesson_id)
        ids += [e.get("Id") for e in
                sorted(sec_items, key=lambda e: e.get("Number", 0))]
    if exercises_csv:
        ids += [int(x) for x in exercises_csv.replace(" ", "").split(",") if x]
    # keep order, drop duplicates
    seen = set()
    return [i for i in ids if i and not (i in seen or seen.add(i))]


def _attach_core(sess, lesson_id, pupil_id, class_id, ids, dry_run):
    """Push already-collected exercise ids into the pupil's homework sheet,
    in the given order, then verify. Sheet MUST already exist (the classroom
    unit pin provisions it) — with no sheet the RPC silently no-ops."""
    existing = sess.find_homework_lesson(lesson_id, pupil_id, class_id)
    hw_id = (existing or {}).get("Id")
    already = set()
    if hw_id:
        already = {e.get("Id") for e in
                   sess.load_homework_exercises(lesson_id, pupil_id, class_id, hw_id)}
    new_ids = [i for i in ids if i not in already]

    if dry_run:
        return {"lesson_id": lesson_id, "pupil_id": pupil_id, "class_id": class_id,
                "homework_lesson_id": hw_id, "to_attach": new_ids,
                "already_there": sorted(already), "mode": "dry-run"}

    if new_ids:
        value = sess.add_exercises_to_homework(lesson_id, pupil_id, class_id, new_ids)
        if isinstance(value, dict):
            hw_id = value.get("HomeworkLessonId") or hw_id
        elif isinstance(value, int):
            hw_id = value or hw_id
        if not hw_id:
            item = sess.find_homework_lesson(lesson_id, pupil_id, class_id)
            hw_id = (item or {}).get("Id")
        sess.update_homework_count(hw_id)
        sess.mark_homework_updates(hw_id, pupil_id)

    after = sess.load_homework_exercises(lesson_id, pupil_id, class_id, hw_id)
    have = {e.get("Id") for e in after}
    missing = [i for i in ids if i not in have]
    return {"lesson_id": lesson_id, "pupil_id": pupil_id, "class_id": class_id,
            "homework_lesson_id": hw_id, "attached": new_ids,
            "already_there": sorted(already), "after_count": len(after),
            "verified": not missing, "missing_ids": missing,
            "items": [{"id": e.get("Id"), "number": e.get("Number"),
                       "type": e.get("Type"), "name": (e.get("Name") or "")[:70]}
                      for e in after]}


def _fmt_attach(d):
    lines = [f"Homework #{d['homework_lesson_id']} — attached "
             f"{len(d['attached'])} exercises (now {d['after_count']} total)",
             *[f"  + #{i}" for i in d["attached"]],
             f"verified: {'OK' if d['verified'] else 'MISSING ' + str(d['missing_ids'])}"]
    return "\n".join(lines)


@homework.command("attach")
@click.option("--lesson", "lesson_id", required=True, type=int,
              help="Lesson id the homework belongs to")
@click.option("--pupil", "pupil_id", required=True, type=int, help="Pupil id")
@click.option("--class-id", "class_id", required=True, type=int, help="Class (group) id")
@click.option("--section", "section_id", type=int, default=None,
              help="Attach every exercise of this lesson section (in order)")
@click.option("--exercises", "exercises_csv", default=None,
              help="Comma-separated exercise ids (instead of --section)")
@click.option("--dry-run", is_flag=True,
              help="Show what would be attached; no writes (auth still needed)")
@click.pass_context
def homework_attach(ctx, lesson_id, pupil_id, class_id, section_id,
                    exercises_csv, dry_run):
    """Attach EXISTING lesson exercises to a pupil's homework sheet.

    Unlike `homework add` (which SAVES new exercises), this pushes exercises
    that already live in the lesson section into the pupil's homework —
    the same flow as the editor's "Add to homework". The homework sheet must
    already exist: the classroom's unit pin creates it (see `homework give`);
    without a sheet the server silently no-ops."""
    sess = get_session(ctx)
    ids = _collect_exercise_ids(sess, lesson_id, section_id, exercises_csv)
    if not ids:
        raise click.ClickException("pass --section and/or --exercises")

    if dry_run:
        res = _attach_core(sess, lesson_id, pupil_id, class_id, ids, dry_run=True)
        emit(ctx, res, human=lambda d: (
            f"PLAN: attach {len(d['to_attach'])} exercises to homework "
            f"#{d['homework_lesson_id']} (lesson #{d['lesson_id']}, "
            f"pupil #{d['pupil_id']}, class #{d['class_id']})\n"
            + "\n".join(f"  - #{i}" for i in d["to_attach"])
            + (f"\n  ({len(d['already_there'])} already attached)" if d["already_there"] else "")))
        return

    res = _attach_core(sess, lesson_id, pupil_id, class_id, ids, dry_run=False)
    emit(ctx, res, human=_fmt_attach)


@homework.command("give")
@click.option("--lesson", "lesson_id", required=True, type=int,
              help="Lesson to give homework for (any book, incl. personal/serial)")
@click.option("--class-id", "class_id", required=True, type=int, help="Class (group) id")
@click.option("--pupil", "pupil_id", required=True, type=int,
              help="Pupil id whose homework sheet receives the exercises")
@click.option("--section", "section_id", type=int, default=None,
              help="Lesson section (default: the lesson's main section)")
@click.option("--exercises", "exercises_csv", default=None,
              help="Comma-separated exercise ids (instead of --section)")
@click.option("--dry-run", is_flag=True, help="Show the plan; no writes")
@click.pass_context
def homework_give(ctx, lesson_id, class_id, pupil_id, section_id, exercises_csv, dry_run):
    """Give homework for a lesson end-to-end (one command).

    Step 1 pins the lesson as the class's current unit (SelectLesson — the
    classroom's "Pin this unit to use in class"); the FIRST pin provisions
    the pupil material and the homework sheet server-side. Step 2 attaches
    the lesson's exercises to the pupil's homework in lesson order."""
    sess = get_session(ctx)
    if not section_id and not exercises_csv:
        section_id = sess.default_section_id(lesson_id)
    ids = _collect_exercise_ids(sess, lesson_id, section_id, exercises_csv)
    if not ids:
        raise click.ClickException("no exercises found — pass --section/--exercises")

    if dry_run:
        res = _attach_core(sess, lesson_id, pupil_id, class_id, ids, dry_run=True)
        res["would_pin"] = f"lesson #{lesson_id} -> class #{class_id}"
        emit(ctx, res, human=lambda d: (
            f"PLAN: pin lesson #{d['lesson_id']} to class #{d['class_id']} (SelectLesson), "
            f"then attach {len(d['to_attach'])} exercises to homework "
            f"#{d['homework_lesson_id']} (pupil #{d['pupil_id']})\n"
            + "\n".join(f"  - #{i}" for i in d["to_attach"])))
        return

    pin = sess.select_lesson(lesson_id, class_id)
    try:
        sess.link_lesson(lesson_id, class_id)
    except EdvibeError:
        pass
    # the sheet is provisioned by the first pin; give the server a beat
    hw = None
    for _ in range(10):
        hw = sess.find_homework_lesson(lesson_id, pupil_id, class_id)
        if hw:
            break
        time.sleep(1.5)
    if not hw:
        raise click.ClickException(
            f"pinned lesson #{lesson_id} but no homework sheet appeared for "
            f"pupil #{pupil_id} — open the class homework page once, then re-run")

    res = _attach_core(sess, lesson_id, pupil_id, class_id, ids, dry_run=False)
    res["pin"] = pin
    res["is_first_select"] = (pin or {}).get("IsFirstSelect") if isinstance(pin, dict) else None

    def _fmt(d):
        lines = [f"Pinned lesson #{d['lesson_id']} to class #{d['class_id']}"
                 f" (first select: {d['is_first_select']})",
                 _fmt_attach(d)]
        return "\n".join(lines)

    emit(ctx, res, human=_fmt)


def _fmt_hw_plan(d):
    lines = [f"PLAN: homework #{d['homework_lesson_id']} "
             f"(lesson #{d['lesson_id']}, pupil #{d['pupil_id']}, class #{d['class_id']}) "
             f"— append {d['total_exercises']} exercises:"]
    for e in d["exercises"]:
        lines.append(f"  - {e['type']}: {(e['name'] or '')[:60]}")
    return "\n".join(lines)


# --------------------------------------------------------------- import
@cli.command("import")
@click.option("--html", "html_file", required=True, type=click.Path(exists=True),
              help="Path to a structured HTML handout (h2 sections become notes)")
@click.option("--name", default=None, help="Material name (default: <title>)")
@click.option("--description", default="")
@click.option("--lesson", default="Lesson 1", help="Lesson name inside the material")
@click.pass_context
def import_html(ctx, html_file, name, description, lesson):
    """Import an HTML handout as a new Edvibe material with content notes."""
    sess = get_session(ctx)
    res = sess.import_html(html_file, material_name=name,
                           lesson_name=lesson, description=description)
    emit(ctx, res, human=lambda d: _fmt_import(d))


def _fmt_import(d):
    lines = [f"Material #{d['material_id']} (course #{d['course_id']})",
             f"Lesson #{d['lesson_id']} section #{d['section_id']}",
             f"Notes added: {len(d['notes'])}"]
    for n in d["notes"]:
        eid = n.get("Id") if isinstance(n, dict) else n
        lines.append(f"  - note exercise #{eid}")
    return "\n".join(lines)


# ------------------------------------------------------------------ REPL
@cli.command("repl", hidden=True)
@click.pass_context
def repl(ctx):
    """Interactive REPL (default when no subcommand is given)."""
    click.echo("cli-anything-edvibe REPL — type a command, 'help' or 'exit'.")
    while True:
        try:
            line = input("edvibe> ").strip()
        except (EOFError, KeyboardInterrupt):
            click.echo()
            break
        if not line:
            continue
        if line in ("exit", "quit"):
            break
        if line in ("help", "?"):
            click.echo("whoami | login | doctor | materials list | "
                       "materials create NAME | "
                       "lesson add --material ID --name N | "
                       "lesson build --file spec.json "
                       "[--material ID | --lesson ID] [--dry-run] | "
                       "lesson show --lesson ID | "
                       "content add-note --lesson ID --section ID --file F | "
                       "students list [--search NAME] | students show PUPIL_ID | "
                       "classroom show --student NAME | "
                       "homework show --lesson ID --pupil ID --class-id ID | "
                       "homework add --file spec.json [--dry-run] | "
                       "homework attach --lesson ID --pupil ID --class-id ID "
                       "[--section ID | --exercises 1,2,3] [--dry-run] | "
                       "homework give --lesson ID --class-id ID --pupil ID "
                       "[--section ID] [--dry-run] | "
                       "import --html FILE | logout | exit")
            continue
        try:
            args = shlex.split(line)
            cli.main(args=args, standalone_mode=False, obj=ctx.obj)
        except SystemExit:
            pass
        except (click.UsageError, EdvibeError, OSError) as exc:
            click.echo(f"error: {exc}", err=True)
        except Exception as exc:  # keep REPL alive
            click.echo(f"error: {type(exc).__name__}: {exc}", err=True)


def main():
    cli()


if __name__ == "__main__":
    sys.exit(main())
