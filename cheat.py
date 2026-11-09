import base64
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.common.by import By
from openai import OpenAI
from pypdf import PdfReader

# Config lives in the ".env" file next to this script ("env" also accepted).
# Point CHEAT_ENV_FILE at another file to OVERRIDE settings on top of it —
# used by the local test setup (see mock_spada.py, test.env, test-real-llm.env).
_BASE_ENV = Path(__file__).parent / "env"
if not _BASE_ENV.exists():
    _BASE_ENV = Path(__file__).parent / ".env"
load_dotenv(_BASE_ENV)  # fill gaps only — values set by the shell ($env:...) win
_OVERRIDE_FILE = os.environ.get("CHEAT_ENV_FILE")
if _OVERRIDE_FILE:
    load_dotenv(_OVERRIDE_FILE, override=True)

for _required in ("BASE_URL", "MODEL", "SPADA_USERNAME", "PASSWORD", "USER_AGENT", "QUIZ_URL"):
    if not os.environ.get(_required):
        sys.exit(f"Error: {_required} is not set. Fill it in in the env file next to cheat.py.")

BASE_URL = os.environ["BASE_URL"]
MODEL = os.environ["MODEL"]
BYNARA_API_KEY = os.environ.get("BYNARA_API_KEY", "")

SPADA_USERNAME = os.environ["SPADA_USERNAME"]
PASSWORD = os.environ["PASSWORD"]
USER_AGENT = os.environ["USER_AGENT"]

# Link to the quiz page of THIS test, pasted in on the day of the test, e.g.
#   https://spada.upnyk.ac.id/mod/quiz/view.php?id=659647
# The attempt id only exists once the quiz is started, so the script opens the
# quiz page and starts the attempt itself (no more hardcoded ATTEMPT/CMID).
# Pasting an already-open attempt URL (.../attempt.php?attempt=...) also works.
QUIZ_URL = os.environ["QUIZ_URL"]
# If QUIZ_URL is a course page (…/course/view.php?id=…), the script waits for
# and opens the quiz whose name contains this text:
QUIZ_NAME = os.environ.get("QUIZ_NAME", "").strip()
# Quiz password ("Kata sandi" prompt before the attempt). Leave empty to type
# it in the browser window yourself.
QUIZ_PASSWORD = os.environ.get("QUIZ_PASSWORD", "")

# 1 = 'a'/'q' are captured system-wide (press them from any window),
#     0 = keys are only read while the terminal window is focused
GLOBAL_KEYS = os.environ.get("GLOBAL_KEYS", "1").strip().lower() not in ("0", "false", "no")
# AUTO_NAV=1    -> script logs in and opens the quiz itself (the old behaviour).
#                  Default 0: you do the navigation, the script follows the screen.
# AUTO_ANSWER=1 -> answer automatically the moment a question is detected.
#                  Default 0: wait for 'a' on each question.
AUTO_NAV = os.environ.get("AUTO_NAV", "0").strip().lower() not in ("0", "false", "no")
AUTO_ANSWER = os.environ.get("AUTO_ANSWER", "0").strip().lower() not in ("0", "false", "no")
# AUTO_NEXT=1    -> after an answer is picked, click "Next page" yourself.
#                  The last page still waits for you to finish/submit.
AUTO_NEXT = os.environ.get("AUTO_NEXT", "0").strip().lower() not in ("0", "false", "no")
# AUTO_SUBMIT=1  -> finish + submit the attempt once the last answer is picked.
#                  Default 0: the Finish/Submit steps stay yours.
AUTO_SUBMIT = os.environ.get("AUTO_SUBMIT", "0").strip().lower() not in ("0", "false", "no")

MATERIALS_DIR = Path(__file__).parent / "materials"
MAX_MATERIAL_CHARS = 60_000  # keep prompts reasonable on long materials
# ============================================


def build_client() -> OpenAI:
    if not BYNARA_API_KEY:
        sys.exit(
            "Error: BYNARA_API_KEY is empty. Set it in the env file next to cheat.py."
        )
    # The official client already retries; a manual loop on top of it only stacked
    # the timeouts and never fired (no letter is ever found in an error).
    return OpenAI(api_key=BYNARA_API_KEY, base_url=BASE_URL, timeout=60, max_retries=3)


