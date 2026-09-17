#!/usr/bin/env python3
"""Build a claude.ai-uploadable ZIP of one skill from the hosted directory bundle.

claude.ai (Customize > Skills > Upload a skill) expects a ZIP whose root holds a
single folder named after the skill, containing SKILL.md. ChatGPT/Codex metadata
(agents/openai.yaml) is left out.

The archive is deterministic: sorted entries, fixed timestamps and permissions,
LF line endings and no compression. Its SHA-256 changes only when the packaged
files change, whatever the OS, Python version or git line-ending setting.

    python cloud-mcp/directory/scripts/package_web_skill.py [--skill NAME] [--out DIR]

Standard library only. Writes <out>/<skill>-skill.zip and prints its path and
SHA-256 as JSON.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import zipfile
from pathlib import Path

DIRECTORY = Path(__file__).resolve().parents[1]
SKILLS = DIRECTORY / "kumiho-memory" / "skills"
DEFAULT_OUT = DIRECTORY / "dist"

# agents/ holds ChatGPT/Codex metadata; the rest is build or editor residue.
EXCLUDED_PARTS = {"agents", "__pycache__"}
EXCLUDED_SUFFIXES = {".pyc", ".pyo"}
TEXT_SUFFIXES = {".md", ".py", ".txt", ".json", ".yaml", ".yml", ".sh"}

FIXED_TIME = (1980, 1, 1, 0, 0, 0)
NAME_PATTERN = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")
NAME_MAX = 64
# The Agent Skills spec allows 1,024 characters, but claude.ai's custom-skill
# guide caps an uploaded skill's description at 200. Uploads need the stricter one.
DESCRIPTION_MAX = 200
LINES_MAX = 500
FRONTMATTER_KEYS = {"name", "description"}
# A YAML plain scalar that starts with one of these, or contains ": " or " #",
# would not read back as the literal text.
YAML_SPECIAL_START = tuple("'\"[]{}>|*&!%@,`#?-:")


def frontmatter(text):
    lines = text.split("\n")
    if not lines or lines[0] != "---":
        raise ValueError("SKILL.md must start with a --- frontmatter block")
    if "---" not in lines[1:]:
        raise ValueError("SKILL.md frontmatter block is not closed")
    meta = {}
    for line in lines[1:lines.index("---", 1)]:
        key, sep, value = line.partition(": ")
        if not sep or not key or key != key.strip() or key in meta:
            raise ValueError(f"unsupported frontmatter line: {line!r}")
        if value.startswith(YAML_SPECIAL_START) or ": " in value or " #" in value:
            raise ValueError(f"frontmatter value for {key!r} must be a plain one-line scalar")
        meta[key] = value.strip()
    return meta


def validate(skill, text):
    """Return the problems that would stop a claude.ai upload or break the spec."""
    try:
        meta = frontmatter(text)
    except ValueError as exc:
        return [str(exc)]
    problems = []
    if set(meta) != FRONTMATTER_KEYS:
        problems.append(f"frontmatter keys must be exactly name and description, got {sorted(meta)}")
    name = meta.get("name", "")
    if name != skill:
        problems.append(f"name {name!r} does not match the folder {skill!r}")
    if not NAME_PATTERN.fullmatch(name) or len(name) > NAME_MAX:
        problems.append(f"name {name!r} must be lowercase letters, digits and hyphens, at most {NAME_MAX}")
    description = meta.get("description", "")
    if not description or len(description) > DESCRIPTION_MAX:
        problems.append(f"description must be 1-{DESCRIPTION_MAX} characters, got {len(description)}")
    if "<" in description or ">" in description:
        problems.append("description must not contain angle brackets")
    if len(text.splitlines()) > LINES_MAX:
        problems.append(f"SKILL.md must stay under {LINES_MAX} lines")
    return problems


def skill_entries(skill_dir):
    entries = []
    for path in skill_dir.rglob("*"):
        rel = path.relative_to(skill_dir)
        if any(part in EXCLUDED_PARTS or part.startswith(".") for part in rel.parts):
            continue
        if path.is_symlink():
            raise ValueError(f"refusing to package a symlink: {rel.as_posix()}")
        if path.is_dir() or path.suffix in EXCLUDED_SUFFIXES:
            continue
        data = path.read_bytes()
        if path.suffix.lower() in TEXT_SUFFIXES:
            data = data.replace(b"\r\n", b"\n")
        entries.append((rel.as_posix(), data))
    return sorted(entries)


def _info(name, mode):
    info = zipfile.ZipInfo(name, date_time=FIXED_TIME)
    info.create_system = 3  # Unix, so permissions read the same everywhere
    info.external_attr = mode << 16
    if name.endswith("/"):
        info.external_attr |= 0x10
    info.compress_type = zipfile.ZIP_STORED
    return info


def build(skill, out_dir):
    skill_dir = SKILLS / skill
    entries = skill_entries(skill_dir) if skill_dir.is_dir() else []
    files = dict(entries)
    if "SKILL.md" not in files:
        raise ValueError(f"no SKILL.md for skill {skill!r} under {SKILLS}")
    problems = validate(skill, files["SKILL.md"].decode("utf-8"))
    if problems:
        raise ValueError("; ".join(problems))

    folders = {f"{skill}/"}
    for rel, _ in entries:
        parts = rel.split("/")[:-1]
        folders.update(f"{skill}/{'/'.join(parts[:i])}/" for i in range(1, len(parts) + 1))

    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"{skill}-skill.zip"
    partial = out.with_name(out.name + ".partial")
    with zipfile.ZipFile(partial, "w", zipfile.ZIP_STORED) as archive:
        for folder in sorted(folders):
            archive.writestr(_info(folder, 0o40755), b"")
        for rel, data in entries:
            archive.writestr(_info(f"{skill}/{rel}", 0o100644), data)
    partial.replace(out)
    return out


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--skill", default="kumiho-memory")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    try:
        out = build(args.skill, args.out)
    except (ValueError, OSError) as exc:
        print(f"package_web_skill: {exc}", file=sys.stderr)
        return 2
    with zipfile.ZipFile(out) as archive:
        names = archive.namelist()
    print(json.dumps({
        "zip": str(out.resolve()),
        "sha256": hashlib.sha256(out.read_bytes()).hexdigest(),
        "bytes": out.stat().st_size,
        "entries": names,
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
