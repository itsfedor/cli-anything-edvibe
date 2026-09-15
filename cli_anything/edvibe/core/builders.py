"""Transfer builders: content spec -> Edvibe ExerciseView payloads.

Each builder takes a normalized spec (see build_*) and returns an
ExerciseView dict ready for SaveExerciseWsController.SaveExercise.

Content templates:
  note      {html, name, hidden?}
  text      {html, name?}
Topic/article and hidden semantics verified empirically; see
cli_anything/edvibe/tests/test_transfer_e2e.py.
"""
import datetime
import json
import random
import re
import time

EX_VIDEO = 3
EX_AUDIO = 4
EX_TEST = 5
EX_MATCH = 6          # Match the words
EX_FILL_TYPED = 10    # Fill in the gaps (typed) - semantics verified Sep 2026
EX_CHOOSE = 13        # Choose the correct option
EX_TRUE_FALSE = 14
EX_WORD_ORDER = 15
EX_LABEL_DRAG = 17
EX_FILL_BOX = 18      # Fill in the gaps from the box
EX_SORT_COLUMNS = 19
EX_WORDLIST = 21
EX_NOTE = 22
EX_TOPIC = 25
EX_WRITING = 26
EX_PICTURES = 27
EX_TEXT = 28
EX_BUTTON = 29       # external-link button ("Watch the episode" block)
EX_VOICE = 30
EX_DIVIDER = 31
EX_GIF = 32
EX_ORDER_SENTENCES = 37  # Put the text in order

_HTML_ESCAPE = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;",
                              '"': "&quot;"})

HIDDEN_MARK = ('<em class="hide-id-exercise-item">##{uid}##</em>')


def _esc(s):
    return str(s).translate(_HTML_ESCAPE)


def _uid():
    return int(time.time() * 1000) + random.randint(0, 999)


def _now():
    return datetime.datetime.now().strftime("%Y-%m-%dT%H:%M:%S.") + \
        f"{datetime.datetime.now().microsecond // 1000:03d}"


def _base(etype, name, number, hidden=False, autocheck=False):
    """Shared envelope for an ExerciseView row."""
    view = {
        "Id": 0, "Number": number, "IsShowExerciseNumber": bool(name),
        "Name": name, "IsHideExercise": False, "IsHidePupil": hidden,
        "JsonData": json.dumps({"IsAutoCheck": autocheck}),
        "Type": etype, "Words": [], "PracticeNumber": 0,
        "inHomework": False, "IsLessonPage": False,
        "Images": [], "Videos": [], "VideosUrl": [],
        "Answers": {"Id": 0, "SelfSync": False, "IsSelf": False,
                    "IsReset": False, "IsHomework": False,
                    "IsHidePupil": False, "ExerciseId": 0, "PupilId": 0,
                    "Answers": [], "IsRightAnswer": False, "Status": 1,
                    "Version": 0, "SingleAnswer": {}, "ManyAnswers": [],
                    "RepeatingManyAnswers": []},
        "AnswerVersion1": [], "AnswersTeacher": [], "IsAutoCheck": autocheck,
        "Selections": [], "DateCreateHomeWork": "0001-01-01T00:00:00",
    }
    return view


