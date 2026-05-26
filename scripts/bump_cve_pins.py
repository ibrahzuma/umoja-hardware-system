"""Update requirements.txt pins to the minimum versions that close known CVEs
reported by `pip-audit -r requirements.txt`.

Sources the fix map from the pip-audit columns output captured manually. Run
again after a future audit to refresh the map.
"""

from __future__ import annotations

import re
from pathlib import Path

REQUIREMENTS = Path(__file__).resolve().parents[1] / "requirements.txt"

# package name (lower-case) -> minimum version that closes the highest-priority
# CVE for that package per pip-audit. We use `==` pins (matching the rest of
# the file) and let pip resolve.
FIXES: dict[str, str] = {
    "aiohttp": "3.13.4",
    "cbor2": "5.9.0",
    "cryptography": "46.0.7",
    "django": "6.0.5",
    "django-allauth": "65.14.1",
    "starlette": "1.0.1",
    "idna": "3.15",
    "lxml": "6.1.0",
    "nicegui": "3.12.0",
    "pygments": "2.20.0",
    "orjson": "3.11.6",
    "pillow": "12.2.0",
    "pyasn1": "0.6.3",
    "pyjwt": "2.12.0",
    "pynacl": "1.6.2",
    "pyopenssl": "26.0.0",
    "pypdf": "6.10.2",
    "python-dotenv": "1.2.2",
    "python-multipart": "0.0.27",
    "requests": "2.33.0",
    "urllib3": "2.7.0",
    "twisted": "26.4.0",
    "ujson": "5.12.1",
}


def main() -> None:
    text = REQUIREMENTS.read_text(encoding="utf-8")
    lines_out: list[str] = []
    changed: list[tuple[str, str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            lines_out.append(raw)
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(==|>=)\s*([^\s;]+)\s*$", line)
        if not m:
            lines_out.append(raw)
            continue
        name, op, ver = m.group(1), m.group(2), m.group(3)
        key = name.lower()
        if key in FIXES:
            new_ver = FIXES[key]
            new_line = f"{name}=={new_ver}"
            lines_out.append(new_line)
            changed.append((name, ver, new_ver))
        else:
            lines_out.append(raw)
    new_text = "\n".join(lines_out) + "\n"
    REQUIREMENTS.write_text(new_text, encoding="utf-8")
    for name, old, new in changed:
        print(f"  {name:<22} {old:<10} -> {new}")
    print(f"\n{len(changed)} pin(s) bumped.")


if __name__ == "__main__":
    main()
