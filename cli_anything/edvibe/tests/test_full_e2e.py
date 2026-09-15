"""E2E tests against the REAL Edvibe API (school account).

Safety contract (user-mandated):
- NEVER deletes anything;
- creates ONLY brand-new materials/lessons/exercises named CLI-HARNESS-*;
- run manually / on demand (credentials required):
    EDVIBE_EMAIL=... EDVIBE_PASSWORD=... pytest test_full_e2e.py -v
"""
import os
import sys
import time
import uuid

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cli_anything.edvibe.core.client import EdvibeSession, EdvibeError  # noqa: E402

pytestmark = pytest.mark.skipif(
    not (os.environ.get("EDVIBE_EMAIL") and os.environ.get("EDVIBE_PASSWORD")),
    reason="E2E needs EDVIBE_EMAIL/EDVIBE_PASSWORD env vars")


@pytest.fixture(scope="module")
def session(tmp_path_factory):
    s = EdvibeSession(
        email=os.environ["EDVIBE_EMAIL"],
        password=os.environ["EDVIBE_PASSWORD"],
        session_path=str(tmp_path_factory.mktemp("e2e") / "session.json"),
    )
    s.authenticate()
    assert s.user and s.user.get("Id")
    return s


def _tag():
    return f"CLI-HARNESS-E2E {int(time.time())} {uuid.uuid4().hex[:4]}"


def test_001_login_and_whoami(session):
    assert session.user.get("Email") == os.environ["EDVIBE_EMAIL"]


def test_002_create_material(session):
    name = _tag()
    book_id = session.create_material(name)
    assert isinstance(book_id, int)
    found = [i for i in session.list_materials() if "Book" in i and i["Book"].get("Id") == book_id]
    assert found and found[0]["Book"]["Name"] == name
    session.__book_id = book_id
    session.__book_name = name


def test_003_add_lesson_and_section(session):
    book_id = session.__book_id
    course_id = session.get_course_id(book_id)
    lesson_id, section_id = session.create_lesson(book_id, course_id, "Lesson 1")
    assert isinstance(lesson_id, int) and isinstance(section_id, int)
    les = session.get_lesson(lesson_id)
    assert any(s.get("Id") == section_id for s in (les.get("Sections") or []))
    session.__lesson_id = lesson_id
    session.__section_id = section_id


def test_004_add_note_exercise(session):
    html = "<h3>Warm-up</h3><p>Hello from the e2e test. <b>Bold</b> works.</p>"
    saved = session.add_note(session.__lesson_id, session.__section_id, html)
    assert saved and saved.get("Id")
    items = session.load_exercises(session.__section_id, session.__lesson_id)
    assert any(e.get("Id") == saved.get("Id") and (e.get("Note") or {}).get("Text") == html
               for e in items)


def test_005_import_html_end_to_end(session, tmp_path):
    html_file = tmp_path / "handout.html"
    html_file.write_text("""<!doctype html><html><head><title>E2E Handout B1</title></head>
<body>
<h1>E2E Handout</h1>
<h2>SECTION 1: Warm-Up</h2>
<p>Talk about your week.</p>
<h2>SECTION 2: Vocabulary</h2>
<ul><li>word one</li><li>word two</li></ul>
<h2>SECTION 3: Homework</h2>
<p>Write three sentences.</p>
</body></html>""", encoding="utf-8")
    res = session.import_html(str(html_file), material_name=_tag())
    assert res["material_id"] and res["lesson_id"] and res["section_id"]
    assert len(res["notes"]) == 3
    items = session.load_exercises(res["section_id"], res["lesson_id"])
    texts = [(e.get("Note") or {}).get("Text", "") for e in items]
    assert any("Warm-Up" in t for t in texts)
    assert any("Vocabulary" in t for t in texts)