# ------------------------------------------------------------ content blocks
def build_note(html, name="", hidden=False, number=0, lesson_id=None,
               section_id=None):
    view = _base(EX_NOTE, name, number, hidden=hidden)
    view["IsHidePupil"] = bool(hidden)  # live hidden notes carry both flags
    view["Note"] = {"Id": 0, "Name": name, "Text": html,
                    "ShowToPupil": not hidden, "VisibilityType": 1 if hidden else 0,
                    "NoteType": 0, "Number": number, "ExerciseId": 0}
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_text(html, name="", number=0, lesson_id=None, section_id=None):
    view = _base(EX_TEXT, name, number)
    view["Text"] = {"Text": html}
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_writing(instruction, task_html="", number=0, lesson_id=None,
                  section_id=None):
    view = _base(EX_WRITING, instruction, number)
    view["Essay"] = {"Id": 0, "Name": "", "PupilId": 0, "IsByPupil": False,
                     "New": False, "ExerciseId": 0}
    if task_html:
        view["Name"] = instruction  # instruction doubles as task line
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_test(instruction, questions, number=0, timer_min=None, points=True,
               lesson_id=None, section_id=None):
    """questions: [{'q': html, 'options': [html...], 'correct': [idx...]}]"""
    view = _base(EX_TEST, instruction, number, autocheck=True)
    tq = []
    for i, item in enumerate(questions):
        answers = [("", 1) if j in item["correct"] else ("", 0)
                   for j in range(len(item["options"]))]
        q = {"Id": _uid(), "Question": item["q"], "Points": 1.0,
             "Answers": item["options"],
             "QuestionAnswers": [("true" if j in item["correct"] else "")
                                 for j in range(len(item["options"]))]}
        tq.append(q)
    view["Test"] = {"IsEnabledTimer": timer_min is not None,
                    "TimerDuration": timer_min or 1,
                    "IsEnabledPoints": points, "Questions": tq}
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_word_order(instruction, sentences, number=0, lesson_id=None,
                     section_id=None):
    """sentences: list of correct-order token lists or '/'-joined strings."""
    view = _base(EX_WORD_ORDER, instruction, number, autocheck=True)
    items = []
    for s in sentences:
        toks = s.split("/") if isinstance(s, str) else list(s)
        perm = list(range(len(toks)))
        random.shuffle(perm)
        items.append({"Text": "/".join(toks), "Id": _uid(),
                      "SortIndexes": perm})
    view["QuestionWithCodingTexts"] = items
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_sort_columns(instruction, columns, number=0, lesson_id=None,
                       section_id=None):
    """columns: [{'title': str, 'words': [str...]}] - word lists must be
    disjoint across columns."""
    view = _base(EX_SORT_COLUMNS, instruction, number, autocheck=True)
    items = []
    for col in columns:
        items.append({"Group": col["title"],
                      "Words": "/".join(col["words"]),
                      "Ids": [0] * len(col["words"])})
    view["GroupWordsByColumns"] = {"Items": items}
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_order_sentences(instruction, sentences, number=0, lesson_id=None,
                          section_id=None):
    """sentences in correct order."""
    view = _base(EX_ORDER_SENTENCES, instruction, number, autocheck=True)
    items = [{"Id": _uid(), "Text": s} for s in sentences]
    view["SentencesInCorrectOrder"] = {"Items": items}
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


# ------------------------------------------------------ gap-based templates
def _gap_mark(payload, uid=None):
    """Internal Edvibe encoding of one gap: [payload<em>##uid##</em>]."""
    return f"[{payload}{HIDDEN_MARK.format(uid=uid or _uid())}]"


def _find_gaps(text):
    """Split a UI-syntax string with [..] gaps into html chunks + gap payloads.

    Returns list of chunks (html or gap dict) - NOT used for save payloads
    directly; UI-syntax parsing happens in build_fill_* via regex replace.
    """
    parts = []
    pos = 0
    for m in re.finditer(r"\[([^\]]*)\]", text):
        parts.append(text[pos:m.start()])
        parts.append({"payload": m.group(1)})
        pos = m.end()
    parts.append(text[pos:])
    return parts


