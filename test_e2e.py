"""Automated end-to-end test: runs the real cheat.py against mock_spada.py.

Usage:
    python mock_spada.py                    # terminal 1 (leave it running)
    python test_e2e.py [override.env]       # terminal 2

    test.env           -> mock LLM (fully offline)
    test-real-llm.env  -> real LLM from .env, mock quiz site

Simulates 'a' keypresses (once per question, only until an answer is picked),
clicks Next/Finish in the browser the way you would, then checks the mock's
summary page shows every answer correct. A Chrome window will open and drive itself.
"""
import os
import sys
import threading
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
override_file = sys.argv[1] if len(sys.argv) > 1 else "test.env"
os.environ["CHEAT_ENV_FILE"] = override_file
os.environ["GLOBAL_KEYS"] = "0"  # e2e drives keys via _console_key, not global hooks
os.environ["AUTO_NAV"] = "1"     # e2e relies on the script doing login + quiz open
print(f"Running cheat.py against the mock, env override: {override_file}")

import cheat
from mock_spada import PAGES as N_PAGES
from mock_spada import QUESTIONS as MOCK_QUESTIONS

n_pages = len(N_PAGES)  # pages driven in the browser (includes the duplicate)
n_questions = len(MOCK_QUESTIONS)  # distinct questions the summary grades

holder = {}
_orig_build = cheat.build_driver

def build_and_hold():
    d = _orig_build()
    holder["driver"] = d
    return d

cheat.build_driver = build_and_hold

pressed_at = [0.0]
quit_flag = [False]

seen_url = [None]


def fake_key():
    if quit_flag[0]:
        return "q"
    if cheat.AUTO_ANSWER:
        return None  # the script answers on its own — no key needed
    d = holder.get("driver")
    if d is None:
        return None
    try:
        if not d.find_elements("css selector", ".qtext"):
            return None  # not on a question page
        # Only trust "already answered" while still on the SAME url: right after
        # Next, the url flips before the new DOM renders, and the old page's
        # checked radio would otherwise block the next question forever.
        if d.current_url == seen_url[0] and d.execute_script(
            "return !!document.querySelector('input[type=radio]:checked')"
        ):
            return None  # already picked this question — don't waste another call
    except Exception:
        return None  # page mid-navigation
    if time.time() - pressed_at[0] > 1.0:
        pressed_at[0] = time.time()
        seen_url[0] = d.current_url
        return "a"
    return None

cheat._console_key = fake_key

def wait_for(pred, timeout, what):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if pred():
                return
        except Exception:
            pass
        time.sleep(0.5)
    raise SystemExit(f"TIMEOUT waiting for: {what}")

t = threading.Thread(target=cheat.main, daemon=True)
t.start()

wait_for(lambda: holder.get("driver") is not None, 30, "Selenium driver")
d = holder["driver"]

def radio_checked():
    """A radio is checked on the CURRENT page — not a leftover from the last one."""
    return d.execute_script(
        "return !!document.querySelector('input[type=radio]:checked')"
    )

def on_page(i):
    """True once page i is loaded and its own question is on screen."""
    return lambda: (
        "attempt.php" in d.current_url
        and f"page={i}" in d.current_url
        and bool(d.find_elements("css selector", ".qtext"))
    )

# each page gets the bot's answer, then advance (Finish on the last)
for i in range(n_pages):
    wait_for(on_page(i), 60 if i == 0 else 20, f"page {i + 1} to appear")
    wait_for(radio_checked, 90, f"answer pick on page {i + 1}")
    last = "finishattempt" if i == n_pages - 1 else "next"
    # AUTO_NEXT clicks Next; AUTO_SUBMIT clicks Finish — never click twice.
    script_drives = cheat.AUTO_NEXT if last == "next" else cheat.AUTO_SUBMIT
    if not script_drives:
        d.find_element("css selector", f'input[name="{last}"]').click()
    # no wait here: the next iteration's on_page(i) already waits for page i+1
wait_for(lambda: "summary.php" in d.current_url, 20, "summary page")
if cheat.AUTO_SUBMIT:  # the script now submits on its own: Finish -> Submit all -> confirm
    wait_for(lambda: "review.php" in d.current_url, 40, "review page (submitted)")

quit_flag[0] = True
t.join(timeout=30)
assert not t.is_alive(), "console did not stop after 'q'"

html = urllib.request.urlopen("http://127.0.0.1:8765/mod/quiz/summary.php").read().decode()
correct = html.count("&#9989; correct")
print(f"\nSummary check: {correct}/{n_questions} questions correct across {n_pages} pages")
assert correct == n_questions, "submitted answers did not all match:\n" + html
print(f"E2E OK: login -> attempt start -> {n_pages} pages answered+picked -> submitted")

