"""Derive third-party build requirements from both project declarations."""

from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parent
projects = [
    tomllib.loads((ROOT / f"{name}.toml").read_text(encoding="utf-8"))["project"]
    for name in ("digues-app", "digues-webapp")
]


def canonical_name(requirement: str) -> str:
    name = re.split(r"[<>=!~;\[\s]", requirement, maxsplit=1)[0]
    return re.sub(r"[-_.]+", "-", name).lower()


local_names = {canonical_name(project["name"]) for project in projects}
third_party = dict.fromkeys(
    requirement
    for project in projects
    for requirement in project["dependencies"]
    if canonical_name(requirement) not in local_names
)
(ROOT / "requirements.txt").write_text(
    "\n".join(third_party) + "\n", encoding="utf-8"
)