def convert_gap_syntax(text):
    """Turn UI-paste syntax '[hint/answer]' / '[answer]' / '[a/b]' etc.
    into the internal gap encoding with hidden uid markers.

    UI syntax rules (Edvibe FAQ):
      [answer]            auto-checked gap
      [hint/answer]       hint + one correct answer
      [a1/a2]             several accepted answers
      [hint/a1/a2]        hint + several accepted
      [/a1/a2]            several accepted, no hint
    Internal payload keeps the same slash syntax (the renderer parses it).
    """
    out = []
    pos = 0
    for m in re.finditer(r"\[([^\]]*)\]", text):
        out.append(text[pos:m.start()])
        payload = m.group(1)
        out.append(_gap_mark(payload))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def build_fill_typed(instruction, sentences, number=0, lesson_id=None,
                     section_id=None, autocheck=True):
    """Fill in the gaps (typed). sentences use UI bracket syntax with hints
    or plain answers: 'I [am/be] tired [now].'"""
    view = _base(EX_FILL_TYPED, instruction, number, autocheck=autocheck)
    items = []
    for s in sentences:
        items.append({"Text": convert_gap_syntax(s)})
    view["QuestionWithCodingTexts"] = items
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_choose_option(instruction, lines, number=0, lesson_id=None,
                        section_id=None):
    """Choose the correct option. lines: html with [opt1/opt2*/opt3] gaps
    (asterisk marks the correct option)."""
    view = _base(EX_CHOOSE, instruction, number, autocheck=True)
    items = []
    for s in lines:
        items.append({"Text": convert_gap_syntax(s)})
    view["QuestionWithCodingTexts"] = items
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_fill_box(instruction, sentences, number=0, lesson_id=None,
                   section_id=None, autocheck=True):
    """Fill in the gaps from the box. Each [answer] becomes a draggable box
    item; the box is built from all gap answers. SortIndexes are shuffled to
    match live materials (the box items start in random order)."""
    view = _base(EX_FILL_BOX, instruction, number, autocheck=autocheck)
    converted = []
    uids = []
    for s in sentences:
        chunks = []
        pos = 0
        for m in re.finditer(r"\[([^\]]*)\]", s):
            chunks.append(s[pos:m.start()])
            uid = _uid()
            uids.append(uid)
            chunks.append(_gap_mark(m.group(1), uid))
            pos = m.end()
        chunks.append(s[pos:])
        converted.append("".join(chunks))
    sort_idx = list(range(len(uids)))
    random.shuffle(sort_idx)
    view["PasteWordByDrag"] = {"Text": "<br>".join(converted),
                               "SortIndexes": sort_idx}
    if not autocheck:  # live lesson-section shape (lesson "1 Burnt Food")
        view["JsonData"] = json.dumps({"PasteWordByDrag": view["PasteWordByDrag"],
                                       "IsAutoCheck": False}, ensure_ascii=False)
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_match(instruction, pairs, number=0, lesson_id=None, section_id=None):
    """Match the words. pairs: [(left, right), ...]."""
    lefts = [p[0] for p in pairs]
    rights = [p[1] for p in pairs]
    ids = [_uid() for _ in pairs]
    sort_idx = list(range(len(pairs)))
    random.shuffle(sort_idx)
    view = _base(EX_MATCH, instruction, number, autocheck=True)
    view["MappingWords"] = [{"Ids": ids, "LeftWords": lefts,
                             "RightWords": rights, "SortIndexes": sort_idx}]
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_true_false(instruction, statements, number=0, not_stated=False,
                     lesson_id=None, section_id=None, autocheck=True):
    """statements: [(text, True/False), ...] or (text, True/False/None).

    Answer encoding mirrors the live frontend (verified Sep 2026):
    True -> "true", False -> "", NotStated -> "NotStated"."""
    view = _base(EX_TRUE_FALSE, instruction, number, autocheck=autocheck)
    qs = []
    qa = []
    for text, val in statements:
        qs.append({"Question": text, "Id": _uid()})
        qa.append({True: "true", False: "", None: "NotStated"}[val])
    view["Questions"] = qs
    view["QuestionAnswers"] = qa
    view["TrueOrFalse"] = {"EnableNotStated": not_stated}
    if not autocheck:  # live lesson-section shape (lesson "1 Burnt Food")
        view["JsonData"] = json.dumps({
            "Questions": qs, "QuestionAnswers": qa, "IsAutoCheck": False,
            "TrueOrFalse": view["TrueOrFalse"]}, ensure_ascii=False)
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_button(name, link, text, number=0, lesson_id=None, section_id=None):
    """External-link button (Type 29) - the "watch the episode" block.

    Live shape (lesson "1 Burnt Food"): top-level Button {Link, Text} AND the
    same object inside JsonData; IsShowExerciseNumber stays off."""
    view = _base(EX_BUTTON, name, number)
    view["IsShowExerciseNumber"] = False
    btn = {"Link": link, "Text": text}
    view["Button"] = btn
    view["JsonData"] = json.dumps({"Button": btn, "IsAutoCheck": False},
                                  ensure_ascii=False)
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


