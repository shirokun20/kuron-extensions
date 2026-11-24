#!/usr/bin/env python3
"""Validate every config/*/*-config.json against schema/source-config.schema.json.

Usage::

    python3 scripts/validate_schema.py                 # all configs, table + summary
    python3 scripts/validate_schema.py --json          # machine-readable report
    python3 scripts/validate_schema.py --verbose       # one line per config
    python3 scripts/validate_schema.py path/a.json ... # explicit files
    python3 scripts/validate_schema.py --engine builtin|jsonschema|auto

Exit code: 0 when every file validates, 1 when any file fails or is unreadable,
2 when the schema itself cannot be loaded.

Engines
-------
`auto` (default) uses the third-party ``jsonschema`` package when it is
installed (full Draft 2020-12 compliance, the CI path) and otherwise falls
back to a self-contained validator covering the JSON Schema keywords this
schema uses. CI installs ``jsonschema`` and therefore always runs the
reference engine; local machines with only the Python stdlib still get the
same pass/fail verdict for this schema. ``--engine`` pins one engine.

Refs #62.
"""

import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA_PATH = os.path.join(ROOT, "schema", "source-config.schema.json")
CONFIG_GLOB = os.path.join(ROOT, "config", "*", "*-config.json")

_TYPE_CHECKS = {
    "object": lambda v: isinstance(v, dict),
    "array": lambda v: isinstance(v, list),
    "string": lambda v: isinstance(v, str),
    "boolean": lambda v: isinstance(v, bool),
    "null": lambda v: v is None,
    "integer": lambda v: isinstance(v, int) and not isinstance(v, bool),
    "number": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
}


class LocalValidator:
    """Stdlib JSON Schema subset used when ``jsonschema`` is unavailable.

    Supports the keywords present in schema/source-config.schema.json:
    $ref/$defs, type, required, properties, patternProperties,
    additionalProperties, items, enum, const, pattern, minLength,
    maxLength, minimum, maximum, exclusiveMinimum, exclusiveMaximum,
    minItems, maxItems, uniqueItems, allOf, anyOf, oneOf, not.
    """

    def __init__(self, schema):
        self.root = schema

    def iter_errors(self, instance, schema=None):
        yield from self._validate(instance, self.root if schema is None else schema, "")

    def _resolve(self, ref):
        if not ref.startswith("#/"):
            raise ValueError("unsupported $ref %r" % ref)
        node = self.root
        for part in ref[2:].split("/"):
            part = part.replace("~1", "/").replace("~0", "~")
            node = node[part]
        return node

    def _validate(self, inst, schema, path):
        if schema is True:
            return
        if schema is False:
            yield "%s: schema is false (no value allowed)" % (path or "/")
            return
        if "$ref" in schema:
            yield from self._validate(inst, self._resolve(schema["$ref"]), path)
            return

        where = path or "/"
        expected = schema.get("type")
        if expected is not None:
            types = expected if isinstance(expected, list) else [expected]
            if not any(_TYPE_CHECKS[t](inst) for t in types):
                yield "%s: expected %s, got %s" % (
                    where, "/".join(types), _json_type(inst))
                return

        if "const" in schema and inst != schema["const"]:
            yield "%s: must equal %r, got %r" % (where, schema["const"], inst)
        if "enum" in schema and inst not in schema["enum"]:
            yield "%s: %r not in %r" % (where, inst, schema["enum"])

        if isinstance(inst, str):
            pattern = schema.get("pattern")
            if pattern is not None and re.search(pattern, inst) is None:
                yield "%s: %r does not match pattern %r" % (where, inst, pattern)
            if "minLength" in schema and len(inst) < schema["minLength"]:
                yield "%s: string shorter than %d" % (where, schema["minLength"])
            if "maxLength" in schema and len(inst) > schema["maxLength"]:
                yield "%s: string longer than %d" % (where, schema["maxLength"])

        if isinstance(inst, (int, float)) and not isinstance(inst, bool):
            if "minimum" in schema and inst < schema["minimum"]:
                yield "%s: %r < minimum %r" % (where, inst, schema["minimum"])
            if "maximum" in schema and inst > schema["maximum"]:
                yield "%s: %r > maximum %r" % (where, inst, schema["maximum"])
            if "exclusiveMinimum" in schema and inst <= schema["exclusiveMinimum"]:
                yield "%s: %r <= exclusiveMinimum %r" % (
                    where, inst, schema["exclusiveMinimum"])
            if "exclusiveMaximum" in schema and inst >= schema["exclusiveMaximum"]:
                yield "%s: %r >= exclusiveMaximum %r" % (
                    where, inst, schema["exclusiveMaximum"])

        if isinstance(inst, list):
            if "minItems" in schema and len(inst) < schema["minItems"]:
                yield "%s: fewer than %d items" % (where, schema["minItems"])
            if "maxItems" in schema and len(inst) > schema["maxItems"]:
                yield "%s: more than %d items" % (where, schema["maxItems"])
            if schema.get("uniqueItems"):
                seen = []
                for item in inst:
                    if item in seen:
                        yield "%s: duplicate item %r" % (where, item)
                        break
                    seen.append(item)
            items = schema.get("items")
            if isinstance(items, dict):
                for idx, item in enumerate(inst):
                    yield from self._validate(item, items, "%s[%d]" % (path, idx))

        if isinstance(inst, dict):
            for key in schema.get("required", []):
                if key not in inst:
                    yield "%s: missing required property %r" % (where, key)
            props = schema.get("properties") or {}
            patterns = schema.get("patternProperties") or {}
            extra = schema.get("additionalProperties", True)
            for key, value in inst.items():
                child = "%s/%s" % (path, key)
                matched = False
                if key in props:
                    matched = True
                    yield from self._validate(value, props[key], child)
                for pattern, sub in patterns.items():
                    if re.search(pattern, key):
                        matched = True
                        yield from self._validate(value, sub, child)
                if matched:
                    continue
                if extra is False:
                    yield "%s: unexpected property %r" % (where, key)
                elif isinstance(extra, dict):
                    yield from self._validate(value, extra, child)

        for sub in schema.get("allOf", []):
            yield from self._validate(inst, sub, path)

        keyword = "anyOf" if "anyOf" in schema else (
            "oneOf" if "oneOf" in schema else None)
        if keyword:
            branches = schema[keyword]
            failures = []
            matched = 0
            for branch in branches:
                errs = list(self._validate(inst, branch, path))
                if errs:
                    failures.append(errs)
                else:
                    matched += 1
            if matched == 0:
                detail = "; ".join(errs[0] for errs in failures if errs)
                yield "%s: does not match %s (%s)" % (
                    where, keyword, detail or "no branch")
            elif keyword == "oneOf" and matched > 1:
                yield "%s: matches %d oneOf branches (expected 1)" % (
                    where, matched)

        if "not" in schema:
            if not list(self._validate(inst, schema["not"], path)):
                yield "%s: matches forbidden `not` schema" % where


