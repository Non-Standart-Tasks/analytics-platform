#!/usr/bin/env python3
"""Подписи кодов system.warning.shown в SQL панелей Grafana.

Источник подписей — раздел «Подписи на дашбордах» в docs/warning-codes.md.
Скрипт переписывает CASE между маркерами в SQL панелей:

    /* warning-labels:w.data */ CASE w.data WHEN '…' THEN '…' … ELSE w.data END /* /warning-labels */

Без аргументов — перегенерирует панели в grafana/dashboards/.
--check — ничего не пишет, падает, если панели расходятся с таблицей подписей.
--codes PATH — вместе с --check сверяет коды из system_warnings.py бэкенда:
у каждого должна быть строка в реестре и подпись.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DOC = ROOT / "docs" / "warning-codes.md"
DASHBOARDS = ROOT / "grafana" / "dashboards"
LABELS_HEADING = "## Подписи на дашбордах"

MARKER = re.compile(
    r"/\* warning-labels:(?P<col>[A-Za-z_][A-Za-z0-9_.]*) \*/ (?P<body>.*?) /\* /warning-labels \*/"
)
CODE = r"[a-z0-9_]+\.[a-z0-9_.]+"
ROW = re.compile(rf"^\| `(?P<code>{CODE})` *\|(?P<rest>.*)$")
CODE_CELL = re.compile(r"^\| `[^`]*` *\|")
PY_CODE = re.compile(rf'^[A-Z][A-Z0-9_]*\s*(?::[^=]+)?=\s*\(?\s*"(?P<code>{CODE})"', re.M)


def read_doc() -> tuple[dict[str, str], set[str]]:
    """Подписи из раздела подписей и коды из таблиц реестра (остальной документ)."""
    text = DOC.read_text(encoding="utf-8")
    start = text.index(LABELS_HEADING)
    end = text.find("\n## ", start + len(LABELS_HEADING))
    section = text[start : end if end != -1 else len(text)]
    reference = text[:start] + (text[end:] if end != -1 else "")

    labels: dict[str, str] = {}
    for line in section.splitlines():
        m = ROW.match(line)
        if not m:
            if CODE_CELL.match(line):
                raise SystemExit(f"не разобран код в строке подписей: {line}")
            continue
        label = m.group("rest").strip().rstrip("|").strip()
        if not label or re.search(r"['\"\\$]|\[\[", label):
            raise SystemExit(f"{m.group('code')}: пустая подпись или запрещённый символ: {label!r}")
        if label in labels.values():
            raise SystemExit(f"{m.group('code')}: подпись {label!r} уже есть у другого кода")
        if m.group("code") in labels:
            raise SystemExit(f"{m.group('code')}: подпись задана дважды")
        labels[m.group("code")] = label

    documented = {m.group("code") for line in reference.splitlines() if (m := ROW.match(line))}
    return labels, documented


def case_sql(col: str, labels: dict[str, str]) -> str:
    whens = " ".join(f"WHEN '{code}' THEN '{label}'" for code, label in labels.items())
    return f"CASE {col} {whens} ELSE {col} END"


def render(raw: str, labels: dict[str, str]) -> tuple[str, int]:
    """Переписывает CASE между маркерами прямо в тексте JSON (форматирование файла не трогаем)."""

    def repl(m: re.Match[str]) -> str:
        body = json.dumps(case_sql(m.group("col"), labels), ensure_ascii=False)[1:-1]
        return f"/* warning-labels:{m.group('col')} */ {body} /* /warning-labels */"

    return MARKER.subn(repl, raw)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="не писать, только проверить")
    parser.add_argument("--codes", type=Path, help="путь к system_warnings.py бэкенда")
    args = parser.parse_args()

    labels, documented = read_doc()
    problems: list[str] = []

    for code in sorted(set(labels) - documented):
        problems.append(f"{code}: есть подпись, но нет строки в реестре")
    for code in sorted(documented - set(labels)):
        problems.append(f"{code}: есть в реестре, но нет подписи")

    if args.codes:
        backend = set(PY_CODE.findall(args.codes.read_text(encoding="utf-8")))
        if not backend:
            problems.append(f"{args.codes}: не найдено ни одного кода")
        for code in sorted(backend - set(labels)):
            problems.append(f"{code}: есть в system_warnings.py, но нет подписи")
        for code in sorted(backend - documented):
            problems.append(f"{code}: есть в system_warnings.py, но нет строки в реестре")

    total = 0
    for path in sorted(DASHBOARDS.rglob("*.json")):
        raw = path.read_text(encoding="utf-8")
        new, n = render(raw, labels)
        total += n
        if new == raw:
            continue
        json.loads(new)  # не сломали JSON
        if args.check:
            problems.append(f"{path.relative_to(ROOT)}: подписи устарели, запустите скрипт")
        else:
            path.write_text(new, encoding="utf-8")
            print(f"обновлён {path.relative_to(ROOT)} ({n} блоков)")
    if total == 0:
        problems.append("в панелях не найдено ни одного маркера warning-labels")

    if problems:
        print("\n".join(problems), file=sys.stderr)
        return 1
    print(f"ок: {len(labels)} подписей, {total} блоков в панелях")
    return 0


if __name__ == "__main__":
    sys.exit(main())
