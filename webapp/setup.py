"""Map the single frontend source into the installed backend package."""
from setuptools import find_namespace_packages, setup

setup(
    packages=find_namespace_packages("backend", include=["digues_webapp*"])
    + ["digues_webapp.frontend_assets"],
    package_dir={"": "backend", "digues_webapp.frontend_assets": "frontend"},
)
