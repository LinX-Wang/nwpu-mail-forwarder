"""Create a source-only archive; never collect runtime data by globbing root."""

from pathlib import Path
import hashlib
import io
import tarfile
import zipfile

root = Path(__file__).resolve().parents[1]
version = "0.1.0"
files = [
    root / name
    for name in (
        "pyproject.toml",
        "requirements.txt",
        "README.md",
        "LICENSE",
        "CONTRIBUTING.md",
        "SECURITY.md",
        ".gitignore",
        ".gitattributes",
    )
]
for folder in ("src", "tests", "deploy", "docs", "scripts", ".github"):
    files.extend(
        p
        for p in (root / folder).rglob("*")
        if p.is_file()
        and "__pycache__" not in p.parts
        and not p.name.endswith((".pyc", ".db", ".sqlite3", ".cred", ".log"))
        and ".egg-info" not in str(p)
    )
dest = root / "dist"
dest.mkdir(exist_ok=True)
text_suffixes = {".py", ".sh", ".md", ".txt", ".toml", ".yml", ".yaml",
                 ".html", ".css", ".js", ".svg", ".service", ".timer", ".ps1"}

def source_bytes(path):
    data = path.read_bytes()
    if path.suffix in text_suffixes or path.name in {"LICENSE", ".gitignore", ".gitattributes"}:
        data = data.replace(b"\r\n", b"\n")
    return data

for extension in ("zip", "tar.gz"):
    target = dest / f"nwpu-mail-forwarder-{version}-source.{extension}"
    if extension == "zip":
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(set(files)):
                archive.writestr(
                    (Path(f"nwpu-mail-forwarder-{version}") / path.relative_to(root)).as_posix(),
                    source_bytes(path),
                )
    else:
        with tarfile.open(target, "w:gz") as archive:
            for path in sorted(set(files)):
                data = source_bytes(path)
                info = tarfile.TarInfo((Path(f"nwpu-mail-forwarder-{version}") / path.relative_to(root)).as_posix())
                info.size = len(data)
                info.mode = 0o755 if path.suffix == ".sh" else 0o644
                archive.addfile(info, io.BytesIO(data))
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    target.with_suffix(target.suffix + ".sha256").write_text(
        digest + "  " + target.name + chr(10), encoding="ascii"
    )
    print(target.name, digest)
