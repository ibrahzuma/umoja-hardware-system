"""Drive the Umoja Pro Android app on the emulator and capture each screen.

Sequence:
  1. login screen
  2. type credentials, tap sign in
  3. POS tab (initial)
  4. Stock tab
  5. Quotes tab
  6. Customers tab
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path

ADB = r"C:\Users\admin\AppData\Local\Android\Sdk\platform-tools\adb.exe"
PKG = "com.umoja.umoja_field"
ACTIVITY = ".MainActivity"
OUT = Path(__file__).resolve().parents[1] / "docs" / "screens"
OUT.mkdir(parents=True, exist_ok=True)


def adb(*args: str) -> str:
    cp = subprocess.run([ADB, *args], capture_output=True, text=True, check=False)
    return cp.stdout + cp.stderr


def screencap(slug: str) -> None:
    out = OUT / f"mobile_{slug}.png"
    cp = subprocess.run(
        [ADB, "exec-out", "screencap", "-p"], capture_output=True, check=True
    )
    out.write_bytes(cp.stdout)
    print(f"  ✓ {out.name}  ({len(cp.stdout) // 1024} KB)")


def tap(x: int, y: int) -> None:
    adb("shell", "input", "tap", str(x), str(y))


def type_text(text: str) -> None:
    safe = text.replace(" ", "%s").replace("&", "\\&")
    adb("shell", "input", "text", safe)


def keyevent(code: str) -> None:
    adb("shell", "input", "keyevent", code)


def get_size() -> tuple[int, int]:
    out = adb("shell", "wm", "size")
    for line in out.splitlines():
        if "size:" in line:
            wh = line.split(":")[-1].strip()
            w, h = wh.split("x")
            return int(w), int(h)
    return 1080, 2400


def dismiss_anrs() -> None:
    """Tap 'Wait' on any ANR dialog and back-press to clear system overlays.
    Repeat a few times because dialogs can stack.
    """
    for _ in range(5):
        # ANR dialog "Wait" sits roughly at y = 0.59*h on a 1080x2400 screen
        adb("shell", "input", "tap", "540", "1395")
        time.sleep(0.4)
    keyevent("KEYCODE_BACK")
    time.sleep(0.5)


def current_focus() -> str:
    out = adb("shell", "dumpsys", "window", "windows")
    for line in out.splitlines():
        if "mCurrentFocus" in line:
            return line.strip()
    return ""


def wait_for_app(timeout: float = 30) -> bool:
    """Wait until our app's MainActivity is the focused window."""
    end = time.time() + timeout
    while time.time() < end:
        f = current_focus()
        if PKG in f and "MainActivity" in f:
            return True
        if "isn%27t responding" in f or "isn't responding" in f or "AppErrorDialog" in f:
            dismiss_anrs()
        time.sleep(1)
    return False


def main() -> None:
    w, h = get_size()
    print(f"device size: {w}x{h}")

    print("clear any stray ANRs and stop the app")
    dismiss_anrs()
    adb("shell", "am", "force-stop", PKG)
    time.sleep(2)

    print("launch the app")
    adb("shell", "monkey", "-p", PKG, "-c", "android.intent.category.LAUNCHER", "1")
    if not wait_for_app(timeout=30):
        print("  WARN: app didn't focus, capturing anyway")
    # Extra settle for Flutter first-frame + secure storage migration
    time.sleep(8)
    dismiss_anrs()
    time.sleep(3)
    screencap("01_login")

    # Tap username field
    tap(w // 2, int(h * 0.42))
    time.sleep(1)
    type_text("umoja")
    time.sleep(1)
    # Tap password field
    tap(w // 2, int(h * 0.52))
    time.sleep(1)
    type_text("Umoja@2026")
    time.sleep(1)
    keyevent("KEYCODE_BACK")  # close keyboard
    time.sleep(2)
    screencap("02_login_filled")

    # Tap Sign in button (~ 62%)
    tap(w // 2, int(h * 0.62))
    print("waiting for home shell…")
    time.sleep(10)
    dismiss_anrs()
    time.sleep(3)
    screencap("03_home_pos")

    # Bottom nav y at ~95% of height, 4 tabs
    nav_y = int(h * 0.95)
    nav_xs = [int(w * 0.125), int(w * 0.375), int(w * 0.625), int(w * 0.875)]

    tap(nav_xs[1], nav_y); time.sleep(3); dismiss_anrs(); time.sleep(2); screencap("04_stock")
    tap(nav_xs[2], nav_y); time.sleep(3); dismiss_anrs(); time.sleep(2); screencap("05_quotes")
    tap(nav_xs[3], nav_y); time.sleep(3); dismiss_anrs(); time.sleep(2); screencap("06_customers")
    tap(nav_xs[0], nav_y); time.sleep(3); dismiss_anrs(); time.sleep(2); screencap("07_pos_active")

    print("done")


if __name__ == "__main__":
    main()
