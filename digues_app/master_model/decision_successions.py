"""Exact, scoped succession of approved assertions in the single registry.

Old decisions and their assertions stay byte-for-byte evidence. A successor
names the complete previous assertion, so editing its old value cannot silently
change the scope of replacement. Invalid or ambiguous successions mask nothing.
"""

from collections import defaultdict

from .common import canonical_bytes


APPROVED = {"DECIDE", "CONSEQUENCE_NECESSAIRE"}


def resolve_decision_assertions(registry):
    """Return active assertions, exact succession evidence and diagnostics.

    This pure resolution does not approve decisions, canonical keys, or imports.
    ``replacement`` explicitly names the new subject/predicate, including when
    the pair is unchanged. No inferred equivalence enlarges the replaced scope.
    """
    decisions = {row["id"]: row for row in registry.get("decisions", [])}
    active = {key: list(row.get("assertions", [])) for key, row in decisions.items()}
    errors, candidates = [], []
    successors = defaultdict(set)
    graph = defaultdict(set)

    def fail(code, decision, message):
        errors.append({"code": "INV-DEC." + code, "element_id": decision,
                       "message": message,
                       "provenance": decisions.get(decision, {}).get("provenance", [])})

    for identifier, decision in sorted(decisions.items()):
        for succession in decision.get("supersessions", []):
            if (not isinstance(succession, dict) or
                    not {"decision", "assertion", "reason", "replacement"} <= set(succession) or
                    set(succession) - {"decision", "assertion", "reason", "replacement"} or
                    not isinstance(succession.get("decision"), str) or
                    not isinstance(succession.get("reason"), str) or not succession["reason"].strip()):
                fail("SUCCESSION_SHAPE", identifier, "Succession requires a prior decision, exact assertion and reason.")
                continue
            previous = decisions.get(succession["decision"])
            assertion = succession["assertion"]
            if not isinstance(assertion, dict) or set(assertion) != {"subject", "predicate", "value"}:
                fail("SUCCESSION_SHAPE", identifier, "The previous assertion must be quoted in full.")
                continue
            replacement = succession["replacement"]
            if (not isinstance(replacement, dict) or set(replacement) != {"subject", "predicate"} or
                    not all(isinstance(value, str) and value for value in replacement.values())):
                fail("SUCCESSION_SHAPE", identifier, "Replacement must name one exact subject and predicate.")
                continue
            if previous is None:
                fail("PREDECESSOR_MISSING", identifier, "The predecessor decision is absent.")
                continue
            if previous.get("status") not in APPROVED or decision.get("status") not in APPROVED:
                fail("SUCCESSION_UNAPPROVED", identifier, "Both predecessor and successor require approval.")
                continue
            token = canonical_bytes(assertion)
            if token not in {canonical_bytes(a) for a in previous.get("assertions", [])}:
                fail("PREDECESSOR_ASSERTION_CHANGED", identifier, "The exact previous assertion is absent or changed.")
                continue
            if not any(a["subject"] == replacement["subject"] and a["predicate"] == replacement["predicate"]
                       for a in decision.get("assertions", [])):
                fail("REPLACEMENT_MISSING", identifier, "The successor has no assertion for the named replacement.")
                continue
            graph[identifier].add(previous["id"])
            successors[(previous["id"], token)].add(identifier)
            candidates.append({"decision": previous["id"], "assertion": assertion,
                               "successor": identifier, "replacement": replacement,
                               "reason": succession["reason"]})

    for (previous, _), replacements in sorted(successors.items()):
        if len(replacements) > 1:
            for identifier in sorted(replacements):
                fail("SUCCESSION_AMBIGUOUS", identifier,
                     "Several successors replace the same assertion of " + previous + ".")

    for identifier in sorted(graph):
        stack, seen = list(graph[identifier]), set()
        while stack:
            current = stack.pop()
            if current == identifier:
                fail("SUCCESSION_CYCLE", identifier, "A succession cannot depend on itself, directly or transitively.")
                break
            if current not in seen:
                seen.add(current)
                stack.extend(graph.get(current, ()))

    if not errors:
        for succession in candidates:
            token = canonical_bytes(succession["assertion"])
            active[succession["decision"]] = [a for a in active[succession["decision"]]
                                                if canonical_bytes(a) != token]
    return {"assertions": dict(sorted(active.items())),
            "superseded": sorted(candidates, key=canonical_bytes) if not errors else [],
            "diagnostics": sorted(errors, key=canonical_bytes)}
