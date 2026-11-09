"""Fake spada + fake LLM so cheat.py can be tested end-to-end, fully offline.

Start it, then run the script against it with the test env file:

    python mock_spada.py              # plain flow
    python mock_spada.py --password   # quiz asks for a password first
    python mock_spada.py --modal      # start attempt shows a confirmation modal

    # in a second terminal:
    $env:CHEAT_ENV_FILE = "test.env"  # PowerShell
    $env:AUTO_NAV = "1"               # let cheat.py do the login + open the quiz
    python cheat.py

What you should see:
  1. The Selenium window logs in, opens the quiz, starts the attempt.
  2. With --password the terminal waits until you type 'opensesame' in the browser.
  3. The terminal prints each question as you move between pages in the browser.
  4. Pressing 'a' selects the correct radio in the browser and prints the letter.
  5. Advance with Next page / Finish attempt in the browser; the summary page
     shows the answers that were actually submitted -- they should match the
     terminal and be marked correct.

The mock LLM answers Q1->a, Q2->b, Q3->d, Q4->b, Q5->c (the correct answers from
the course materials), so the summary
page doubles as an automatic check that questions, options and clicks all work.
Test with the real LLM instead via test-real-llm.env (only QUIZ_URL differs;
model, API key and credentials are inherited from .env).
"""

import argparse
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs

HOST, PORT = "127.0.0.1", 8765
QUIZ_PASSWORD = "opensesame"
ATTEMPT_ID, CMID = "999123", "42"
LOGO = Path(__file__).parent / "moodle-logo.png"  # Moodle globe icon (trimmed): favicon + taskbar
ICONS = {  # /theme/<name> -> file ("volume and eng.png" gets a URL-safe alias; -clean = transparent bg)
    "logo.png": LOGO,
    "refresh.png": Path(__file__).parent / "icons" / "refresh-clean.png",
    "hamburger.png": Path(__file__).parent / "icons" / "hamburger-clean.png",
    "volume-eng.png": Path(__file__).parent / "icons" / "volume-clean.png",
    "power.png": Path(__file__).parent / "icons" / "power-clean.png",
}

# Questions taken from the course materials (materials/ folder, Struktur Data lectures):
# 1-2 from "3 Array", 3 from "4 Pointer & Linked List", 4 from "5 Circular dan
# Doubly Linked List", 5 from "6 Stack". The mock LLM maps Q<number> -> CORRECT.
QUESTIONS = [
    ("Q1: Array menyimpan sekumpulan elemen pada lokasi memori yang ...", [
        "berurutan dan bertipe data sama",
        "acak dan bertipe data berbeda-beda",
        "berurutan dan bertipe data berbeda-beda",
        "acak dan bertipe data sama",
    ]),
    ("Q2: Untuk mengakses nilai yang ditunjuk oleh sebuah pointer, operator yang digunakan adalah ...", [
        "& (ampersand)",
        "* (asterisk / dereferencing)",
        "# (pagar)",
        "-> (panah)",
    ]),
    ("Q3: Kelebihan utama Doubly Linked List dibandingkan Singly Linked List adalah ...", [
        "ukuran memori yang lebih kecil",
        "akses elemen O(1) melalui indeks",
        "penyimpanan yang selalu berurutan di memori",
        "traversal bolak-balik (maju dan mundur)",
    ]),
    ("Q4: Prinsip kerja stack adalah ...", [
        "FIFO (First In, First Out)",
        "LIFO (Last In, First Out)",
        "akses acak ke setiap elemen",
        "penyisipan selalu terurut",
    ]),
    ("Q5: Kondisi error yang terjadi ketika push dilakukan pada stack berbasis array yang sudah penuh disebut ...", [
        "stack underflow",
        "memory leak",
        "stack overflow",
        "heap overflow",
    ]),
]
CORRECT = ["a", "b", "d", "b", "c"]  # answer key the summary page checks against
# Same question text on a different page — the script must treat it as a new question.
DUPLICATE = QUESTIONS[0]
# Pages of the quiz. Page 2 repeats page 1's question verbatim: same text, new
# URL — the case the old (url, question) key mishandled. Every question still
# appears on a page of its own, so the answer key stays complete.
PAGES = [QUESTIONS[0], QUESTIONS[0], QUESTIONS[1], QUESTIONS[2], QUESTIONS[3], QUESTIONS[4]]

