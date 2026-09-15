"""Offline unit tests for homework_spec (validate / plan / normalize).

No network, no auth: exercises are built and inspected in memory only.
Live end-to-end flow (Save -> Add -> count update -> verify) is exercised by
the `homework add` CLI against a real pupil's sheet, not in this suite.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cli_anything.edvibe.core import homework_spec as HS  # noqa: E402
from cli_anything.edvibe.core import lesson_spec as LS  # noqa: E402


def base_spec():
    return {
        "lesson_id": 111, "pupil_id": 222, "class_id": 333,
        "homework_lesson_id": 444,
        "exercises": [
            {"type": "match", "instruction": "Match.",
             "pairs": [["a", "1"], ["b", "2"]]},
            {"type": "truefalse", "instruction": "TF.",
             "statements": [["t", True], ["f", False], ["ns", "notstated"]]},
        ],
    }


def test_validate_ok():
    assert HS.validate(base_spec()) == []


def test_validate_requires_ids():
    spec = base_spec()
    del spec["pupil_id"]
    errs = HS.validate(spec)
    assert any("pupil_id" in e for e in errs)


def test_validate_requires_exercises():
    spec = base_spec()
    spec["exercises"] = []
    assert any("non-empty" in e for e in HS.validate(spec))


def test_validate_unknown_type_and_missing_field():
    spec = base_spec()
    spec["exercises"] = [{"type": "nope"}, {"type": "video", "instruction": "x"}]
    errs = HS.validate(spec)
    assert any("unknown type" in e for e in errs)
    assert any("link" in e for e in errs)


def test_plan():
    plan = HS.plan(base_spec())
    assert plan["ok"] and plan["total_exercises"] == 2
    assert plan["exercises"][0]["type"] == "match"


def _norm(ex, etype, number):
    view = LS._build_one(ex, etype, number, None, None)
    return HS._normalize_for_homework(view, etype, 444, number)


def test_normalize_truefalse_encoding():
    spec = base_spec()
    view = _norm(spec["exercises"][1], "truefalse", 8)
    # frontend encoding: True -> "true", False -> "", NotStated -> "NotStated"
    assert view["QuestionAnswers"] == ["true", "", "NotStated"]
    assert view["TrueOrFalse"]["EnableNotStated"] is True
    jd = json.loads(view["JsonData"])
    assert jd["QuestionAnswers"] == view["QuestionAnswers"]
    assert jd["IsAutoCheck"] is False
    assert view["inHomework"] is True
    assert view["HomeworkLessonId"] == 444
    assert "LessonSectionId" not in view and "LessonId" not in view


def test_normalize_fillbox_shuffled_and_json():
    ex = {"type": "fillbox", "instruction": "Gaps.",
          "sentences": ["A [one] and a [two] and a [three]."]}
    view = _norm(ex, "fillbox", 6)
    pw = view["PasteWordByDrag"]
    assert pw["Text"].count("hide-id-exercise-item") == 3
    assert sorted(pw["SortIndexes"]) == [0, 1, 2]
    jd = json.loads(view["JsonData"])
    assert set(jd.keys()) == {"PasteWordByDrag", "IsAutoCheck"}
    assert jd["IsAutoCheck"] is False


def test_normalize_video_minimal_json():
    ex = {"type": "video", "instruction": "Watch.", "link": "https://youtu.be/x"}
    view = _norm(ex, "video", 7)
    jd = json.loads(view["JsonData"])
    assert list(jd.keys()) == ["Videos", "IsAutoCheck"]
    assert view["Videos"][0]["Link"] == "https://youtu.be/x"


def test_normalize_test_points_off():
    ex = {"type": "test", "instruction": "Choose.",
          "questions": [{"q": "Q", "options": ["a", "b"], "correct": [1]}]}
    view = _norm(ex, "test", 9)
    assert view["Test"]["IsEnabledPoints"] is False
    assert view["Test"]["Questions"][0]["QuestionAnswers"] == ["", "true"]


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
