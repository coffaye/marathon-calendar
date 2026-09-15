"""Small independent iCalendar contract validator used by smoke tests.

It intentionally does not import the application's ICS renderer. It checks the
wire representation: CRLF structure, required VEVENT fields, and UID
uniqueness.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _unfold(text: str) -> list[str]:
    physical = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    logical: list[str] = []
    for line in physical:
        if line.startswith((" ", "\t")) and logical:
            logical[-1] += line[1:]
        elif line:
            logical.append(line)
    return logical


def validate_calendar(text: str) -> dict[str, object]:
    lines = _unfold(text)
    if not text.endswith("\r\n"):
        raise ValueError("calendar must end with CRLF")
    if not lines or lines[0] != "BEGIN:VCALENDAR" or lines[-1] != "END:VCALENDAR":
        raise ValueError("invalid VCALENDAR envelope")

    events: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in lines[1:-1]:
        if line == "BEGIN:VEVENT":
            if current is not None:
                raise ValueError("nested VEVENT")
            current = {}
            continue
        if line == "END:VEVENT":
            if current is None:
                raise ValueError("orphan END:VEVENT")
            events.append(current)
            current = None
            continue
        if current is not None and ":" in line:
            key, value = line.split(":", 1)
            current[key.split(";", 1)[0].upper()] = value
    if current is not None:
        raise ValueError("unterminated VEVENT")

    required = {"UID", "SUMMARY", "DTSTART", "DTEND", "STATUS", "SEQUENCE", "LAST-MODIFIED", "DTSTAMP"}
    missing = [sorted(required - set(event)) for event in events if required - set(event)]
    if missing:
        raise ValueError(f"missing required VEVENT properties: {missing[0]}")
    uids = [event["UID"] for event in events]
    if len(uids) != len(set(uids)):
        raise ValueError("duplicate UID")
    if any(not re.fullmatch(r"\d+", event["SEQUENCE"]) for event in events):
        raise ValueError("SEQUENCE must be a non-negative integer")
    return {"events": len(events), "unique_uids": len(uids), "bytes": len(text.encode("utf-8"))}


def main() -> int:
    source = sys.argv[1] if len(sys.argv) > 1 else "-"
    text = sys.stdin.read() if source == "-" else Path(source).read_bytes().decode("utf-8")
    print(json.dumps(validate_calendar(text), ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
