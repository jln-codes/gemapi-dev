"""Candidate records reconstructed from evidence, never canonical approvals.

These constructors supply explicit unknowns, not business defaults. The manual
registry retains the disposition; extraction independently detects omissions.
"""

from copy import deepcopy


def candidate(identifier, kind, scope, definition, provenance, **kwargs):
    row = {
        "id": identifier, "kind": kind, "scope": scope,
        "definition": definition, "owner": None, "origin": "UNKNOWN",
        "status": "A_APPROUVER", "key_status": "A_APPROUVER",
        "decision_refs": [], "depends_on": [], "preservation": [],
        "questions": ["Qualification canonique non approuvée."],
        "target": None, "evidence_keys": [], "provenance": provenance,
        "binding": {"status": "INDETERMINE", "supports": [],
                    "gaps": ["Équivalence métier, cardinalités et règles de validité non qualifiées."]},
        "details": {},
    }
    row.update(deepcopy(kwargs))
    return row