def load_materials() -> str:
    """Read every .txt/.md/.pdf in the materials folder into one reference text."""
    if not MATERIALS_DIR.is_dir():
        print(f"Warning: materials folder not found at {MATERIALS_DIR} — answering WITHOUT reference material.")
        return ""

    parts = []
    for path in sorted(MATERIALS_DIR.iterdir()):
        if path.suffix.lower() in (".txt", ".md"):
            parts.append(f"### {path.name}\n{path.read_text(encoding='utf-8', errors='replace')}")
        elif path.suffix.lower() == ".pdf":
            try:
                reader = PdfReader(str(path))
                text = "\n".join(page.extract_text() or "" for page in reader.pages)
                parts.append(f"### {path.name}\n{text}")
            except Exception as e:
                print(f"Warning: could not read PDF {path.name}: {e}")

    combined = "\n\n".join(parts).strip()
    if not combined:
        print("Warning: materials folder exists but is empty — answering WITHOUT reference material.")
        return ""

    print(f"Loaded materials: {len(parts)} file(s), {len(combined)} characters.")
    if len(combined) > MAX_MATERIAL_CHARS:
        print(f"Materials exceed {MAX_MATERIAL_CHARS} chars — truncating to fit the prompt.")
        combined = combined[:MAX_MATERIAL_CHARS]
    return combined


def get_answer_letter(client: OpenAI, materials: str, question: str, options: list[str]) -> str | None:
    option_lines = "\n".join(f"{chr(97 + i)}. {opt}" for i, opt in enumerate(options))

    material_block = ""
    if materials:
        material_block = (
            "\n\nMATERI REFERENSI (gunakan sebagai dasar jawaban, jawab sesuai isi materi):\n"
            f"{materials}"
        )

    prompt = (
        "Jawablah soal pilihan ganda berikut HANYA dengan huruf opsinya (a, b, c, d, atau e) "
        "tanpa penjelasan apapun."
        + material_block
        + f"\n\nSoal:\n{question}\n\nPilihan:\n{option_lines}\n\nJawaban:"
    )

    # Retries happen inside the client (max_retries), not here.
    try:
        response = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
        )
    except Exception as e:
        print(f"API error: {e}")
        return None
    text = (response.choices[0].message.content or "").strip().lower()
    for char in text:
        if char in "abcde":
            return char
    return None  # got a reply but no usable letter


