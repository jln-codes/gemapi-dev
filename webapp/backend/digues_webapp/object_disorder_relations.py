"""Socle fermé et transactionnel des relations entre objets et Désordres."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Final, Iterable
from uuid import UUID

from psycopg import sql


class ObjectDisorderRelationConfigurationError(ValueError):
    """La famille demandée n'appartient pas au registre serveur."""


class ObjectDisorderRelationValidationError(ValueError):
    """L'ensemble de Désordres demandé ne respecte pas le contrat commun."""


class ObjectDisorderRelationConflictError(ObjectDisorderRelationValidationError):
    """Un Désordre appartient déjà à un autre parent exclusif."""


@dataclass(frozen=True, slots=True)
class ObjectDisorderRelationConfig:
    """Noms SQL exclusivement déclarés par le serveur pour une famille métier."""

    resource: str
    parent_table: str
    parent_id_column: str
    association_table: str
    association_parent_column: str
    association_disorder_column: str
    exclusive_disorder: bool = False
    category_label: str = ""
    label_columns: tuple[str, ...] = ("designation",)


@dataclass(frozen=True, slots=True)
class ObjectDisorderRelationDifference:
    """Différence déterministe entre l'état autoritaire et l'ensemble demandé."""

    current: tuple[UUID, ...]
    requested: tuple[UUID, ...]
    additions: tuple[UUID, ...]
    removals: tuple[UUID, ...]
    unchanged: tuple[UUID, ...]

    @property
    def changed(self) -> bool:
        return bool(self.additions or self.removals)


@dataclass(frozen=True, slots=True)
class ObjectDisorderRelationPlan:
    """Plan validé et verrouillé, applicable dans la transaction courante."""

    config: ObjectDisorderRelationConfig
    parent_id: UUID
    difference: ObjectDisorderRelationDifference


@dataclass(frozen=True, slots=True)
class ObjectDisorderRelationWrite:
    """Nombre de lignes effectivement touchées dans la table d'association."""

    deleted: int
    inserted: int


RELATION_UNSET: Final = object()

_CHEMINEMENT_DESORDRES = ObjectDisorderRelationConfig(
    resource="cheminements",
    parent_table="cheminements",
    parent_id_column="id",
    association_table="cheminements__desordres",
    association_parent_column="cheminement_id",
    association_disorder_column="desordre_id",
    category_label="Cheminements",
    label_columns=("designation", "libelle"),
)

_OUVRAGE_PARTICULIER_DESORDRES = ObjectDisorderRelationConfig(
    resource="ouvrages-particuliers",
    parent_table="ouvrages_particuliers",
    parent_id_column="id",
    association_table="ouvrages_particuliers__desordres",
    association_parent_column="ouvrage_particulier_id",
    association_disorder_column="desordre_id",
    category_label="Ouvrages particuliers",
    label_columns=("designation", "libelle"),
)

_MOBILIER_DESORDRES = ObjectDisorderRelationConfig(
    resource="mobilier",
    parent_table="mobilier",
    parent_id_column="id",
    association_table="mobilier__desordres",
    association_parent_column="mobilier_id",
    association_disorder_column="desordre_id",
    category_label="Mobilier",
)

_EQUIPEMENT_MESURE_DESORDRES = ObjectDisorderRelationConfig(
    resource="equipements-mesure",
    parent_table="equipements_mesure",
    parent_id_column="id",
    association_table="equipements_mesure__desordres",
    association_parent_column="equipement_mesure_id",
    association_disorder_column="desordre_id",
    category_label="Équipements de mesure",
)

_RESEAU_TECHNIQUE_DESORDRES = ObjectDisorderRelationConfig(
    resource="reseaux-techniques",
    parent_table="reseaux_techniques",
    parent_id_column="id",
    association_table="reseaux_techniques__desordres",
    association_parent_column="reseau_technique_id",
    association_disorder_column="desordre_id",
    category_label="Réseaux techniques",
)

_VEGETATION_DESORDRES = ObjectDisorderRelationConfig(
    resource="vegetation",
    parent_table="vegetation",
    parent_id_column="id",
    association_table="vegetation__desordres",
    association_parent_column="vegetation_id",
    association_disorder_column="desordre_id",
    category_label="Végétation",
)

