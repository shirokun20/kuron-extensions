#!/usr/bin/env python3
"""Render body issue GitHub dari issue form repo (.github/ISSUE_TEMPLATE/*.yml).

Form GitHub tidak bisa diisi lewat `gh issue create`, jadi script ini membaca
field form apa adanya (id, label, opsi dropdown, field wajib) lalu merakit
markdown yang persis sama dengan yang dilihat manusia saat mengisi form.

python3 scripts/issue_body.py --list
python3 scripts/issue_body.py --check
python3 scripts/issue_body.py --template .github/ISSUE_TEMPLATE/config-bug-id.yml \
    --set source=komiku --set path=config/id/komiku-config.json \
    --set baseurl=https://komiku.org --set version=1.0.1 \
    --set kolom="Search" --set kolom="Search + Pag + Total" \
    --set status="✖️ tidak ada / route tidak ditemukan" \
    --set jenis="Pagination rusak / halaman berikutnya kosong" \
    --set diharapkan="..." --set aktual="..." --set bukti="..." \
    --set title="Search tidak support" --body-only

Nilai juga boleh dari file JSON: --values vals.json ({"source": "komiku", "kolom": ["Search"]}).

Stdlib saja; parser YAML-nya sengaja hanya mencakup subset yang dipakai issue form
(peta/list bersarang, quoted string, inline list, block scalar `|`).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATE_DIR = REPO_ROOT / ".github" / "ISSUE_TEMPLATE"
REPO = "shirokun20/kuron-extensions"

# (jenis, bahasa) -> nama file form. Satu issue = satu jenis + satu bahasa.
FORMS = {
    ("bug", "id"): "config-bug-id.yml",
    ("bug", "en"): "config-bug-en.yml",
    ("bug", "zh"): "config-bug-zh.yml",
    ("feature", "id"): "feature-id.yml",
    ("feature", "en"): "feature-en.yml",
    ("feature", "zh"): "feature-zh.yml",
}

# Kolom fitur harus persis sama di ketiga bahasa (AGENTS.md Rules: issue #3).
FEATURE_COLUMNS = [
    "Home",
    "Home + Pag + Total",
    "Search",
    "Search + Pag + Total",
    "Detail + Chapters",
    "Reader (image/video)",
    "Genre + Pag",
    "Tag + Pag",
    "Author + Pag",
    "Artist + Pag",
]

FEATURE_DROPDOWN_IDS = {"area", "impact"}


class TemplateError(Exception):
    """Template rusak atau nilai tidak cocok dengan opsi form."""


# --------------------------------------------------------------------------- #
# YAML subset parser
# --------------------------------------------------------------------------- #

_PAIR = re.compile(r"^([A-Za-z0-9_.$-]+)\s*:(.*)$")
_BLOCK_MARKERS = ("|", "|-", "|+", ">", ">-", ">+")
_TRUE = {"true", "yes"}
_FALSE = {"false", "no"}
_NULL = {"null", "~", ""}


def _split_flow(text: str) -> list[str]:
    """Pecah isi `[a, "b, c"]` di koma yang di luar tanda kutip."""
    parts, buf, quote = [], [], ""
    for ch in text:
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
        elif ch in "\"'":
            quote = ch
            buf.append(ch)
        elif ch == ",":
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf))
    return [p.strip() for p in parts]


def _scalar(token: str):
    token = token.strip()
    if len(token) >= 2 and token[0] == token[-1] and token[0] in "\"'":
        return token[1:-1]
    if token.startswith("[") and token.endswith("]"):
        return [_scalar(p) for p in _split_flow(token[1:-1])]
    low = token.lower()
    if low in _TRUE:
        return True
    if low in _FALSE:
        return False
    if low in _NULL:
        return None
    return token


class _Reader:
    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.i = 0

    def peek(self):
        """(indent, isi) baris berikutnya yang bukan kosong/komentar, atau (None, None)."""
        while self.i < len(self.lines):
            line = self.lines[self.i]
            stripped = line.strip()
            if not stripped or stripped.startswith("#"):
                self.i += 1
                continue
            return len(line) - len(line.lstrip(" ")), stripped
        return None, None

    def parse(self, indent: int):
        found, text = self.peek()
        if found is None or found < indent:
            return None
        return self._parse_list(found) if text.startswith("- ") else self._parse_map(found)

    def _parse_list(self, indent: int) -> list:
        items = []
        while True:
            found, text = self.peek()
            if found is None or found != indent or not text.startswith("- "):
                return items
            self.i += 1
            rest = text[2:].strip()
            if not rest:
                items.append(self.parse(indent + 1))
            elif _PAIR.match(rest):
                # `- key: value` -> item peta; sisa key-nya ada di indent +2.
                self.lines[self.i - 1] = " " * (indent + 2) + rest
                self.i -= 1
                items.append(self._parse_map(indent + 2))
            else:
                items.append(_scalar(rest))

    def _parse_map(self, indent: int) -> dict:
        out: dict = {}
        while True:
            found, text = self.peek()
            if found is None or found != indent or text.startswith("- "):
                return out
            match = _PAIR.match(text)
            if not match:
                raise TemplateError(f"baris tidak bisa diparse: {text!r}")
            key, raw = match.group(1), match.group(2).strip()
            self.i += 1
            if raw in _BLOCK_MARKERS:
                out[key] = self._block(indent)
            elif raw == "":
                nxt, _ = self.peek()
                out[key] = self.parse(nxt) if nxt is not None and nxt > indent else None
            else:
                out[key] = _scalar(raw)

    def _block(self, indent: int) -> str:
        lines: list[str] = []
        base = None
        while self.i < len(self.lines):
            line = self.lines[self.i]
            if not line.strip():
                lines.append("")
                self.i += 1
                continue
            found = len(line) - len(line.lstrip(" "))
            if found <= indent:
                break
            base = found if base is None else base
            lines.append(line[base:])
            self.i += 1
        while lines and lines[-1] == "":
            lines.pop()
        return "\n".join(lines) + "\n"


def load_template(path: Path) -> dict:
    data = _Reader(path.read_text(encoding="utf-8")).parse(0)
    if not isinstance(data, dict) or "body" not in data:
        raise TemplateError(f"{path.name}: tidak punya key `body`")
    return data


# --------------------------------------------------------------------------- #
# Field & render
# --------------------------------------------------------------------------- #


def fields_of(template: dict) -> list[dict]:
    """Field yang bisa diisi user (markdown intro dilewati).

    Opsi dropdown = list teks; opsi checkboxes = list dict `label`/`required`,
    dinormalkan ke list label supaya bisa dibandingkan antar bahasa.
    """
    fields = []
    for item in template["body"]:
        if item.get("type") == "markdown":
            continue
        attributes = item.get("attributes") or {}
        validations = item.get("validations") or {}
        raw_options = attributes.get("options")
        if raw_options is None:
            raw_options = item.get("options") or []
        options = [
            str(o["label"]) if isinstance(o, dict) else str(o) for o in raw_options
        ]
        fields.append(
            {
                "id": item.get("id"),
                "type": item["type"],
                "label": attributes.get("label", item.get("id", "")),
                # Checkbox bisa dicentang banyak; atributnya tidak punya `multiple`.
                "multiple": item["type"] == "checkboxes" or bool(attributes.get("multiple")),
                "options": options,
                "required": bool(validations.get("required")),
                "required_options": [
                    str(o["label"]) for o in raw_options if isinstance(o, dict) and o.get("required")
                ],
            }
        )
    return fields


def kind_of(form_name: str) -> str:
    return "bug" if form_name.startswith("config-bug-") else "feature"


def resolve_values(form_name: str, fields: list[dict], values: dict) -> dict:
    """Petakan id kanonik (bahasa-independent) ke id field milik form itu.

    Form 3 bahasa memakai id berbeda (`kolom`/`column`, `cek`/`checks`), jadi
    perintah agent cukup menyebut nama kanonik; posisi field tetap dijaga oleh
    `issue_body.py --check`.
    """
    canonical = CANONICAL[kind_of(form_name)]
    ids = [f["id"] for f in fields]
    alias = dict(zip(canonical, ids))
    known = set(ids) | set(alias)
    resolved, problems = {}, []
    for key, value in values.items():
        target = alias.get(key, key)
        if target not in known:
            problems.append(f"id field tidak dikenal: {key}")
        elif target in resolved:
            problems.append(f"id {key} diisi dua kali (setara id {target})")
        else:
            resolved[target] = value
    if problems:
        raise TemplateError(
            "; ".join(problems)
            + "\nid kanonik: " + ", ".join(canonical)
            + "\nid form ini: " + ", ".join(ids)
        )
    return resolved



CANONICAL = {
    "bug": ["source", "path", "baseurl", "version", "kolom", "status", "jenis",
            "diharapkan", "aktual", "bukti", "fix", "cek"],
    "feature": ["area", "problem", "proposal", "alternative", "impact", "example", "checks"],
}


def _items(field: dict, values: dict) -> list[str]:
    raw = values.get(field["id"])
    if raw is None:
        return []
    raw = raw if isinstance(raw, list) else [raw]
    return [str(i).strip() for i in raw if str(i).strip()]


def validate(fields: list[dict], values: dict) -> list[str]:
    problems = []
    for field in fields:
        items = _items(field, values)
        if not items:
            if field["required"]:
                problems.append(f"field wajib kosong: {field['id']} ({field['label']})")
            continue
        if not field["multiple"] and len(items) > 1:
            problems.append(f"field {field['id']} bukan multi-select, diberi {len(items)} nilai")
        if field["options"]:
            for item in items:
                if item not in field["options"]:
                    problems.append(
                        f"nilai {item!r} tidak ada di opsi {field['id']}\n"
                        f"    opsi: {' | '.join(field['options'])}"
                    )
        if field["type"] == "checkboxes":
            for option in field["required_options"]:
                if option not in items:
                    problems.append(
                        f"checklist wajib belum dicentang: {option!r}\n"
                        f"    isi --set {field['id']}=<label persis di atas>"
                    )
    return problems


def render(fields: list[dict], values: dict) -> str:
    blocks: list[str] = []
    for field in fields:
        items = _items(field, values)
        if field["type"] == "checkboxes":
            checked = set(items)
            rows = [f"- [{'x' if o in checked else ' '}] {o}" for o in field["options"]]
            lines = [f"**{field['label']}**", ""] + rows
        elif not items:
            continue
        elif field["type"] == "dropdown" and field["multiple"]:
            lines = [f"**{field['label']}**", ""] + [f"- {i}" for i in items]
        else:
            lines = [f"**{field['label']}**", "", "\n\n".join(items)]
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks).rstrip() + "\n"



def title_of(template: dict, title: str | None) -> str:
    """Prefix `[bug][id] ` diambil dari form; agent cukup menulis judul intinya."""
    prefix = str(template.get("title") or "").strip()
    title = str(title or "").strip()
    return f"{prefix} {title}".strip() if title else prefix


# --------------------------------------------------------------------------- #
# Mode: --check (sinkron antar template)
# --------------------------------------------------------------------------- #


def check() -> int:
    problems: list[str] = []
    parsed = {name: load_template(TEMPLATE_DIR / name) for name in FORMS.values()}

    for name, template in parsed.items():
        missing = [k for k in ("name", "description", "title", "labels") if k not in template]
        if missing:
            problems.append(f"{name}: key hilang: {', '.join(missing)}")
        if not str(template.get("title", "")).startswith(("[bug]", "[feat]")):
            problems.append(f"{name}: title prefix harus diawali [bug] atau [feat]")
        labels = template.get("labels")
        if not isinstance(labels, list) or not labels:
            problems.append(f"{name}: labels kosong")

    # Bentuk form (tipe + wajib + multi) harus sama di 3 bahasa per jenis; id
    # boleh berbeda antar bahasa karena id itu internal form, dipetakan ke nama kanonik.
    for kind, canonical in CANONICAL.items():
        shapes, requireds = {}, {}
        for lang in ("id", "en", "zh"):
            fields = fields_of(parsed[FORMS[(kind, lang)]])
            if len(fields) != len(canonical):
                problems.append(
                    f"{FORMS[(kind, lang)]}: punya {len(fields)} field, "
                    f"canonical {kind} punya {len(canonical)}"
                )
            shapes[lang] = [(f["type"], f["required"], f["multiple"]) for f in fields]
            requireds[lang] = [c for c, f in zip(canonical, fields) if f["required"]]
        if len({repr(v) for v in shapes.values()}) != 1:
            problems.append(f"form {kind}: bentuk field beda antar bahasa: {shapes}")
        if len({repr(v) for v in requireds.values()}) != 1:
            problems.append(f"form {kind}: field wajib beda antar bahasa: {requireds}")

    # Kolom fitur harus identik persis di 3 bahasa bug (nama kolom = bahasa netral).
    for lang in ("id", "en", "zh"):
        name = FORMS[("bug", lang)]
        fields = fields_of(parsed[name])
        column = next((f for f, c in zip(fields, CANONICAL["bug"]) if c == "kolom"), None)
        if column is None:
            problems.append(f"{name}: dropdown kolom fitur tidak ditemukan")
        elif column["options"] != FEATURE_COLUMNS:
            problems.append(
                f"{name}: opsi kolom fitur tidak sama dengan matrix\n"
                f"    template: {column['options']}\n"
                f"    matrix:   {FEATURE_COLUMNS}"
            )

    # Dropdown status = 4 baris, satu per emoji matrix; jenis masalah = 9 opsi.
    for lang in ("id", "en", "zh"):
        name = FORMS[("bug", lang)]
        fields = fields_of(parsed[name])
        by_canonical = {c: f for f, c in zip(fields, CANONICAL["bug"])}
        status = by_canonical.get("status")
        if status is None:
            problems.append(f"{name}: dropdown status tidak ditemukan")
        else:
            for emoji in ("⚠️", "✖️", "🚫", "✅"):
                if not any(o.startswith(emoji) for o in status["options"]):
                    problems.append(f"{name}: opsi status tanpa emoji {emoji}")
        kind_field = by_canonical.get("jenis")
        if kind_field is None or len(kind_field["options"]) != 9:
            problems.append(f"{name}: dropdown jenis masalah harus punya 9 opsi")

    # Dropdown area & impact wajib ada di ketiga bahasa feature.
    for lang in ("id", "en", "zh"):
        name = FORMS[("feature", lang)]
        fields = fields_of(parsed[name])
        counts = {
            c: len(f["options"])
            for f, c in zip(fields, CANONICAL["feature"])
            if c in FEATURE_DROPDOWN_IDS
        }
        if counts != {"area": 7, "impact": 4}:
            problems.append(f"{name}: opsi area/impact harus 7/4, dapat {counts}")

    # Label per jenis harus sama antar bahasa.
    for kind, expected in (("bug", ["config-verify", "bug"]), ("feature", ["enhancement"])):
        for lang in ("id", "en", "zh"):
            name = FORMS[(kind, lang)]
            if parsed[name].get("labels") != expected:
                problems.append(f"{name}: labels {parsed[name].get('labels')} != {expected}")

    # config.yml: blank issue harus dimatikan.
    chooser = TEMPLATE_DIR / "config.yml"
    if chooser.exists():
        text = chooser.read_text(encoding="utf-8")
        if "blank_issues_enabled: false" not in text:
            problems.append("config.yml: blank_issues_enabled harus false")

    for problem in problems:
        print(f"✗ {problem}")
    if problems:
        print(f"\n{len(problems)} masalah sinkronisasi form issue.")
        return 1
    print(f"✓ {len(FORMS)} form issue sinkron: kolom fitur, id field, label, prefix title.")
    return 0


def listing() -> int:
    print(f"{'jenis':<8} {'bahasa':<7} {'form':<22} {'label':<28} nama")
    for (kind, lang), name in FORMS.items():
        template = load_template(TEMPLATE_DIR / name)
        print(
            f"{kind:<8} {lang:<7} {name:<22} {','.join(template['labels']):<28} {template['name']}"
        )
        print(f"{'':39}prefix title: {str(template.get('title', '')).strip()!r}")
        for field, canonical in zip(fields_of(template), CANONICAL[kind]):
            flag = "wajib" if field["required"] else "opsional"
            multi = "multi" if field["multiple"] else "satu"
            opts = f"  opsi: {' | '.join(field['options'])}" if field["options"] else ""
            print(f"{'':39}  --set {canonical}=<{field['type']}, {multi}, {flag}>{opts}")
    return 0


# --------------------------------------------------------------------------- #


def collect_values(args) -> dict:
    values: dict = {}
    if args.values:
        loaded = json.loads(Path(args.values).read_text(encoding="utf-8"))
        if not isinstance(loaded, dict):
            raise TemplateError("--values harus berisi objek JSON {id: nilai}")
        for key, value in loaded.items():
            values[key] = value
    for item in args.set or []:
        if "=" not in item:
            raise TemplateError(f"--set '{item}' harus berbentuk id=nilai")
        key, value = item.split("=", 1)
        key = key.strip()
        if key in values:
            existing = values[key]
            values[key] = (list(existing) if isinstance(existing, list) else [existing]) + [value]
        else:
            values[key] = value
    return values


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--form", metavar="JENIS[:BHS]", help="pilih form: bug|bug:id|bug:en|bug:zh|feature|feature:id|...")
    parser.add_argument("--template", help="path ke form .github/ISSUE_TEMPLATE/<form>.yml")
    parser.add_argument("--set", action="append", metavar="ID=NILAI", help="isi field form (id kanonik); ulangi untuk dropdown multi")
    parser.add_argument("--values", metavar="FILE.json", help="isi field dari file JSON")
    parser.add_argument("--title", help="judul issue tanpa prefix (prefix diambil dari form)")
    parser.add_argument("--body-only", action="store_true", help="cetak markdown body saja")
    parser.add_argument("--json", action="store_true", help="cetak envelope JSON (title/label/body/url form)")
    parser.add_argument("--out", metavar="FILE", help="tulis body ke file (untuk --body-file gh)")
    parser.add_argument("--list", action="store_true", help="daftar form + id field yang bisa diisi")
    parser.add_argument("--check", action="store_true", help="cek sinkronisasi antar form issue")
    args = parser.parse_args(argv)

    if args.list:
        return listing()
    if args.check:
        return check()

    form = args.form or args.template
    if not form:
        parser.error("--form atau --template wajib (lihat --list)")
    if args.form:
        kind, _, lang = args.form.partition(":")
        name = FORMS.get((kind, lang or "id")) or FORMS.get((kind, ""))
        if name is None:
            parser.error(f"form '{args.form}' tidak dikenal (lihat --list)")
        path = TEMPLATE_DIR / name
    else:
        path = Path(args.template)
        if not path.is_absolute():
            candidate = REPO_ROOT / args.template
            path = candidate if candidate.exists() else path
    if not path.exists():
        print(f"✗ template tidak ditemukan: {form}", file=sys.stderr)
        return 2

    values = collect_values(args)
    title = values.pop("title", None) or args.title
    try:
        template = load_template(path)
        fields = fields_of(template)
        resolved = resolve_values(path.name, fields, values)
        problems = validate(fields, resolved)
    except TemplateError as error:
        print(f"✗ {error}", file=sys.stderr)
        return 2
    if problems:
        print(f"✗ {path.name} tidak bisa dirender:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    body = render(fields, resolved)
    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
    if args.body_only:
        sys.stdout.write(body)
        return 0
    envelope = {
        "repo": REPO,
        "form": path.name,
        "title": title_of(template, title),
        "labels": template.get("labels") or [],
        "body": body,
        "form_url": f"https://github.com/{REPO}/issues/new?template={path.stem}",
    }
    if args.json:
        print(json.dumps(envelope, ensure_ascii=False, indent=2))
        return 0
    print(f"# {envelope['title']}")
    print(f"# label: {', '.join(envelope['labels'])}   form: {envelope['form']}")
    print()
    sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
