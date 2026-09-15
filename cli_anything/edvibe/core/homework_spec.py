"""Append exercises to a pupil's homework (Edvibe homework microservice).

Spec v1 (JSON) — mirrors the lesson-spec exercise dicts:

    {
      "lesson_id": 12345678,           # the lesson the homework belongs to
      "pupil_id": 23456789,            # student (pupil) id
      "class_id": 34567890,            # class/group id
      "homework_lesson_id": 45678901,  # homework object id (GetHomeworkLessons)
      "exercises": [
        {"type": "match",  "instruction": "Match the words with their definitions.",
         "pairs": [["term", "definition"], ...]},
        {"type": "fillbox", "instruction": "...", "sentences": ["text [gap] text"]},
        {"type": "video",   "instruction": "...", "link": "https://youtube..."},
        {"type": "truefalse", "instruction": "...",
         "statements": [["text", true], ["text", false], ["text", "notstated"]]},
        {"type": "test", "instruction": "...",
         "questions": [{"q": "...", "options": ["a", "b", "c"], "correct": [1]}]},
        {"type": "voice", "instruction": "...", "html": "model answer", "seconds": 60}
      ]
    }

Execution (all verified live Sep 2026):
  1. Save each exercise with ExerciseView.HomeworkLessonId + inHomework=true
     (no LessonSectionId/LessonId), numbered after the current last exercise.
  2. HomeworkWsController.AddExercisesToHomework {LessonId, PupilId, ClassId,
     HomeworkGroupName:"", ExercisesId:[...]} — attaches them in order.
  3. UpdateHomeworkGroupExercisesCount {HomeworkLessonId} — server recompute.
  4. MarkHomeworkLessonHasUpdates {HomeworkLessonId, PupilId, TeacherId}.
  5. Re-load and verify every new id is present.

Field conventions mirror the live homework items: True/False answers
True->"true"/False->""/NotStated->"NotStated"; FillBox keeps
PasteWordByDrag in JsonData with shuffled SortIndexes; Video uses a minimal
JsonData; homework keeps IsAutoCheck=false on fillbox/video/truefalse.
Additive only — never edits or deletes existing exercises.
"""
import json

from . import builders as B
from . import lesson_spec as LS


def validate(spec):
    """Human-readable problems list ([] == valid). Offline, no auth."""
    errs = []
    if not isinstance(spec, dict):
        return ["spec must be a JSON object"]
    for key in ("lesson_id", "pupil_id", "class_id", "homework_lesson_id"):
        if not spec.get(key):
            errs.append(f"missing '{key}'")
    exs = spec.get("exercises")
    if not isinstance(exs, list) or not exs:
        return errs + ["'exercises' must be a non-empty list"]
    for j, ex in enumerate(exs):
        w = f"exercises[{j}]"
        if not isinstance(ex, dict):
            errs.append(f"{w}: must be an object")
            continue
        t = LS.norm_type(ex.get("type"))
        if t not in LS.VALID_TYPES:
            errs.append(f"{w}: unknown type '{ex.get('type')}' "
                        f"(valid: {', '.join(sorted(LS.VALID_TYPES))})")
            continue
        for field in LS.REQUIRED_FIELDS.get(t, []):
            if ex.get(field) in (None, "", []):
                errs.append(f"{w} ({t}): missing '{field}'")
    return errs


def plan(spec):
    """Dry-run summary; raises ValueError when invalid."""
    errs = validate(spec)
    if errs:
        raise ValueError("spec invalid: " + "; ".join(errs))
    out = {"ok": True, "lesson_id": spec["lesson_id"],
           "pupil_id": spec["pupil_id"], "class_id": spec["class_id"],
           "homework_lesson_id": spec["homework_lesson_id"],
           "exercises": [{"type": LS.norm_type(ex.get("type")),
                          "name": LS.label(ex)} for ex in spec["exercises"]]}
    out["total_exercises"] = len(out["exercises"])
    return out