_AMENAGEMENT_HYDRAULIQUE_DESORDRES = ObjectDisorderRelationConfig(
    resource="amenagements-hydrauliques",
    parent_table="amenagements_hydrauliques",
    parent_id_column="id",
    association_table="amenagements_hydrauliques__desordres",
    association_parent_column="amenagement_hydraulique_id",
    association_disorder_column="desordre_id",
    exclusive_disorder=True,
    category_label="Aménagements hydrauliques",
    label_columns=("designation", "libelle"),
)

_TRONCON_DESORDRES = ObjectDisorderRelationConfig(
    resource="troncons",
    parent_table="troncons",
    parent_id_column="id",
    association_table="desordres__troncons",
    association_parent_column="troncon_id",
    association_disorder_column="desordre_id",
    category_label="Tronçons",
    label_columns=("libelle", "designation"),
)

_REGISTRY = MappingProxyType({
    _CHEMINEMENT_DESORDRES.resource: _CHEMINEMENT_DESORDRES,
    _OUVRAGE_PARTICULIER_DESORDRES.resource: _OUVRAGE_PARTICULIER_DESORDRES,
    _MOBILIER_DESORDRES.resource: _MOBILIER_DESORDRES,
    _EQUIPEMENT_MESURE_DESORDRES.resource: _EQUIPEMENT_MESURE_DESORDRES,
    _RESEAU_TECHNIQUE_DESORDRES.resource: _RESEAU_TECHNIQUE_DESORDRES,
    _VEGETATION_DESORDRES.resource: _VEGETATION_DESORDRES,
    _AMENAGEMENT_HYDRAULIQUE_DESORDRES.resource:
        _AMENAGEMENT_HYDRAULIQUE_DESORDRES,
})

_INVERSE_RELATIONS: Final = (
    _TRONCON_DESORDRES,
    _MOBILIER_DESORDRES,
    _EQUIPEMENT_MESURE_DESORDRES,
    _RESEAU_TECHNIQUE_DESORDRES,
    _OUVRAGE_PARTICULIER_DESORDRES,
    _CHEMINEMENT_DESORDRES,
    _VEGETATION_DESORDRES,
    _AMENAGEMENT_HYDRAULIQUE_DESORDRES,
)


def registered_object_disorder_relations() -> tuple[str, ...]:
    """Retourne les seules familles raccordées, dans un ordre stable."""

    return tuple(sorted(_REGISTRY))


def inverse_object_disorder_relations() -> tuple[ObjectDisorderRelationConfig, ...]:
    """Retourne le registre fermé des projections inverses, ordre d'affichage inclus."""

    return _INVERSE_RELATIONS


def object_disorder_relation(resource: str) -> ObjectDisorderRelationConfig:
    """Résout une configuration fermée sans accepter de nom SQL arbitraire."""

    try:
        return _REGISTRY[resource]
    except (KeyError, TypeError) as exc:
        raise ObjectDisorderRelationConfigurationError(
            f"Relations Objet–Désordre non configurées pour {resource!r}."
        ) from exc


def ensure_registered_object_disorder_relation(
    config: ObjectDisorderRelationConfig,
) -> ObjectDisorderRelationConfig:
    """Refuse même une configuration forgée identique à une entrée du registre."""

    if not any(config is registered for registered in _REGISTRY.values()):
        raise ObjectDisorderRelationConfigurationError(
            "La configuration Objet–Désordre ne provient pas du registre serveur."
        )
    return config


def normalize_object_disorder_request(
    requested: object = RELATION_UNSET,
) -> tuple[UUID, ...] | None:
    """Distingue champ absent, valeur nulle et ensemble final explicite."""

    if requested is RELATION_UNSET:
        return None
    if requested is None:
        raise ObjectDisorderRelationValidationError(
            "desordre_ids ne peut pas être nul."
        )
    if isinstance(requested, (str, bytes)):
        raise ObjectDisorderRelationValidationError(
            "desordre_ids doit être une liste d'UUID."
        )
    try:
        values = tuple(requested)  # type: ignore[arg-type]
    except TypeError as exc:
        raise ObjectDisorderRelationValidationError(
            "desordre_ids doit être une liste d'UUID."
        ) from exc
    if any(not isinstance(value, UUID) for value in values):
        raise ObjectDisorderRelationValidationError(
            "desordre_ids doit contenir uniquement des UUID."
        )
    if len(values) != len(set(values)):
        raise ObjectDisorderRelationValidationError(
            "desordre_ids contient des doublons."
        )
    return values