# ------------------------------------------------------------------ wordlist
def build_wordlist(instruction, entries, number=0, lesson_id=None,
                   section_id=None, voice_language_id=1):
    """entries: [(word, translation_or_definition), ...] (max 50)."""
    view = _base(EX_WORDLIST, instruction, number)
    view["Words"] = []
    aw = []
    for word, trans in entries:
        tr = [{"LanguageId": voice_language_id, "Text": trans}]
        view["Words"].append({"Id": 0, "Word": word, "Translations": tr,
                              "LanguageId": voice_language_id,
                              "ExerciseId": 0, "IsAdded": False})
        aw.append({"Id": 0, "Word": word, "Translations": tr,
                   "LanguageId": 0, "ExcerciseId": 0, "IsAdded": False,
                   "Number": 0})
    view["AddWordsToVocabulary"] = {
        "VoiceLanguageId": voice_language_id, "IsMultilingual": False,
        "TranslationLanguages": [1], "Words": aw}
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


def build_video(instruction, link, number=0, lesson_id=None, section_id=None):
    """Video block (Type 3). link: YouTube share URL (youtu.be/...)."""
    vid = {"Text": "", "Link": link, "Type": 0, "Uploaded": None}
    jd = {
        "Videos": [vid], "VideosUrl": [], "QuestionManyAnswers": None,
        "Questions": None, "Descriptions": None, "QuestionAnswers": None,
        "Audios": None, "Test": None, "MappingWords": None,
        "NumbersByImages": None, "QuestionWithCodingTexts": None,
        "SpaceTextByImages": None, "MappingImageByDrag": None,
        "ImageInputCorrectWord": None, "ImageSelectCorrectWord": None,
        "PasteWordByDrag": None, "GroupWordsByColumns": None,
        "ChoiseExtraWords": None, "BuildWordFromLetters": None,
        "Text": None, "Button": None, "MiroBoard": None, "GooglePDF": None,
        "Wordwall": None, "Learningapps": None, "SentencesInCorrectOrder": None,
        "SelectRightWord": None, "AddWordsToVocabulary": None,
        "Paintings": None, "IsAutoCheck": False, "Note": None,
        "TrueOrFalse": None, "AudioRecording": None, "Pdfs": None,
    }
    view = _base(EX_VIDEO, instruction, number)
    view["JsonData"] = json.dumps(jd, ensure_ascii=False)
    view["Videos"] = [{"Text": "", "Link": link, "Type": 0}]
    view["VideosUrl"] = []
    if lesson_id is not None:
        view["LessonId"] = lesson_id
        view["LessonSectionId"] = section_id
    return view


BUILDERS = {
    "note": build_note, "text": build_text, "writing": build_writing,
    "test": build_test, "wordorder": build_word_order,
    "sortcolumns": build_sort_columns, "ordersentences": build_order_sentences,
    "filltyped": build_fill_typed, "chooseoption": build_choose_option,
    "fillbox": build_fill_box, "match": build_match,
    "truefalse": build_true_false, "wordlist": build_wordlist,
    "video": build_video, "button": build_button,
}
