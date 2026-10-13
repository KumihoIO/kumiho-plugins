"""Build a self-contained, deterministic Gemini gallery ZIP from the CLI subtree."""
from pathlib import Path
import argparse
import hashlib
import json
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

def package(source, output):
    manifest = json.loads((source / "gemini-extension.json").read_text(encoding="utf-8"))
    output.parent.mkdir(parents=True, exist_ok=True)
    with ZipFile(output, "w", compression=ZIP_DEFLATED) as archive:
        for file in sorted(source.rglob("*")):
            if not file.is_file():
                continue
            relative = file.relative_to(source)
            if file.is_symlink() or any(p.startswith(".") or p == "__pycache__" for p in relative.parts):
                raise ValueError(f"Unexpected package file: {relative}")
            info = ZipInfo(relative.as_posix(), date_time=(2026, 10, 14, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, file.read_bytes())
    return {"version": manifest["version"], "sha256": hashlib.sha256(output.read_bytes()).hexdigest()}

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "cli")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.source.resolve(), args.out.resolve())))