USE_PASSWORD = False
MODAL = False
MOODLE3 = False  # render answers with the old radio+label markup

state = {
    "password_ok": False,  # quiz password entered (with --password)
    "answers": {},         # page -> letter actually submitted via Next/Finish
    "submitted": False,
}

PAGE_CSS = """
body { font-family: sans-serif; margin: 2rem; }
.answer { border: 1px solid #ccc; padding: .4rem; margin: .3rem 0; }
.answer label { display: block; cursor: pointer; }
.qtext { font-size: 1.1rem; font-weight: 600; margin-bottom: 1rem; }
.btn { display: inline-block; padding: .4rem .8rem; background: #0f6cbf; color: #fff; text-decoration: none; border: 0; cursor: pointer; }
.modal { display: none; }
.modal.show { display: block; border: 2px solid #0f6cbf; padding: 1rem; margin: 1rem 0; }
"""


def page(title, body):
    return f"""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>{PAGE_CSS}</style></head>
<body><h1>{title}</h1>
{body}
</body></html>"""


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="id"><head><meta charset="utf-8">
<title>e-Learning UPN Veteran Yogyakarta: Masuk ke situs</title>
<style>
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: 'Segoe UI', Arial, sans-serif; height: 100vh; overflow: hidden; display: flex; flex-direction: column; }
.titlebar { background: #fdfdfd; border-bottom: 1px solid #ebebeb; height: 30px; padding-left: 8px;
  display: flex; justify-content: space-between; align-items: center; font-size: 12px; color: #1a1a1a; }
.titlebar img { width: 16px; height: 16px; }
.tb-left { display: flex; align-items: center; gap: 8px; }
.winbtns { display: flex; align-self: stretch; }
.winbtns span { width: 46px; display: flex; align-items: center; justify-content: center; font-size: 10px; color: #333; }
.topbar { background: #fff; color: #444; font-size: 20px; padding: 8px 16px; display: flex; justify-content: space-between; }
.topbar img { height: 22px; width: auto; }
.taskbar { background: #f5f5f5; color: #333; height: 46px; flex: none; display: flex; align-items: center;
  justify-content: space-between; padding: 0 14px 0 5px; }
.taskbar .app { position: relative; display: flex; align-self: stretch; align-items: center; }
.taskbar .app::after { content: ""; position: absolute; bottom: 0; left: 0; right: 0; height: 3px; background: #0067c0; }
.taskbar img { width: 30px; height: 30px; }
.tray { display: flex; align-items: center; gap: 14px; font-size: 12px; }
.tray img { height: 17px; width: auto; }
.tray img[src="/theme/volume-eng.png"] { height: 38px; }
.tray img[src="/theme/power.png"] { height: 34px; }
.clock { text-align: center; line-height: 1.3; font-size: 13px; color: #6c6470; }
.clock .time { font-weight: 600; -webkit-text-stroke: 0.25px currentColor; }
.navbar { background: #0d3b5e; color: #fff; padding: 15px 18px; font-size: 19px; font-weight: 700;
  letter-spacing: .5px; display: flex; align-items: center; gap: 14px; }
.hero { flex: 1; min-height: 0; overflow: auto; display: flex; align-items: center; justify-content: center; padding: 48px 16px;
  background: linear-gradient(180deg, #7d9db4 0%, #a9c3cf 30%, #567d5a 62%, #2e5c3c 100%); }
/* ponytail: gradient stands in for the campus photo — swap to background: url('foto.jpg') center/cover when you have it */
.card { width: 910px; max-width: 100%; background: #fff; padding: 24px 0 46px;
  border-top: 3px solid; border-image: linear-gradient(90deg, #1e88e5 50%, #e07b26 50%) 1; }
.banner { background: #1e88e5; color: #fff; margin: 0 22px; padding: 16px 26px;
  display: flex; align-items: center; gap: 20px; }
.banner h1 { font-family: 'Baloo 2', 'Comic Sans MS', 'Segoe UI', cursive; font-size: 32px; line-height: 1.15; }
.sub { display: flex; align-items: center; gap: 18px; font-size: 27px; font-weight: 600; }
.sub .rule { flex: 1; height: 3px; background: #fff; border-radius: 2px; }
.form { width: 760px; max-width: calc(100% - 44px); margin: 28px auto 0; }
.form input[type=text], .form input[type=password] { width: 100%; padding: 13px; font-size: 15px;
  background: #f2f2f2; border: 1px solid #d5d5d5; border-radius: 3px; margin-bottom: 15px; }
.loginbtn { width: 100%; padding: 15px; background: #1e88e5; color: #fff; border: 0; border-radius: 3px;
  font-size: 15px; font-weight: 700; letter-spacing: 2px; cursor: pointer; }
.row { display: flex; justify-content: space-between; align-items: center; margin: 14px 2px; font-size: 14px; color: #333; }
.row a { color: #1a73e8; text-decoration: none; }
.hint { text-align: center; margin: 14px 0 26px; font-size: 14px; color: #333; }
.hint .q { display: inline-block; width: 18px; height: 18px; border-radius: 50%; background: #e3f0fd;
  color: #1a73e8; font-size: 12px; line-height: 18px; margin-left: 6px; vertical-align: -4px; }
.guestnote { text-align: center; margin: 8px 0 16px; font-size: 15px; color: #333; }
.ghost { width: 100%; padding: 13px; background: #fff; border: 1px solid #ccc; border-radius: 3px;
  color: #1a3c5a; font-size: 14px; font-weight: 700; letter-spacing: 1.5px; cursor: pointer; }
.others { text-align: center; font-size: 27px; color: #555; margin: 28px 0 16px; }
</style></head><body>
<div class="titlebar"><span class="tb-left"><img src="/theme/logo.png" alt=""><span>e-Learning UPN Veteran Yogyakarta: Masuk ke situs</span></span>
  <span class="winbtns"><span>&#8722;</span><span>&#9634;</span><span>&#10005;</span></span></div>
<div class="topbar"><img src="/theme/refresh.png" alt=""><img src="/theme/hamburger.png" alt=""></div>
<div class="navbar">E-LEARNING <span>&#127760;</span></div>
<div class="hero">
  <div class="card">
    <div class="banner">
      <svg width="80" height="86" viewBox="0 0 64 68" aria-hidden="true">
        <path d="M32 2 60 11v24c0 15-12 26-28 31C16 61 4 50 4 35V11Z" fill="#fdd200" stroke="#d63a2f" stroke-width="3"/>
        <path d="M32 16c7 5 7 11 0 16s-7 11 0 16" fill="none" stroke="#fff" stroke-width="5" stroke-linecap="round"/>
      </svg>
      <div>
        <h1>E-Learning UPN Veteran Yogyakarta</h1>
        <div class="sub">Spada Wimaya <span class="rule"></span></div>
      </div>
    </div>
    <form class="form" method="post" action="/login/">
      <input type="text" name="username" id="username" placeholder="Nama pengguna" autocomplete="username">
      <input type="password" name="password" id="password" placeholder="Kata sandi" autocomplete="current-password">
      <button type="submit" id="loginbtn" class="loginbtn">MASUK</button>
      <div class="row">
        <label><input type="checkbox" name="rememberusername" id="rememberusername"> Ingat pengguna</label>
        <a href="#">Lupa nama pengguna dan kata sandi?</a>
      </div>
      <div class="hint">Kuki harus diaktifkan pada peramban Anda <span class="q">?</span></div>
      <div class="guestnote">Beberapa kursus membolehkan akses pengguna tamu</div>
      <button type="button" class="ghost">AKSES SEBAGAI TAMU</button>
      <div class="others">Masuk menggunakan akun Anda pada:</div>
      <button type="button" class="ghost">
        <svg width="18" height="22" viewBox="0 0 20 24" aria-hidden="true" style="vertical-align: -5px; margin-right: 8px;">
          <circle cx="10" cy="5" r="4" fill="#7b1fa2"/><path d="M10 9 4 22h12Z" fill="#e53935"/>
        </svg>LOGIN VIA SEMAR
      </button>
    </form>
  </div>
</div>
<div class="taskbar">
  <span class="app"><img src="/theme/logo.png" alt=""></span>
  <div class="tray">
    <img src="/theme/volume-eng.png" alt="">
    <span class="clock" id="clock"><span class="time">10:11 PM</span><br><span class="date">05/10/2026</span></span>
    <img src="/theme/power.png" alt="">
  </div>
</div>
<script>
const clock = document.getElementById('clock');
const tick = () => {
  const d = new Date();
  clock.querySelector('.time').textContent =
    d.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
  clock.querySelector('.date').textContent = d.toLocaleDateString('en-GB');
};
tick(); setInterval(tick, 1000);
</script>
</body></html>"""

PASSWORD_FORM = f"""
<form method="post" action="/mod/quiz/attempt.php?attempt={ATTEMPT_ID}&cmid={CMID}&page=0">
  <p>This quiz requires a password (hint: <b>{QUIZ_PASSWORD}</b>).</p>
  <p><input type="password" name="quizpassword" id="quizpassword"></p>
  <p><input type="submit" name="submitbtn" value="Check password"></p>
</form>
"""

QUIZ_VIEW_LINK = f"""
<p><a class="btn btn-primary" href="/mod/quiz/startattempt.php?cmid={CMID}&sesskey=topsecret">Attempt quiz now</a></p>
"""

QUIZ_VIEW_MODAL = f"""
<button class="btn btn-primary" data-action="startattempt">Attempt quiz now</button>
<div class="modal" id="startmodal">
  <div class="modal-content">
    <p>You are about to start the attempt (time limit etc.)</p>
    <button class="btn btn-primary" data-action="start-attempt"
      onclick="location.href='/mod/quiz/startattempt.php?cmid={CMID}&sesskey=topsecret'">Start attempt</button>
  </div>
</div>
<script>
document.querySelector('[data-action="startattempt"]').addEventListener('click', () => {{
  document.getElementById('startmodal').classList.add('show');
}});
</script>
"""


COURSE_PAGE = f"""
<p>Mock course page. The quiz below only "appears" when it exists —
the script must find it by name.</p>
<ul>
  <li><a class="aalink" href="/mod/forum/view.php?id=99">Announcements</a></li>
  <li><a class="aalink" href="/mod/quiz/view.php?id={CMID}">
    <span class="instancename">Evaluasi tengah semester</span></a></li>
</ul>
"""


def _question(page_no: int) -> tuple[str, list[str]]:
    """Duplicate question on page 2: different URL, same text as page 1."""
    return PAGES[page_no]


def _option_rows_4x(page_no: int) -> str:
    """Moodle 4.x style: rows wrapped in div[data-region=answer-label]."""
    question, options = _question(page_no)
    rows = []
    for i, opt in enumerate(options):
        rows.append(f"""
    <div class="answer" data-region="answer-label">
      <label>
        <input type="radio" name="q{ATTEMPT_ID}:{page_no + 1}_answer" value="{i}">
        <span class="answernumber">{chr(97 + i)}. </span>
        <div class="flex-fill">{opt}</div>
      </label>
    </div>""")
    return "".join(rows)


def _option_rows_3x(page_no: int) -> str:
    """Moodle 3.x style (what spada runs): radio + label pairs."""
    question, options = _question(page_no)
    rows = []
    for i, opt in enumerate(options):
        rows.append(f"""
    <div class="r{i % 2}">
      <input type="radio" name="q{ATTEMPT_ID}:{page_no + 1}_answer" value="{i}" id="resp{i}">
      <label for="resp{i}"><span class="answernumber">{chr(97 + i)}. </span>{opt}</label>
    </div>""")
    return f'<div class="answer">{"".join(rows)}</div>'


def question_page(page_no: int) -> str:
    question, options = _question(page_no)
    body_options = _option_rows_3x(page_no) if MOODLE3 else _option_rows_4x(page_no)
    if page_no < len(PAGES) - 1:
        nav = '<input type="submit" name="next" value="Next page">'
    else:
        nav = '<input type="submit" name="finishattempt" value="Finish attempt ...">'
    return f"""
  <div class="qtext">{question}</div>
  <form method="post" action="/mod/quiz/attempt.php?attempt={ATTEMPT_ID}&cmid={CMID}&page={page_no}">
    {body_options}
    <p>{nav}</p>
  </form>
  <p><i>Mock: question {page_no + 1} / {len(PAGES)} — press 'a' in the terminal,
  then click {nav and 'Next page' or 'Finish attempt'} in this window.</i></p>
"""


SUBMIT_ALL = f"""
<form method="post" action="/mod/quiz/summary.php">
  <p><input type="submit" name="submitall" id="mod_quiz-submitall" value="Submit all and finish"
    onclick="document.getElementById('submitmodal').classList.add('show'); return false;"></p>
  <div class="modal" id="submitmodal">
    <div class="modal-content">
      <p>Once you submit, you can no longer change your answers.</p>
      <button type="submit" name="submitall" value="1" data-action="submit"
        class="btn btn-primary">Submit all and finish</button>
    </div>
  </div>
</form>
"""


def _answers_for(question: str) -> set:
    """Every letter submitted for a question, across the pages that asked it."""
    return {state["answers"][p] for p in _pages_for(question) if p in state["answers"]}


def _pages_for(question: str) -> list[int]:
    return [i for i in range(len(PAGES)) if _question(i)[0] == question]


def _fully_answered(question: str, expected: str) -> bool:
    """Answered on EVERY page that asked it, always with the right letter."""
    pages = _pages_for(question)
    return all(state["answers"].get(p) == expected for p in pages)


def summary_html() -> str:
    rows = []
    for q, (question, _) in enumerate(QUESTIONS):
        given = _answers_for(question)
        ok = "&#9989; correct" if _fully_answered(question, CORRECT[q]) else f"&#10060; expected {CORRECT[q]}"
        shown = "/".join(g or "—" for g in sorted(given)) or "—"
        rows.append(f"<li>{question} — submitted: <b>{shown}</b> {ok}</li>")
    if state["submitted"]:
        correct = sum(1 for q, (q_text, _) in enumerate(QUESTIONS)
                      if _fully_answered(q_text, CORRECT[q]))
        total = len(QUESTIONS)
        status = (
            "<p><b>Attempt submitted.</b> Test complete!</p>"
            '<table class="quizreviewsummary"><tr><th>Grade</th>'
            f"<td>{correct:.2f} out of {total:.2f} ({correct * 100 // total}%)</td></tr></table>"
        )
    else:
        status = "<p>Check your answers, then submit.</p>" + SUBMIT_ALL
    return f"<ol>{''.join(rows)}</ol>{status}<p><a href='/mod/quiz/view.php?id={CMID}'>Back to quiz page</a></p>"


class MockHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):  # keep the console quiet
        pass

    def handle_one_request(self):
        try:
            super().handle_one_request()
        except ConnectionResetError:  # browser quit mid-request — normal, not an error
            self.close_connection = True

    # --- helpers ----------------------------------------------------------
    def _send_html(self, html, code=200):
        data = html.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")  # Selenium windows must never show a stale page
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_json(self, obj, code=200):
        data = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _raw_body(self) -> str:
        length = int(self.headers.get("Content-Length") or 0)
        return self.rfile.read(length).decode("utf-8") if length else ""

    def _redirect(self, location):
        self.send_response(302)
        self.send_header("Location", location)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _form(self):
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length).decode("utf-8") if length else ""
        return parse_qs(raw)

    # --- routes -----------------------------------------------------------
    def do_GET(self):
        path, _, query = self.path.partition("?")
        q = parse_qs(query)

        if path == "/login/index.php":
            self._send_html(LOGIN_PAGE)
        elif path.startswith("/theme/"):
            icon = ICONS.get(path[len("/theme/"):])
            if icon and icon.exists():
                data = icon.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type", "image/png")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
            else:
                self._send_html(page("Not found", "<p>No such theme asset</p>"), code=404)
        elif path == "/mod/quiz/view.php":
            body = QUIZ_VIEW_MODAL if MODAL else QUIZ_VIEW_LINK
            self._send_html(page("Quiz: Test quiz", body))
        elif path == "/course/view.php":
            self._send_html(page("Course: Test course", COURSE_PAGE))
        elif path == "/mod/quiz/startattempt.php":
            self._redirect(f"/mod/quiz/attempt.php?attempt={ATTEMPT_ID}&cmid={CMID}&page=0")
        elif path == "/mod/quiz/attempt.php":
            page_no = int(q.get("page", ["0"])[0])
            if USE_PASSWORD and not state["password_ok"]:
                self._send_html(page("Quiz password", PASSWORD_FORM))
            else:
                self._send_html(page(f"Question {page_no + 1}", question_page(page_no)))
        elif path == "/mod/quiz/summary.php":
            self._send_html(page("Summary of attempt", summary_html()))
        elif path == "/mod/quiz/review.php":
            self._send_html(page("Review of attempt", summary_html()))
        else:
            self._send_html(page("Not found", f"<p>Unknown path: {path}</p>"), code=404)

    def do_POST(self):
        path, _, query = self.path.partition("?")
        q = parse_qs(query)
        raw = self._raw_body()
        form = parse_qs(raw)

        if path == "/login/":  # any credentials are accepted
            self.send_response(302)
            self.send_header("Location", f"/mod/quiz/view.php?id={CMID}")
            self.send_header("Set-Cookie", "MoodleSession=mocksession; Path=/")
            self.send_header("Content-Length", "0")
            self.end_headers()
        elif path == "/mod/quiz/attempt.php":
            page_no = int(q.get("page", ["0"])[0])
            if USE_PASSWORD and not state["password_ok"]:
                given = (form.get("quizpassword") or [""])[0]
                if given == QUIZ_PASSWORD:
                    state["password_ok"] = True
                    self._redirect(self.path)  # reload the same URL: now shows the question
                else:
                    self._send_html(page(
                        "Quiz password",
                        "<p style='color:red'>Wrong password, try again.</p>" + PASSWORD_FORM,
                    ))
                return
            # which radio was picked? name looks like "q999123:1_answer", value = index
            letter = None
            for key, values in form.items():
                if key.endswith("_answer") and values:
                    letter = chr(97 + int(values[0]))
            state["answers"][page_no] = letter
            if "finishattempt" in form:
                self._redirect("/mod/quiz/summary.php")
            else:
                next_page = min(page_no + 1, len(PAGES) - 1)
                self._redirect(
                    f"/mod/quiz/attempt.php?attempt={ATTEMPT_ID}&cmid={CMID}&page={next_page}"
                )
        elif path == "/mod/quiz/summary.php":
            if "submitall" in form:
                state["submitted"] = True
                self._redirect(f"/mod/quiz/review.php?attempt={ATTEMPT_ID}")
            else:
                self._redirect("/mod/quiz/summary.php")
        elif path == "/v1/chat/completions":
            self._mock_llm(raw)
        else:
            self._send_html(page("Not found", "<p>Unknown POST path</p>"), code=404)

    def _mock_llm(self, raw: str):
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            payload = {}
        content = "".join(m.get("content") or "" for m in payload.get("messages", []))
        # the question starts right after "Soal:" in cheat.py's prompt
        m = re.search(r"Soal:\s*\n?\s*Q(\d+)", content)
        letter = CORRECT[int(m.group(1)) - 1] if m else "z"  # 'z' exercises the retry path
        self._send_json({
            "id": "chatcmpl-mock",
            "object": "chat.completion",
            "created": 0,
            "model": payload.get("model", "mock"),
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": letter},
                "finish_reason": "stop",
            }],
        })


def main():
    global USE_PASSWORD, MODAL
    parser = argparse.ArgumentParser(description="Mock spada + LLM for testing cheat.py")
    parser.add_argument("--password", action="store_true", help="quiz asks for a password (opensesame)")
    parser.add_argument("--modal", action="store_true", help="start attempt via confirmation modal")
    parser.add_argument("--moodle3", action="store_true", help="render answers in old Moodle 3.x markup")
    args = parser.parse_args()
    USE_PASSWORD, MODAL, MOODLE3 = args.password, args.modal, args.moodle3

    server = ThreadingHTTPServer((HOST, PORT), MockHandler)
    print(f"Mock spada + LLM listening on http://{HOST}:{PORT}")
    print(f"  quiz page : http://{HOST}:{PORT}/mod/quiz/view.php?id={CMID}")
    print(f"  password  : {'ON (' + QUIZ_PASSWORD + ')' if USE_PASSWORD else 'off'}   modal: {'on' if MODAL else 'off'}")
    print()
    print("Now run in another terminal:")
    print('  $env:CHEAT_ENV_FILE = "test.env"; $env:AUTO_NAV = "1"; python cheat.py')
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nMock stopped.")


if __name__ == "__main__":
    main()