CHROME_JS = r"""
(() => {
  // Fake window chrome (title bar + toolbar + taskbar) drawn over every page.
  // pointer-events: none => purely visual, clicks pass through to the real page.
  // The page content is pushed down (body padding) so the site's own navbar
  // stays visible below the fake toolbar. Pages with their own chrome (the
  // mock login) are left alone.
  if (window.top !== window.self) return;
  // CDP fires this before any DOM exists — defer until documentElement is ready.
  const CHROME_H = 68;  // title bar 30 + toolbar 38
  const init = () => {
    if (document.getElementById('spada-chrome')) return;
    const ownChrome = !!document.querySelector('.titlebar');
    const top = ownChrome ? '' : `
    <div class="sc-title"><span class="sc-tb-left"><img class="sc-favicon" src="__LOGO__"
      onerror="this.style.display='none'"><span class="sc-doctitle"></span></span>
      <span class="sc-btns"><span>&#8722;</span><span>&#9634;</span><span>&#10005;</span></span></div>
    <div class="sc-tools"><img src="__REFRESH__" onerror="this.style.display='none'"><img
      src="__HAMBURGER__" onerror="this.style.display='none'"></div>`;
    const wrap = document.createElement('div');
    wrap.id = 'spada-chrome';
    wrap.innerHTML = `
    <style>
      #spada-chrome { position: fixed; inset: 0; z-index: 2147483647; pointer-events: none;
        font-family: 'Segoe UI', Arial, sans-serif; }
      #spada-chrome .sc-title { background: #fdfdfd; border-bottom: 1px solid #ebebeb; height: 30px;
        display: flex; justify-content: space-between; align-items: center; padding-left: 8px;
        font-size: 12px; color: #1a1a1a; }
      #spada-chrome .sc-tb-left { display: flex; align-items: center; gap: 8px; }
      #spada-chrome .sc-favicon { width: 16px; height: 16px; }
      #spada-chrome .sc-btns { display: flex; align-self: stretch; }
      #spada-chrome .sc-btns span { width: 46px; display: flex; align-items: center; justify-content: center;
        font-size: 10px; color: #333; }
      #spada-chrome .sc-tools { background: #fff; color: #444; font-size: 20px; padding: 8px 16px;
        display: flex; justify-content: space-between; }
      #spada-chrome .sc-tools img { height: 22px; width: auto; }
      #spada-chrome .sc-task { position: absolute; bottom: 0; left: 0; right: 0; background: #f5f5f5; color: #333;
        height: 46px; display: flex; align-items: center; justify-content: space-between; padding: 0 14px 0 5px;
        font-size: 12px; }
      #spada-chrome .sc-appwrap { position: relative; align-self: stretch; display: flex; align-items: center; }
      #spada-chrome .sc-appwrap::after { content: ""; position: absolute; bottom: 0; left: 0; right: 0;
        height: 3px; background: #0067c0; }
      #spada-chrome .sc-app { width: 30px; height: 30px; }
      #spada-chrome .sc-tray { display: flex; align-items: center; gap: 14px; }
      #spada-chrome .sc-tray img { height: 17px; width: auto; }
      #spada-chrome .sc-tray img.sc-vol { height: 38px; }
      #spada-chrome .sc-tray img.sc-power { height: 34px; }
      #spada-chrome .sc-clock { text-align: center; line-height: 1.3; font-size: 13px; color: #6c6470; }
      #spada-chrome .sc-time { font-weight: 600; -webkit-text-stroke: 0.25px currentColor; }
    </style>
    ${top}
    <div class="sc-task">
      <span class="sc-appwrap"><img class="sc-app" src="__LOGO__" onerror="this.style.display='none'"></span>
      <div class="sc-tray">
        <img class="sc-vol" src="__VOLUME__" onerror="this.style.display='none'">
        <span class="sc-clock"><span class="sc-time"></span><br><span class="sc-date"></span></span>
        <img src="__POWER__" class="sc-power" onerror="this.style.display='none'">
      </div>
    </div>`;
    document.documentElement.appendChild(wrap);
    if (!ownChrome) {
      document.body.style.setProperty('padding-top', CHROME_H + 'px', 'important');
      // The site's own top bars are fixed/sticky at the viewport top — body padding
      // can't move those, so shift them below the fake chrome explicitly.
      document.querySelectorAll('body nav, body header').forEach((el) => {
        const cs = getComputedStyle(el);
        if (cs.position !== 'fixed' && cs.position !== 'sticky') return;
        const r = el.getBoundingClientRect();
        if (r.top > 100 || r.height > 160) return;  // only actual top bars, not drawers/FABs
        el.style.setProperty('top', CHROME_H + 'px', 'important');
      });
    }
    const tick = () => {
      const d = new Date();
      wrap.querySelector('.sc-time').textContent =
        d.toLocaleTimeString('en-US', {hour: 'numeric', minute: '2-digit'});
      wrap.querySelector('.sc-date').textContent = d.toLocaleDateString('en-GB');
      wrap.querySelector('.sc-doctitle').textContent = document.title;
    };
    tick(); setInterval(tick, 1000);
  };
  if (document.documentElement) init(); else document.addEventListener('DOMContentLoaded', init);
})();
"""


_ICONS = Path(__file__).parent / "icons"


def _data_uri(p: Path) -> str:
    """Image file as a data URI, so the overlay works on any page (no server needed)."""
    try:
        return "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()
    except OSError:
        return ""


def _overlay_js() -> str:
    subs = {
        "__LOGO__": Path(__file__).parent / "moodle-logo.png",
        "__REFRESH__": _ICONS / "refresh-clean.png",
        "__HAMBURGER__": _ICONS / "hamburger-clean.png",
        "__VOLUME__": _ICONS / "volume-clean.png",
        "__POWER__": _ICONS / "power-clean.png",
    }
    js = CHROME_JS
    for token, path in subs.items():
        js = js.replace(token, _data_uri(path))
    return js