def object_disorder_relation_difference(
    current: Iterable[UUID], requested: Iterable[UUID],
) -> ObjectDisorderRelationDifference:
    """Calcule une différence triée, indépendante de l'ordre des entrées."""

    current_set = set(current)
    requested_set = set(requested)
    def ordered(values: set[UUID]) -> tuple[UUID, ...]:
        return tuple(sorted(values, key=lambda value: value.int))

    return ObjectDisorderRelationDifference(
        current=ordered(current_set),
        requested=ordered(requested_set),
        additions=ordered(requested_set - current_set),
        removals=ordered(current_set - requested_set),
        unchanged=ordered(current_set & requested_set),
    )


def _association_table(config: ObjectDisorderRelationConfig) -> sql.Identifier:
    return sql.Identifier("public", config.association_table)


def _lock_parent(
    cursor: Any, config: ObjectDisorderRelationConfig, parent_id: UUID,
) -> None:
    cursor.execute(sql.SQL(
        "SELECT 1 FROM {parent} WHERE {parent_id}=%s FOR UPDATE"
    ).format(
        parent=sql.Identifier("public", config.parent_table),
        parent_id=sql.Identifier(config.parent_id_column),
    ), (parent_id,))
    if cursor.fetchone() is None:
        raise ObjectDisorderRelationValidationError(
            "L'objet parent de la relation est introuvable."
        )


def _read_disorder_ids(
    cursor: Any,
    config: ObjectDisorderRelationConfig,
    parent_id: UUID,
    *,
    for_update: bool,
) -> tuple[UUID, ...]:
    lock = sql.SQL(" FOR UPDATE") if for_update else sql.SQL("")
    cursor.execute(sql.SQL(
        "SELECT {disorder_id} FROM {association} "
        "WHERE {parent_id}=%s ORDER BY {disorder_id}{lock}"
    ).format(
        disorder_id=sql.Identifier(config.association_disorder_column),
        association=_association_table(config),
        parent_id=sql.Identifier(config.association_parent_column),
        lock=lock,
    ), (parent_id,))
    return tuple(row[0] for row in cursor.fetchall())


def read_object_disorder_ids(
    cursor: Any,
    config: ObjectDisorderRelationConfig,
    parent_id: UUID,
) -> tuple[UUID, ...]:
    """Lit les associations d'une famille enregistrée, sans géométrie."""

    ensure_registered_object_disorder_relation(config)
    return _read_disorder_ids(cursor, config, parent_id, for_update=False)


def _validate_disorder_ids(cursor: Any, requested: tuple[UUID, ...]) -> None:
    if not requested:
        return
    cursor.execute(
        "SELECT id FROM public.desordres WHERE id=ANY(%s::uuid[]) "
        "ORDER BY id FOR KEY SHARE",
        (list(requested),),
    )
    existing = {row[0] for row in cursor.fetchall()}
    missing = sorted(set(requested) - existing, key=lambda value: value.int)
    if missing:
        raise ObjectDisorderRelationValidationError(
            f"Désordre absent de la base active : {missing[0]}."
        )


def _lock_exclusive_disorder_ids(
    cursor: Any,
    config: ObjectDisorderRelationConfig,
    parent_id: UUID,
    requested: tuple[UUID, ...],
    current: tuple[UUID, ...],
) -> None:
    """Sérialise le 1:N et refuse toute réaffectation implicite."""

    locked_ids = tuple(
        sorted(set(requested) | set(current), key=lambda value: value.int)
    )
    if not locked_ids:
        return
    cursor.execute(
        "SELECT id FROM public.desordres WHERE id=ANY(%s::uuid[]) "
        "ORDER BY id FOR UPDATE",
        (list(locked_ids),),
    )
    existing = {row[0] for row in cursor.fetchall()}
    missing = sorted(set(requested) - existing, key=lambda value: value.int)
    if missing:
        raise ObjectDisorderRelationValidationError(
            f"Désordre absent de la base active : {missing[0]}."
        )
    cursor.execute(sql.SQL(
        "SELECT {disorder_id} FROM {association} "
        "WHERE {disorder_id}=ANY(%s::uuid[]) AND {parent_id}<>%s "
        "ORDER BY {disorder_id}"
    ).format(
        disorder_id=sql.Identifier(config.association_disorder_column),
        association=_association_table(config),
        parent_id=sql.Identifier(config.association_parent_column),
    ), (list(requested), parent_id))
    conflicts = tuple(row[0] for row in cursor.fetchall())
    if conflicts:
        raise ObjectDisorderRelationConflictError(
            f"Le Désordre {conflicts[0]} est déjà rattaché à un autre "
            "Aménagement hydraulique. Retirez d'abord son rattachement existant."
        )


