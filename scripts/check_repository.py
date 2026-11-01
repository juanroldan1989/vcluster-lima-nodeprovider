# SPDX-License-Identifier: Apache-2.0
"""Check source syntax, local documentation links and basic text formatting offline."""
import ast
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]


def anchors(path):
    source = re.sub(r"```.*?```", "", path.read_text(), flags=re.S)
    headings = re.findall(r"^#+\s+(.+)$", source, re.M)
    automatic = {re.sub(r"[^\w -]", "", heading.lower()).replace(" ", "-") for heading in headings}
    explicit = set(re.findall(r'<a\s+(?:name|id)="([^"]+)"', source))
    return automatic | explicit


def main():
    errors = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file() or any(part in {".git", ".local", ".venv", "__pycache__"} for part in path.relative_to(ROOT).parts):
            continue
        if path.suffix not in {".md", ".py", ".json", ".yml", ".yaml"} and path.name not in {".gitignore", ".editorconfig"}:
            continue
        source = path.read_text()
        relative = path.relative_to(ROOT)
        if not source.endswith("\n") or any(line != line.rstrip() for line in source.splitlines()):
            errors.append(f"{relative}: missing final newline or trailing whitespace")
        try:
            if path.suffix == ".py":
                ast.parse(source, filename=str(relative), feature_version=(3, 11))
            elif path.suffix == ".json":
                json.loads(source)
            elif path.suffix == ".md":
                if source.count("```") % 2:
                    errors.append(f"{relative}: unbalanced code fences")
                links = re.findall(r"\]\(([^)]+)\)", source)
                links.extend(re.findall(r'<img\b[^>]*\bsrc="([^"]+)"', source))
                for link in links:
                    if "://" in link or link.startswith("mailto:"):
                        continue
                    name, _, anchor = link.partition("#")
                    target = path.parent / name if name else path
                    if not target.is_file():
                        errors.append(f"{relative}: missing local link {link}")
                    elif anchor and anchor not in anchors(target):
                        errors.append(f"{relative}: missing heading {link}")
        except (ValueError, SyntaxError) as error:
            errors.append(f"{relative}: {error}")
    if errors:
        print("\n".join(errors), file=sys.stderr)
        return 1
    print("Source syntax, local Markdown links and text formatting passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