def build_driver() -> webdriver.Chrome:
    options = webdriver.ChromeOptions()
    options.add_argument(f"--user-agent={USER_AGENT}")
    # Reduce obvious automation fingerprints (bar + navigator.webdriver flag)
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_experimental_option("excludeSwitches", ["enable-automation", "enable-logging"])
    options.add_experimental_option("useAutomationExtension", False)
    # Auto-accept native JS dialogs (e.g. older Moodle confirm() on quiz start)
    options.set_capability("unhandledPromptBehavior", "accept")
    driver = webdriver.Chrome(options=options)
    driver.execute_cdp_cmd(
        "Page.addScriptToEvaluateOnNewDocument",
        {"source": "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"},
    )
    driver.execute_cdp_cmd("Page.addScriptToEvaluateOnNewDocument",
                           {"source": _overlay_js()})
    return driver


def _first_visible(driver, selectors):
    """Return the first displayed element matching any of the selectors, else None."""
    for selector in selectors:
        for element in driver.find_elements(By.CSS_SELECTOR, selector):
            if element.is_displayed():
                return element
    return None


def _wait_for_attempt_page(driver, timeout: int = 15) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if "attempt.php" in driver.current_url:
            return True
        time.sleep(1)
    return False


def _ids_from_url(url: str) -> tuple[str, str]:
    params = parse_qs(urlparse(url).query)
    return params["attempt"][0], params["cmid"][0]


def _open_quiz_from_course(driver) -> None:
    """On a course page, wait for the quiz link and click it.

    Restricted quizzes render NO link at all until they open, so this polls
    for a /mod/quiz/ link whose text contains QUIZ_NAME, refreshing the page
    every 60 seconds. While waiting it reports Moodle's restriction message
    (e.g. "Available from 6 October 2026, 10:00 AM").
    """
    if not QUIZ_NAME:
        sys.exit(
            "QUIZ_URL is a course page — set QUIZ_NAME in the env file to the "
            "quiz's name (e.g. Evaluasi tengah semester)."
        )
    print(f"Waiting for '{QUIZ_NAME}' to become available on the course page...")
    last_refresh = time.time()
    last_note = 0.0
    while True:
        links = driver.find_elements(By.CSS_SELECTOR, 'a[href*="/mod/quiz/view.php"]')
        matches = [a for a in links if QUIZ_NAME.lower() in a.text.lower()]
        if matches:
            print(f"Found '{QUIZ_NAME}' — opening it.")
            matches[0].click()
            time.sleep(2)
            return

        # Not clickable yet — report why, using Moodle's own restriction text
        note = f"Still waiting for '{QUIZ_NAME}' to become clickable"
        for item in driver.find_elements(By.CSS_SELECTOR, "li.activity.quiz"):
            try:
                name = item.find_element(By.CSS_SELECTOR, ".instancename").text
            except Exception:
                continue
            if QUIZ_NAME.lower() in name.lower():
                infos = item.find_elements(By.CSS_SELECTOR, ".availabilityinfo")
                if infos and infos[0].text.strip():
                    note += " — " + " ".join(infos[0].text.split())
                break
        if time.time() - last_note >= 30:
            print(note)
            last_note = time.time()
        if time.time() - last_refresh >= 60:
            driver.get(QUIZ_URL)
            last_refresh = time.time()
        time.sleep(2)


