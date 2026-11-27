"""Contrats C1/C2 autonomes. Aucun chargement de données à l'import du module."""

from __future__ import annotations

from copy import deepcopy
from importlib.resources import files
import json
from pathlib import Path
from typing import Any, Mapping

from .validation import ReferenceContractError, validate_contracts


MISSING = object()


def field_state(document: Mapping[str, Any], field: str) -> str:
    """État d'un champ textuel : absence, null, chaîne vide, ou valeur."""
    if field not in document:
        return "absent"
    if document[field] is None:
        return "null"
    if isinstance(document[field], str) and document[field] == "":
        return "empty"
    return "value"


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ReferenceContractError(f"JSON : propriété dupliquée {key!r}")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ReferenceContractError(f"JSON : constante non finie interdite {value}")


class ReferenceContracts:
    """Instantané validé ; les accesseurs renvoient des copies indépendantes.

    Les décisions C2 décrivent la conversion connue. Elles ne préparent aucun
    objet, n'émettent aucun SQL et ne chargent aucun override de corpus.
    """

    def __init__(self, c1: dict, c2: dict):
        validate_contracts(c1, c2)
        self._c1 = deepcopy(c1)
        self._c2 = deepcopy(c2)

    @property
    def c1(self) -> dict:
        return deepcopy(self._c1)

    @property
    def c2(self) -> dict:
        return deepcopy(self._c2)

    def values(self, nomenclature: str) -> list[dict]:
        try:
            return deepcopy(self._c1["nomenclatures"][nomenclature]["values"])
        except KeyError as exc:
            raise ReferenceContractError(f"Nomenclature inconnue : {nomenclature}") from exc

    def source_contract(self, source_class: str) -> dict:
        try:
            return deepcopy(self._c2["classes"][source_class])
        except KeyError as exc:
            raise ReferenceContractError(f"Classe source inconnue : {source_class}") from exc

    def standard_values(self, source_class: str, *, original: bool = False) -> list[dict]:
        """Valeurs courantes ou valeurs avant les normalisations attestées.

        original=True restitue le dérivé JSON initial, pas les octets du CSV :
        ses cellules vides avaient déjà été converties en null par l'extracteur.
        """
        spec = self.source_contract(source_class)
        if spec["nomenclature"] is None:
            raise ReferenceContractError(f"Standard indisponible : {source_class} ; documents locaux uniquement")
        values = self.values(spec["nomenclature"])
        if original:
            for norm in self._c2["normalizations"]:
                if norm["source_class"] == source_class:
                    for row in values:
                        if row["id"] == norm["id"]:
                            row[norm["field"]] = norm["source_value"]
        return values

    def canonical_values(self, table: str) -> list[dict]:
        spec = self._c2["tables"].get(table)
        if spec is None or len(spec["nomenclatures"]) != 1:
            raise ReferenceContractError(f"Ensemble canonique inconnu : {table}")
        name = spec["nomenclatures"][0]
        if self._c1["nomenclatures"][name]["kind"] != "canonical":
            raise ReferenceContractError(f"Ensemble non canonique : {table}")
        return self.values(name)

    def resolve(self, mapping: str, value: Any = MISSING, *, known_local_ids: frozenset[str] = frozenset()) -> dict:
        """Restitue une décision et l'identité source ; un rejet reste une erreur.

        Les actions suffixées _warn nécessitent le diagnostic de leur futur
        consommateur. La priorité d'un override AH explicite figure dans la
        décision de fallback ; cette méthode n'exécute pas cet override.
        """
        spec = self._c2["mappings"].get(mapping)
        if spec is None:
            raise ReferenceContractError(f"Correspondance inconnue : {mapping}")
        if value is not MISSING and value is not None and not isinstance(value, str):
            raise ReferenceContractError(f"{mapping}: identifiant non textuel : {value!r}")
        source = {} if value is MISSING else {"value": value}
        state = field_state(source, "value")
        outcome = None
        historical_default = None
        if state in {'absent', 'null'}:
            from .undefined import mapping_default
            historical_default = mapping_default(self, mapping)
            if historical_default is not None:
                outcome = next(r for r in spec['entries'] if r['key'] == historical_default)
        if state == "value":
            outcome = next((r for r in spec["entries"] if r["key"] == value), None)
        if outcome is None:
            outcome = spec["on_unknown" if state == "value" else "on_" + state]
        result = deepcopy(outcome)
        result.pop("key", None)
        result["source"] = {"state": state, **source}
        if historical_default is not None:
            result['historical_default'] = {'contract':'MIG-UNDEFINED-01',
                'reference_class':spec['source_class'], 'source_id':historical_default, 'state':state}
        if result["action"] == "reject":
            raise ReferenceContractError(f"{mapping}: référence non mappée ({state}) : {value!r}" if value is not MISSING else f"{mapping}: référence absente non mappée")
        if "status_if_local_known" in result:
            result["status"] = result["status_if_local_known"] if value in known_local_ids else result["status_otherwise"]
        return result


def load_contracts(directory: Path | None = None) -> ReferenceContracts:
    """Lit les ressources embarquées, ou un répertoire explicitement fourni."""
    root = files(__package__).joinpath("data") if directory is None else Path(directory)
    data = []
    for name in ("c1.json", "c2.json"):
        try:
            text = root.joinpath(name).read_text(encoding="utf-8")
            data.append(json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant))
        except (OSError, UnicodeError, ValueError, RecursionError) as exc:
            raise ReferenceContractError(f"{name}: contrat illisible : {exc}") from exc
    return ReferenceContracts(*data)


__all__ = ["MISSING", "ReferenceContractError", "ReferenceContracts", "field_state", "load_contracts", "validate_contracts"]
