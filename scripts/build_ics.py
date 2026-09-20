#!/usr/bin/env python3
"""Сборка ICS-календаря дней рождения Genshin Impact.
Python 3.11+, только стандартная библиотека. RFC 5545.
"""
from __future__ import annotations

import json
import re
import sys
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

API_ALL = "https://genshin.jmp.blue/characters/all?lang=en"
OVERRIDES = Path("data/overrides.json")
OUT = Path("docs/genshin-birthdays.ics")

ANCHOR_YEAR = 2020        # високосный — чтобы 29 февраля не сломалось
MIN_EXPECTED = 80         # предохранитель: меньше — считаем выгрузку битой
DTSTAMP = "20200101T000000Z"   # константа, см. раздел «подводные камни»
UA = "genshin-birthday-ics/1.0 (github actions bot)"
BDAY_RE = re.compile(r"^\d{4}-(\d{2})-(\d{2})$")


def fetch_json(url: str):
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def normalize(raw) -> list[dict]:
    """API может отдать dict {id: obj} или list[obj]. Приводим к списку."""
    if isinstance(raw, dict):
        out = []
        for key, value in raw.items():
            if isinstance(value, dict):
                value.setdefault("id", key)
                out.append(value)
        return out
    if isinstance(raw, list):
        return [x for x in raw if isinstance(x, dict)]
    raise TypeError(f"unexpected payload type: {type(raw)!r}")


def make_id(ch: dict) -> str:
    if ch.get("id"):
        return str(ch["id"])
    name = str(ch.get("name", "unknown")).lower()
    return re.sub(r"[^a-z0-9]+", "-", name).strip("-")


def esc(text: str) -> str:
    """Экранирование TEXT-значений по RFC 5545 §3.3.11."""
    return (
        text.replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\n", "\\n")
    )


def fold(line: str) -> str:
    """Свёртка строк до 75 октетов, без разрыва UTF-8 последовательностей."""
    data = line.encode("utf-8")
    if len(data) <= 75:
        return line
    chunks, start, limit = [], 0, 75
    while start < len(data):
        end = min(start + limit, len(data))
        while end < len(data) and (data[end] & 0xC0) == 0x80:
            end -= 1          # не рвём многобайтовый символ
        chunks.append(data[start:end])
        start, limit = end, 74   # у продолжений первый октет — пробел
    return "\r\n ".join(c.decode("utf-8") for c in chunks)


def collect() -> dict[str, dict]:
    people: dict[str, dict] = {}
    try:
        for ch in normalize(fetch_json(API_ALL)):
            people[make_id(ch)] = ch
    except (urllib.error.URLError, TimeoutError, ValueError) as exc:
        print(f"::warning::API недоступен ({exc}), работаем на overrides", file=sys.stderr)

    if OVERRIDES.exists():
        for ch in json.loads(OVERRIDES.read_text("utf-8")):
            people[make_id(ch)] = {**people.get(make_id(ch), {}), **ch}
    return people


def build(people: dict[str, dict]) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//genshin-birthday-ics//EN",
        "CALSCALE:GREGORIAN",
        "X-WR-CALNAME:Genshin Impact Birthdays",
        "X-WR-CALDESC:Character birthdays, auto-generated",
        "REFRESH-INTERVAL;VALUE=DURATION:PT12H",
        "X-PUBLISHED-TTL:PT12H",
    ]
    count = 0
    for cid in sorted(people):
        ch = people[cid]
        bday = str(ch.get("birthday") or "")
        m = BDAY_RE.match(bday)
        if not m:                      # у Путешественника ДР нет — пропускаем
            continue
        month, day = int(m.group(1)), int(m.group(2))
        try:
            start = date(ANCHOR_YEAR, month, day)
        except ValueError:
            print(f"::warning::битая дата у {cid}: {bday}", file=sys.stderr)
            continue
        end = start + timedelta(days=1)

        name = str(ch.get("name") or cid.title())
        descr = " · ".join(
            filter(None, [ch.get("nation"), ch.get("vision"), ch.get("title")])
        )
        lines += [
            "BEGIN:VEVENT",
            f"UID:{cid}@genshin-birthday-ics",
            f"DTSTAMP:{DTSTAMP}",
            f"DTSTART;VALUE=DATE:{start:%Y%m%d}",
            f"DTEND;VALUE=DATE:{end:%Y%m%d}",
            "RRULE:FREQ=YEARLY",
            "TRANSP:TRANSPARENT",
            "CATEGORIES:Genshin Impact",
            f"SUMMARY:🎂 {esc(name)} — Birthday",
        ]
        if descr:
            lines.append(f"DESCRIPTION:{esc(descr)}")
        lines.append("END:VEVENT")
        count += 1

    if count < MIN_EXPECTED:
        sys.exit(f"ОТКАЗ: собрано {count} событий, ожидалось ≥ {MIN_EXPECTED}")

    lines.append("END:VCALENDAR")
    print(f"событий: {count}")
    return "\r\n".join(fold(l) for l in lines) + "\r\n"


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(build(collect()), encoding="utf-8", newline="")
