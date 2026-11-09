"""Visual check for the fake-chrome overlay (CHROME_JS in cheat.py).

Starts the mock, opens its login page in a real Chrome window with the
overlay injected, saves a screenshot, then cleans up.

    python demo_chrome.py   # -> chrome-overlay-check.png
"""
import subprocess
import time
from pathlib import Path

from selenium.webdriver.common.by import By

from cheat import build_driver
from mock_spada import HOST, PORT

OUT = Path(__file__).parent / "chrome-overlay-check.png"


def main():
    mock = subprocess.Popen(["python", "mock_spada.py"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        time.sleep(1.5)  # let the server bind
        driver = build_driver()
        try:
            driver.get(f"http://{HOST}:{PORT}/login/index.php")
            time.sleep(1)  # let the clock tick once
            driver.find_element(By.ID, "loginbtn")          # real form reachable
            overlay = driver.find_element(By.ID, "spada-chrome")
            assert overlay.is_displayed(), "overlay not displayed"
            assert not driver.execute_script("return !!document.querySelector('.sc-title')"), \
                "mock login keeps its own top chrome"
            driver.find_element(By.ID, "loginbtn").click()  # overlay must not block clicks
            time.sleep(1)
            assert "mod/quiz/view.php" in driver.current_url, "login click was blocked by the overlay"
            assert driver.find_element(By.ID, "spada-chrome").is_displayed(), "overlay lost after navigation"
            assert driver.execute_script("return !!document.querySelector('.sc-title')"), \
                "real pages need the fake top chrome"
            assert driver.execute_script("return getComputedStyle(document.body).paddingTop") == "68px", \
                "page content must be pushed below the fake chrome"
            driver.save_screenshot(str(OUT))
            print(f"overlay OK -> {OUT}")
        finally:
            driver.quit()
    finally:
        mock.terminate()


if __name__ == "__main__":
    main()