def start_quiz_attempt(driver) -> None:
    """Open the quiz page and try to start the attempt.

    If the start button isn't there yet (quiz not open), this just returns —
    wait_for_quiz_entry() keeps waiting and the user can start it manually.
    """
    driver.get(QUIZ_URL)
    time.sleep(2)

    # Course page pasted in? Find and open the quiz by name (waits until the
    # quiz appears — handy when it only shows up on the day of the test).
    if "/course/view.php" in QUIZ_URL:
        _open_quiz_from_course(driver)

    # Attempt already in progress? ("Continue the last attempt" link)
    link = _first_visible(driver, ('a[href*="/mod/quiz/attempt.php?attempt="]',))
    if link:
        link.click()
        _wait_for_attempt_page(driver)
        return

    # Fresh start: Moodle renders the button either as a link straight to
    # startattempt.php or as a submit button inside such a form.
    start = _first_visible(driver, (
        'a[href*="startattempt.php"]',
        'form[action*="startattempt.php"] button[type="submit"]',
        'form[action*="startattempt.php"] input[type="submit"]',
    ))
    if start is None:
        print(
            "No 'attempt quiz' button yet — waiting for the quiz to open "
            "(you can also click it yourself in the browser window)."
        )
        return

    start.click()

    # Wait for the redirect, clicking the confirmation modal when shown. A
    # click's navigation can get dropped when another Selenium command races
    # it (e.g. a watcher polling the page), so re-click the start control
    # until the attempt page is actually reached.
    confirmed = False
    deadline = time.time() + 20
    while time.time() < deadline and "attempt.php" not in driver.current_url:
        if not confirmed:
            confirm = _first_visible(driver, (
                '.modal.show button[data-action="start-attempt"]',
                '.modal.show .modal-footer .btn-primary',
                '.modal.show .btn-primary',
                '.confirmation-dialogue .btn-primary',
                '.confirmation-dialogue input[type="submit"]',
                '.moodle-dialogue .confirmation-buttons input[type="button"]',
            ))
            if confirm:
                confirm.click()
                confirmed = True
            else:
                start = _first_visible(driver, (
                    'a[href*="startattempt.php"]',
                    'form[action*="startattempt.php"] button[type="submit"]',
                    'form[action*="startattempt.php"] input[type="submit"]',
                ))
                if start:
                    try:
                        start.click()
                    except Exception:
                        pass
        time.sleep(1)


_quiz_pw_tries = 0


def _auto_quiz_password(driver) -> bool:
    """Type QUIZ_PASSWORD into a visible quiz password prompt and submit it.

    Returns True when it filled and submitted the form, so the caller can skip
    its waiting note. Gives up after 3 tries (wrong password, odd page) and
    leaves it to the user.
    """
    global _quiz_pw_tries
    if not QUIZ_PASSWORD:
        return False
    fields = [
        el
        for el in driver.find_elements(By.CSS_SELECTOR, 'input[name="quizpassword"]')
        if el.is_displayed()
    ]
    if not fields:
        _quiz_pw_tries = 0
        return False
    if _quiz_pw_tries >= 3:
        return False
    field = fields[0]
    try:
        field.clear()
        field.send_keys(QUIZ_PASSWORD)
        form = field.find_element(By.XPATH, "./ancestor::form")
        buttons = form.find_elements(
            By.CSS_SELECTOR, 'button[type="submit"], input[type="submit"]'
        )
        if buttons:
            buttons[0].click()
        else:
            form.submit()
    except Exception:
        return False
    _quiz_pw_tries += 1
    print(f"Quiz password entered automatically (try {_quiz_pw_tries}/3).")
    time.sleep(2)
    return True


NEXT_SELECTORS = (
    "#mod_quiz-next-nav",  # Moodle 4.x
    'input[type="submit"][name="next"]',  # Moodle 3.x
    'button[name="next"]',
)


def _click_next(driver) -> bool:
    """Click the quiz's 'Next page' button — never 'Finish attempt'."""
    for selector in NEXT_SELECTORS:
        for el in driver.find_elements(By.CSS_SELECTOR, selector):
            if el.is_displayed():
                el.click()
                return True
    return False


FINISH_SELECTORS = (
    "#mod_quiz-finishattempt",
    'input[type="submit"][name="finishattempt"]',
    'button[name="finishattempt"]',
)
SUBMIT_SELECTORS = (
    "#mod_quiz-submitall",
    'input[name="submitall"]',
    'button[name="submitall"]',
    'input[type="submit"][value*="Submit all"]',
)
CONFIRM_SELECTORS = (
    '.modal.show button[data-action="submit"]',
    ".modal.show .btn-primary",
    '.modal.show input[type="submit"]',
    ".confirmation-dialogue button",
    '.confirmation-dialogue input[type="submit"]',
)


def _read_grade(driver) -> str | None:
    """Grade text from the review page, e.g. '90.00 out of 100.00' / '90 / 100'."""
    for xpath in (
        '//th[contains(., "Grade") or contains(., "Nilai")]/following-sibling::td[1]',
        '//dt[contains(., "Grade") or contains(., "Nilai")]/following-sibling::dd[1]',
    ):
        for el in driver.find_elements(By.XPATH, xpath):
            if el.is_displayed() and el.text.strip():
                return el.text.strip()
    return None