def _json_type(value):
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if value is None:
        return "null"
    return type(value).__name__


def load_schema(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def make_engine(name, schema):
    """Return (engine_label, callable(instance) -> list[str])."""
    if name in ("auto", "jsonschema"):
        try:
            from jsonschema import Draft202012Validator  # type: ignore
        except ImportError:
            if name == "jsonschema":
                raise SystemExit(
                    "ERROR: --engine jsonschema requested but the `jsonschema` "
                    "package is not installed (python3 -m pip install jsonschema)")
        else:
            validator = Draft202012Validator(schema)
            validator.check_schema(schema)
            return ("jsonschema-%s" % _version("jsonschema"),
                    lambda inst: [
                        "%s: %s" % (_pointer(err.absolute_path), err.message)
                        for err in sorted(
                            validator.iter_errors(inst),
                            key=lambda e: list(e.absolute_path))
                    ])
    local = LocalValidator(schema)
    return ("builtin", lambda inst: list(local.iter_errors(inst)))


def _version(pkg):
    try:
        from importlib.metadata import version
        return version(pkg)
    except Exception:  # pragma: no cover - metadata is best effort
        return "?"


def _pointer(path):
    if not path:
        return "/"
    return "/" + "/".join(str(p) for p in path)


def collect_targets(args):
    if args.paths:
        targets = []
        for item in args.paths:
            matches = sorted(glob.glob(item)) or [item]
            targets.extend(matches)
        return targets
    return sorted(glob.glob(CONFIG_GLOB))


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Validate Kuron source configs against the JSON Schema "
                    "in schema/source-config.schema.json (Refs #62).",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*",
                        help="config files or globs (default: config/*/*-config.json)")
    parser.add_argument("--schema", default=SCHEMA_PATH,
                        help="schema path (default: %(default)s)")
    parser.add_argument("--engine", choices=("auto", "builtin", "jsonschema"),
                        default="auto",
                        help="validation engine; auto prefers the jsonschema package")
    parser.add_argument("--json", action="store_true",
                        help="emit a JSON report instead of a table")
    parser.add_argument("--verbose", action="store_true",
                        help="print one OK line per file")
    parser.add_argument("--max-errors", type=int, default=50,
                        help="cap error lines per file (default: %(default)s)")
    args = parser.parse_args(argv)

    try:
        schema = load_schema(args.schema)
    except (OSError, ValueError) as exc:
        print("ERROR: cannot load schema %s: %s" % (args.schema, exc))
        return 2

    try:
        engine_name, validate = make_engine(args.engine, schema)
    except Exception as exc:  # schema may be invalid for the reference engine
        print("ERROR: schema rejected by %s: %s" % (args.engine, exc))
        return 2

    targets = collect_targets(args)
    results = []
    for path in targets:
        rel = os.path.relpath(path, ROOT) if os.path.isabs(path) else path
        if rel.startswith(".."):
            rel = path
        try:
            with open(path, encoding="utf-8") as fh:
                cfg = json.load(fh)
        except (OSError, ValueError) as exc:
            results.append({"file": rel, "ok": False,
                            "errors": ["unreadable JSON: %s" % exc]})
            continue
        errors = validate(cfg)
        results.append({"file": rel, "ok": not errors,
                        "errors": errors[:args.max_errors]
                        + (["... %d more" % (len(errors) - args.max_errors)]
                           if len(errors) > args.max_errors else [])})

    failed = [r for r in results if not r["ok"]]
    if args.json:
        print(json.dumps({
            "schema": os.path.relpath(args.schema, ROOT),
            "engine": engine_name,
            "total": len(results),
            "passed": len(results) - len(failed),
            "failed": len(failed),
            "results": results,
        }, indent=2, ensure_ascii=False))
    else:
        for row in results:
            if not row["ok"]:
                print("✖️  %s" % row["file"])
                for err in row["errors"]:
                    print("      %s" % err)
            elif args.verbose:
                print("✅ %s" % row["file"])
        print("%s  %d/%d config valid against %s (engine: %s)" % (
            "✅" if not failed else "✖️",
            len(results) - len(failed), len(results),
            os.path.relpath(args.schema, ROOT), engine_name))
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