# ------------------------------------------------------------- normalization
def _normalize_for_homework(view, etype, homework_lesson_id, number):
    """Mirror the live homework item conventions for a freshly built view."""
    view.pop("LessonId", None)
    view.pop("LessonSectionId", None)
    view["inHomework"] = True
    view["HomeworkLessonId"] = homework_lesson_id
    view["Number"] = number
    view["IsShowExerciseNumber"] = False
    if etype == "fillbox":
        pwbd = view.get("PasteWordByDrag") or {}
        idx = list(pwbd.get("SortIndexes") or [])
        B.random.shuffle(idx)
        pwbd["SortIndexes"] = idx
        view["PasteWordByDrag"] = pwbd
        view["JsonData"] = json.dumps({"PasteWordByDrag": pwbd,
                                       "IsAutoCheck": False}, ensure_ascii=False)
        view["IsAutoCheck"] = False
    elif etype == "video":
        view["JsonData"] = json.dumps({"Videos": view.get("Videos") or [],
                                       "IsAutoCheck": False}, ensure_ascii=False)
        view["IsAutoCheck"] = False
    elif etype == "truefalse":
        view["JsonData"] = json.dumps({
            "Questions": view.get("Questions"),
            "QuestionAnswers": view.get("QuestionAnswers"),
            "IsAutoCheck": False,
            "TrueOrFalse": view.get("TrueOrFalse")}, ensure_ascii=False)
        view["IsAutoCheck"] = False
    elif etype == "test":
        t = view.get("Test") or {}
        t["IsEnabledPoints"] = False
        view["Test"] = t
    return view


# --------------------------------------------------------------- executor
def build_homework(session, spec, log=None):
    """Append the spec's exercises to the pupil's homework. Returns a summary.

    Additive only: existing homework items are never touched. Numbers are
    assigned after the current last exercise.
    """
    errs = validate(spec)
    if errs:
        raise ValueError("spec invalid: " + "; ".join(errs))

    lesson_id = int(spec["lesson_id"])
    pupil_id = int(spec["pupil_id"])
    class_id = int(spec["class_id"])
    hw_id = int(spec["homework_lesson_id"])

    existing = session.load_homework_exercises(lesson_id, pupil_id, class_id, hw_id)
    counter = max([e.get("Number", 0) for e in existing] + [0]) + 1

    summary = {"homework_lesson_id": hw_id, "lesson_id": lesson_id,
               "pupil_id": pupil_id, "class_id": class_id,
               "before_count": len(existing), "saved": [], "verified": False}
    ids = []
    for j, ex in enumerate(spec["exercises"]):
        etype = LS.norm_type(ex.get("type"))
        try:
            view = LS._build_one(ex, etype, counter, None, None)
            view = _normalize_for_homework(view, etype, hw_id, counter)
            saved = session.save_homework_exercise(view, hw_id, number=counter)
        except Exception as exc:
            raise ValueError(
                f"exercise {j} ({etype}): {exc}") from exc
        eid = saved.get("Id") if isinstance(saved, dict) else saved
        ids.append(eid)
        summary["saved"].append({"id": eid, "type": etype,
                                 "number": counter, "name": LS.label(ex)})
        counter += 1
        if log:
            log(f"   + #{eid} {etype} n={counter - 1} "
                + (LS.label(ex) or "")[:60])

    session.add_exercises_to_homework(lesson_id, pupil_id, class_id, ids)
    if log:
        log(f"   attached {len(ids)} exercises to homework #{hw_id}")
    try:
        session.update_homework_count(hw_id)
    except Exception as exc:  # non-fatal, verified below regardless
        summary["count_update_error"] = str(exc)
    try:
        session.mark_homework_updates(hw_id, pupil_id)
    except Exception as exc:
        summary["mark_update_error"] = str(exc)

    # verify
    after = session.load_homework_exercises(lesson_id, pupil_id, class_id, hw_id)
    have = {e.get("Id") for e in after}
    missing = [i for i in ids if i not in have]
    summary["after_count"] = len(after)
    summary["verified"] = not missing
    summary["missing_ids"] = missing
    summary["items"] = [{"id": e.get("Id"), "number": e.get("Number"),
                         "type": e.get("Type"),
                         "name": (e.get("Name") or "")[:70]} for e in after]
    return summary