_grade_shown = False


def _print_grade(driver) -> None:
    global _grade_shown
    if _grade_shown:
        return
    grade = _read_grade(driver)
    print(f"Grade: {grade}" if grade else "Submitted — no grade shown on this page.")
    _grade_shown = True


def _auto_submit(driver) -> None:
    """Finish the attempt: Finish attempt -> Submit all and finish -> confirm.

    Returns quietly when there is nothing to submit yet (still on an earlier
    question page), so it is safe to call after every answer.
    """
    if _first_visible(driver, NEXT_SELECTORS):
        return  # not on the last page
    idle = 0
    deadline = time.time() + 60
    while time.time() < deadline:
        if "review.php" in driver.current_url:
            print("Attempt submitted — review page reached.")
            _print_grade(driver)
            return
        try:
            clicked = False
            for label, selectors in (
                ("Confirming the submission...", CONFIRM_SELECTORS),
                ("Submitting all answers...", SUBMIT_SELECTORS),
                ("Finishing the attempt...", FINISH_SELECTORS),
            ):
                el = _first_visible(driver, selectors)
                if el:
                    el.click()
                    print(label)
                    clicked = True
                    break
            if clicked:
                idle = 0
                time.sleep(2)
                continue
        except Exception:
            pass
        idle += 1
        if idle >= 3:  # nothing to click (already submitted?) — leave it
            return
        time.sleep(1)


def _advance_or_submit(driver) -> None:
    """AUTO_NEXT: go to the next page. AUTO_SUBMIT: finish when no next is left."""
    moved = False
    if AUTO_NEXT:
        time.sleep(0.3)
        moved = _click_next(driver)
    if AUTO_SUBMIT and not moved:
        _auto_submit(driver)


def wait_for_quiz_entry(driver) -> tuple[str, str]:
    """Block until a real attempt page is showing (past any quiz password).

    A visible password prompt is filled from QUIZ_PASSWORD when that is set;
    otherwise you type it in the browser. Returns (attempt_id, cmid).
    """
    print()
    print("Waiting until you are inside the test...")
    if QUIZ_PASSWORD:
        print("Quiz password is filled in automatically when the quiz asks for it.")
    else:
        print("If the quiz asks for a password, type it into the browser window.")
    print("The script continues automatically once the questions appear (Ctrl+C to stop).")
    last_note = 0.0
    while True:
        if _auto_quiz_password(driver):
            last_note = 0.0
            continue
        if "attempt.php" in driver.current_url:
            password_visible = any(
                el.is_displayed()
                for el in driver.find_elements(By.CSS_SELECTOR, 'input[name="quizpassword"]')
            )
            if not password_visible:
                return _ids_from_url(driver.current_url)
            message = "Quiz password required — waiting for you to enter it in the browser window..."
        else:
            message = f"Still waiting... (currently on {driver.current_url})"
        if time.time() - last_note >= 30:
            print(message)
            last_note = time.time()
        time.sleep(2)


# --- interactive console mode ----------------------------------------------
# Keys: 'a' = answer + pick, 'q' = quit. They are read from the terminal, or —
# when the 'keyboard' (or 'pynput') package is installed and GLOBAL_KEYS=1 —
# captured system-wide, so there is no need to refocus the terminal window.

try:
    import msvcrt

    def _console_key() -> str | None:
        if msvcrt.kbhit():
            ch = msvcrt.getwch().lower()
            if ch in ("\x00", "\xe0"):  # special key (arrows/F-keys): eat 2nd code
                msvcrt.getwch()
                return None
            return ch
        return None

except ImportError:

    def _console_key() -> str | None:
        line = input("(a/q + Enter) ").strip().lower()
        return line[:1] or None


