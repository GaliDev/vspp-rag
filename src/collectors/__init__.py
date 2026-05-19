from .ado_wiki import discover_ado_wiki
from .github_specs import discover_github
from .ietf import discover_ietf
from .structural_system import discover_structural_system
from .threegpp import discover_3gpp
from .webdrafts import discover_webdrafts

__all__ = [
    "discover_ietf",
    "discover_3gpp",
    "discover_github",
    "discover_webdrafts",
    "discover_structural_system",
    "discover_ado_wiki",
]

