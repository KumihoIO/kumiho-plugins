"""Build a self-contained, deterministic Gemini gallery ZIP from the CLI subtree."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import tempfile
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED


def package(source, output):
    source, output = Path(source).resolve(), Path(output).resolve()
    if output.is_relative_to(source):
        raise ValueError("The output archive must be outside the extension source.")
    manifest = json.loads((source / "gemini-extension.json").read_text(encoding="utf-8"))
    version = manifest.get("version") if isinstance(manifest, dict) else None
    if not isinstance(version, str) or not version.strip():
        raise ValueError("The extension manifest needs a version.")
    files = []
    # Validate before touching an existing release, including linked directories
    # that rglob would otherwise skip instead of rejecting.
    for file in sorted(source.rglob("*"), key=lambda path: (
            path.relative_to(source).as_posix().lower(),
            path.relative_to(source).as_posix())):
        relative = file.relative_to(source)
        if (file.is_symlink() or getattr(file, "is_junction", lambda: False)()
                or any(p.startswith(".") or p == "__pycache__" for p in relative.parts)):
            raise ValueError(f"Unexpected package path: {relative}")
        if file.is_file():
            files.append((file, relative))

    output.parent.mkdir(parents=True, exist_ok=True)
    fd, staging = tempfile.mkstemp(prefix=f".{output.name}.", suffix=".tmp", dir=output.parent)
    os.close(fd)
    try:
        with ZipFile(staging, "w", compression=ZIP_DEFLATED) as archive:
            for file, relative in files:
                info = ZipInfo(relative.as_posix(), date_time=(2026, 10, 14, 0, 0, 0))
                # Preserve the published Windows-built ZIP format on every host.
                # ZipInfo otherwise chooses create_system from the build OS.
                info.create_system = 0
                info.compress_type = ZIP_DEFLATED
                info.external_attr = 0o100644 << 16
                archive.writestr(info, file.read_bytes())
        digest = hashlib.sha256(Path(staging).read_bytes()).hexdigest()
        os.replace(staging, output)
    finally:
        Path(staging).unlink(missing_ok=True)
    return {"version": version, "sha256": digest}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=Path(__file__).resolve().parents[1] / "cli")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(package(args.source, args.out)))