def install_global_keys(queue: list) -> bool:
    """Start capturing 'a'/'q' no matter which window is focused.

    Events are appended to `queue` (deduplicated) and consumed by the main
    loop. Returns True when a global listener is active.
    """
    if not GLOBAL_KEYS:
        return False
    try:
        import keyboard

        def on_press(event):
            name = (event.name or "").lower()
            if name in ("a", "q") and (not queue or queue[-1] != name):
                queue.append(name)

        keyboard.on_press(on_press)
        return True
    except ImportError:
        pass
    try:
        from pynput import keyboard as pkl

        def on_press(key):
            name = (getattr(key, "char", None) or "").lower()
            if name in ("a", "q") and (not queue or queue[-1] != name):
                queue.append(name)

        listener = pkl.Listener(on_press=on_press)
        listener.daemon = True
        listener.start()
        return True
    except ImportError:
        return False


MENU = "[a] answer + pick   [q] quit   — change pages in the browser yourself"


def _strip_letter(text: str) -> str:
    """Remove a leading "a. "/"e)" prefix some Moodle themes put on labels."""
    return re.sub(r"^\s*[a-eA-E][\.\)]\s*", "", text or "").strip()


def _current_question(driver) -> tuple[str | None, list[str]]:
    """Return (question, options) for the question on screen, else (None, []).

    Handles Moodle 4.x markup (div[data-region=answer-label] rows) and the
    older 3.x markup spada runs (radio inputs with a label each).
    """
    qtexts = driver.find_elements(By.CSS_SELECTOR, ".qtext")
    if not qtexts:
        return None, []
    question = qtexts[0].text.strip()

    options = []
    rows = driver.find_elements(By.CSS_SELECTOR, 'div[data-region="answer-label"]')
    for div in rows:  # Moodle 4.x
        try:
            options.append(_strip_letter(div.find_element(By.CSS_SELECTOR, "div.flex-fill").text))
        except Exception:
            options.append(_strip_letter(div.text))
    if not options:  # Moodle 3.x: each radio has a label like "a. <text>"
        for radio in driver.find_elements(By.CSS_SELECTOR, 'input[type="radio"]'):
            rid = radio.get_attribute("id") or ""
            labels = driver.find_elements(By.CSS_SELECTOR, f'label[for="{rid}"]') if rid else []
            options.append(_strip_letter(labels[0].text if labels else ""))
    return question, options


def _pick_answer(driver, letter: str) -> bool:
    """Click the option matching `letter` on the current page."""
    # Moodle 4.x rows
    for div in driver.find_elements(By.CSS_SELECTOR, 'div[data-region="answer-label"]'):
        spans = div.find_elements(By.CSS_SELECTOR, "span.answernumber")
        if spans and spans[0].text.strip().lower().startswith(letter):
            try:
                div.click()
            except Exception:
                radios = div.find_elements(By.CSS_SELECTOR, 'input[type="radio"]')
                if not radios:
                    return False
                driver.execute_script("arguments[0].click()", radios[0])
            return True
    # Moodle 3.x: match the label text ("a. ...", "b. ...") and click it
    for radio in driver.find_elements(By.CSS_SELECTOR, 'input[type="radio"]'):
        rid = radio.get_attribute("id") or ""
        labels = driver.find_elements(By.CSS_SELECTOR, f'label[for="{rid}"]') if rid else []
        if labels and labels[0].text.strip().lower().startswith(letter):
            try:
                labels[0].click()
            except Exception:
                driver.execute_script("arguments[0].click()", radio)
            return True
    return False


def _answer_on_screen(client: OpenAI, materials: str, driver, question: str, options: list[str]) -> bool:
    """Ask the model and click the chosen option on the current page."""
    print("Asking the model...")
    answer = get_answer_letter(client, materials, question, options)
    # The page may have changed while the model was thinking — never pick on the
    # wrong question, so re-check what is on screen before clicking.
    try:
        now_question, _ = _current_question(driver)
    except Exception:
        now_question = None
    if not answer:
        print("No usable answer returned — press 'a' to retry.")
        return False
    if now_question != question:
        print("Page changed while the model was answering — waiting for the next question.")
        return False
    idx = ord(answer) - 97
    option_text = options[idx] if 0 <= idx < len(options) else "?"
    if _pick_answer(driver, answer):
        print(f"Answer: {answer} — {option_text[:120]}  (picked)")
        return True
    print(
        f"Model said {answer} ({option_text[:80]}), but the option "
        "was not found on this page — press 'a' to retry."
    )
    return False


