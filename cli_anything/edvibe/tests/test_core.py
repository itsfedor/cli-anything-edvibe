"""Unit tests: importer parsing, payload builders, session helpers. No network."""
import json
import os
import sys
import tempfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from cli_anything.edvibe.core import importer  # noqa: E402
from cli_anything.edvibe.core.client import client_time, EdvibeSession  # noqa: E402

SAMPLE = """<!doctype html><html><head><meta charset="utf-8"/>
<title>ESL Teacher's Guide: Sample Lesson B1</title>
<style>body{font-family:sans-serif}</style></head>
<body>
<h1>ESL Teacher's Guide: Sample Lesson</h1>
<p>Intro paragraph for teachers.</p>
<h2>SECTION 1: Warm-Up Discussion Questions (10-12 minutes)</h2>
<p>What comes to mind when you hear <b>"memes"</b>?</p>
<ul><li>option one</li><li>option two</li></ul>
<h3>A. Key Terms Matching</h3>
<p>match the words</p>
<h2>SECTION 2: Video Comprehension</h2>
<p>Watch the video and answer.</p>
<h2>SECTION 3: Discussion</h2>
<p>Discuss with your teacher.</p>
</body></html>"""


def _write(tmp_path, content):
    p = tmp_path / "lesson.html"
    p.write_text(content, encoding="utf-8")
    return str(p)


def test_title_extracted(tmp_path):
    title, sections = importer.html_to_sections(_write(tmp_path, SAMPLE))
    assert title == "ESL Teacher's Guide: Sample Lesson B1"
    assert len(sections) == 3  # h1 dropped, h2 split level chosen


def test_sections_split_and_titled(tmp_path):
    _, sections = importer.html_to_sections(_write(tmp_path, SAMPLE))
    assert sections[0]["title"].startswith("SECTION 1")
    assert sections[1]["title"].startswith("SECTION 2")
    assert "Watch the video" in sections[1]["html"]
    # sub-heading h3 stays inside section 1
    assert "Key Terms Matching" in sections[0]["html"]
    # leading h1 title is not a section
    assert not any(s["title"].startswith("ESL Teacher's Guide: Sample Lesson</") or
                   s["title"].startswith("ESL Teacher") and "SECTION" not in s["title"]
                   for s in sections)


def test_style_and_handlers_removed(tmp_path):
    html = SAMPLE.replace("onclick=\"x()\"", "")  # sample has none; add one
    html = html.replace("<p>Intro paragraph", '<p onclick="alert(1)" style="color:red">Intro paragraph')
    _, sections = importer.html_to_sections(_write(tmp_path, html))
    joined = "\n".join(s["html"] for s in sections)
    assert "onclick" not in joined
    assert "style=" not in joined
    assert "<style>" not in joined


def test_client_time_format():
    ct = client_time()
    assert len(ct) == 23
    assert ct[10] == "T"


def test_session_pick_role():
    assert EdvibeSession._pick_role([1, 2]) == (1, 4)
    assert EdvibeSession._pick_role([2]) == (2, 1)
    assert EdvibeSession._pick_role([3]) == (3, 6)
    assert EdvibeSession._pick_role([0]) == (0, 4)
    with pytest.raises(Exception):
        EdvibeSession._pick_role([])


def test_session_file_roundtrip(tmp_path):
    path = str(tmp_path / "sess.json")
    s = EdvibeSession(session_path=path)
    assert os.path.exists(path) is False
    # simulate persisted token
    import cli_anything.edvibe.core.client as c
    c._save_session(path, {"token": "t1", "user": {"Id": 1}})
    s.token = "t1"
    assert os.path.exists(path)
    s.logout()
    assert not os.path.exists(path)