def plan_object_disorder_update(
    cursor: Any,
    config: ObjectDisorderRelationConfig,
    parent_id: UUID,
    requested: object = RELATION_UNSET,
) -> ObjectDisorderRelationPlan | None:
    """Valide et verrouille un ensemble final sans encore écrire les différences."""

    ensure_registered_object_disorder_relation(config)
    normalized = normalize_object_disorder_request(requested)
    if normalized is None:
        return None
    _lock_parent(cursor, config, parent_id)
    if config.exclusive_disorder:
        # Le verrou parent sérialise les mutations de cet agrégat. Les lignes
        # Désordre sont ensuite verrouillées avant toute ligne d'association,
        # dans l'ordre UUID, afin d'éviter une inversion de verrous entre deux AH.
        current = _read_disorder_ids(cursor, config, parent_id, for_update=False)
        _lock_exclusive_disorder_ids(
            cursor, config, parent_id, normalized, current,
        )
    else:
        _validate_disorder_ids(cursor, normalized)
        current = _read_disorder_ids(cursor, config, parent_id, for_update=True)
    difference = object_disorder_relation_difference(current, normalized)
    return ObjectDisorderRelationPlan(
        config=config,
        parent_id=parent_id,
        difference=difference,
    )


def apply_object_disorder_update(
    cursor: Any,
    plan: ObjectDisorderRelationPlan | None,
) -> ObjectDisorderRelationWrite:
    """Applique uniquement les suppressions et insertions calculées par le plan."""

    if plan is None:
        return ObjectDisorderRelationWrite(deleted=0, inserted=0)
    config = ensure_registered_object_disorder_relation(plan.config)
    deleted = inserted = 0
    if plan.difference.removals:
        cursor.execute(sql.SQL(
            "DELETE FROM {association} WHERE {parent_id}=%s "
            "AND {disorder_id}=ANY(%s::uuid[])"
        ).format(
            association=_association_table(config),
            parent_id=sql.Identifier(config.association_parent_column),
            disorder_id=sql.Identifier(config.association_disorder_column),
        ), (plan.parent_id, list(plan.difference.removals)))
        deleted = cursor.rowcount
        if deleted != len(plan.difference.removals):
            raise RuntimeError("La suppression différentielle des relations est incomplète.")
    if plan.difference.additions:
        conflict_clause = (
            sql.SQL(" ON CONFLICT ({disorder_id}) DO NOTHING").format(
                disorder_id=sql.Identifier(config.association_disorder_column),
            )
            if config.exclusive_disorder else sql.SQL("")
        )
        cursor.execute(sql.SQL(
            "INSERT INTO {association} ({parent_id},{disorder_id}) "
            "SELECT %s,requested_id FROM unnest(%s::uuid[]) AS requested(requested_id) "
            "ORDER BY requested_id{conflict_clause}"
        ).format(
            association=_association_table(config),
            parent_id=sql.Identifier(config.association_parent_column),
            disorder_id=sql.Identifier(config.association_disorder_column),
            conflict_clause=conflict_clause,
        ), (plan.parent_id, list(plan.difference.additions)))
        inserted = cursor.rowcount
        if inserted != len(plan.difference.additions):
            if config.exclusive_disorder:
                raise ObjectDisorderRelationConflictError(
                    "Un Désordre a été rattaché concurremment à un autre "
                    "Aménagement hydraulique."
                )
            raise RuntimeError("L'ajout différentiel des relations est incomplet.")
    return ObjectDisorderRelationWrite(deleted=deleted, inserted=inserted)