def run_console(client: OpenAI, materials: str, driver) -> None:
    """Follow the browser page-by-page: answers the on-screen question
    (AUTO_ANSWER=1 or on 'a'), clicks 'Next page' (AUTO_NEXT=1) and submits
    the attempt at the end (AUTO_SUBMIT=1).
    """
    key_queue: list[str] = []
    global_keys = install_global_keys(key_queue)
    print()
    print("Following the browser. The script reacts to whatever question is on screen:")
    if AUTO_ANSWER:
        print("  auto-answer ON — questions are answered the moment they appear")
        print("  'a' = ask again / re-pick   'q' = stop")
    else:
        print("  'a' = ask the model and pick (click) the answer on the page")
        print("  'q' = stop the script")
    if global_keys:
        print("Global keys ON — press 'a' from any window, no need to focus this terminal.")
    if AUTO_NEXT:
        print("Auto-next ON — moving to the next page after each answer.")
    if AUTO_SUBMIT:
        print("Auto-submit ON — finishing and submitting the attempt at the end.")
    print("Advancing pages and submitting are done by you, in the browser.")

    last_page = None
    last_answered = None

    while True:
        try:
            if _auto_quiz_password(driver):
                continue
            question, options = _current_question(driver)
        except Exception:  # page was mid-navigation, try again shortly
            time.sleep(0.5)
            continue

        # Key on (url, question): a same-question-next-page is already covered by
        # the url changing, and a reload must still re-print.
        page_key = (driver.current_url, question or "")
        if page_key != last_page:
            last_page = page_key
            if question:
                page_param = parse_qs(urlparse(driver.current_url).query).get("page", [""])[0]
                label = f"Page {int(page_param) + 1}" if page_param.isdigit() else "Question"
                print(f"\n{label}: {question[:150]}")
                if AUTO_ANSWER and options and question != last_answered:
                    if _answer_on_screen(client, materials, driver, question, options):
                        last_answered = question
                        _advance_or_submit(driver)
            else:
                print("\nNo question on this page — submit (or move on) in the browser when ready.")
                if AUTO_SUBMIT:
                    _auto_submit(driver)
                elif "review.php" in driver.current_url:
                    _print_grade(driver)
            print(MENU, flush=True)

        # When global hooks are active they also capture keys typed into this
        # terminal — reading msvcrt as well would answer every 'a' twice.
        key = key_queue.pop(0) if key_queue else (None if global_keys else _console_key())
        if key == "a":
            if not question:
                print("No question on screen — nothing to answer yet.")
            else:
                if _answer_on_screen(client, materials, driver, question, options):
                    _advance_or_submit(driver)
        elif key == "q":
            print("Stopping.")
            return

        time.sleep(0.3)


def main():
    client = build_client()
    materials = load_materials()
    driver = build_driver()

    # The spada domain comes from QUIZ_URL, so nothing is hardcoded here either
    spada_base = f"{urlparse(QUIZ_URL).scheme}://{urlparse(QUIZ_URL).netloc}"

    try:
        if AUTO_NAV:
            driver.get(f"{spada_base}/login/index.php")
            time.sleep(2)

            # Login
            driver.find_element(By.ID, "username").send_keys(SPADA_USERNAME)
            driver.find_element(By.ID, "password").send_keys(PASSWORD)
            driver.find_element(By.ID, "loginbtn").click()
            time.sleep(3)

            if "attempt.php" in QUIZ_URL:
                # Already-open attempt URL pasted in — just use it
                driver.get(QUIZ_URL)
            else:
                start_quiz_attempt(driver)

            # Wait until the questions are actually on screen — this is where the
            # user types the quiz password (or waits for the quiz to open).
            attempt_id, cmid = wait_for_quiz_entry(driver)
            print(f"Inside the quiz (attempt={attempt_id}, cmid={cmid}).")
        else:
            print("Manual navigation: log in, open the quiz and start the attempt yourself.")
            print("The script reacts as soon as it detects a question on screen."
                  if AUTO_ANSWER else
                  "Press 'a' in this terminal once a question is on screen.")
            driver.get(spada_base)
        run_console(client, materials, driver)
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        driver.quit()


if __name__ == "__main__":
    main()
