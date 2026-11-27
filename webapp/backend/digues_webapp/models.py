"""Contrats d'entrée minimaux du prototype web."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    FiniteFloat,
    field_validator,
    model_validator,
)

from .ai_memory import (
    AI_ASSISTANT_HISTORY_MAX_CHARS,
    AI_CONVERSATION_SUMMARY_INPUT_MAX_CHARS,
    AI_RECENT_MESSAGES_MAX,
    AI_USER_MESSAGE_MAX_CHARS,
)


DATA_VIEW_MAX_FIELDS = 100
DATA_VIEW_MAX_PAGE_SIZE = 2_000
DATA_VIEW_DEFAULT_PAGE_SIZE = 2_000
DATA_VIEW_MAX_HIDDEN_TRONCON_IDS = 10_000

DataViewRoot = Literal[
    "systemes",
    "digues",
    "troncons",
    "desordres",
    "observations",
    "photos",
    "mobilier",
    "equipements_mesure",
    "reseaux_techniques",
    "ouvrages_particuliers",
    "cheminements",
    "vegetation",
    "amenagements_hydrauliques",
    "zones_stockage",
    "prestations",
    "troncons_lit",
    "elements_morphologiques_lit",
]


class BusinessCRSSummary(BaseModel):
    """Identité publique du CRS métier persistant de la base sélectionnée."""

    model_config = ConfigDict(extra="forbid")

    authority: Literal["EPSG"]
    srid: int = Field(gt=0)
    code: str
    name: str
    label: str


class BaseSummaryResponse(BaseModel):
    """Synthèse stable consommée par la page Base."""

    model_config = ConfigDict(extra="forbid")

    crs: BusinessCRSSummary


class DesordreSearchItem(BaseModel):
    """Projection légère destinée aux sélecteurs de relations."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    label: str = Field(min_length=1, max_length=500)
    designation: str | None = None
    lieu_dit: str | None = None
    type_label: str | None = None
    category_label: str | None = None
    valid: bool


class DesordreSearchResponse(BaseModel):
    """Page stable et bornée de Désordres, sans géométrie."""

    model_config = ConfigDict(extra="forbid")

    items: list[DesordreSearchItem]
    next_cursor: str | None = None
    has_more: bool


class DesordreSelectionResponse(BaseModel):
    """Résolution bornée des UUID déjà sélectionnés dans la base active."""

    model_config = ConfigDict(extra="forbid")

    items: list[DesordreSearchItem]
    missing_ids: list[UUID]


class DesordreAssociatedObjectItem(BaseModel):
    """Référence légère vers un objet métier affecté par un Désordre."""

    model_config = ConfigDict(extra="forbid")

    resource: Literal[
        "troncons",
        "mobilier",
        "equipements_mesure",
        "reseaux_techniques",
        "ouvrages_particuliers",
        "cheminements",
        "vegetation",
        "amenagements_hydrauliques",
    ]
    category: str = Field(min_length=1, max_length=100)
    id: UUID
    label: str = Field(min_length=1, max_length=500)
    valid: bool | None = None


class DesordreAssociatedObjectsResponse(BaseModel):
    """Page globale et bornée des objets associés, sans géométrie."""

    model_config = ConfigDict(extra="forbid")

    items: list[DesordreAssociatedObjectItem]
    next_cursor: str | None = None
    has_more: bool


class AiChatMessage(BaseModel):
    """Message non privilégié accepté depuis le navigateur."""

    model_config = ConfigDict(extra="forbid")

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=AI_ASSISTANT_HISTORY_MAX_CHARS)

    @model_validator(mode="after")
    def validate_content(self) -> "AiChatMessage":
        normalized = self.content.strip()
        if not normalized:
            raise ValueError("Le message est obligatoire.")
        if self.role == "user" and len(normalized) > AI_USER_MESSAGE_MAX_CHARS:
            raise ValueError(
                f"Un message utilisateur est limité à {AI_USER_MESSAGE_MAX_CHARS} caractères."
            )
        self.content = normalized
        return self


class AiChatRequest(BaseModel):
    """Mémoire longue, fenêtre récente et nouveau message utilisateur."""

    model_config = ConfigDict(extra="forbid")

    conversation_summary: str = Field(
        default="", max_length=AI_CONVERSATION_SUMMARY_INPUT_MAX_CHARS
    )
    recent_messages: list[AiChatMessage] = Field(
        default_factory=list, max_length=AI_RECENT_MESSAGES_MAX
    )
    message: str = Field(min_length=1, max_length=AI_USER_MESSAGE_MAX_CHARS)

    @field_validator("conversation_summary")
    @classmethod
    def normalize_summary(cls, value: str) -> str:
        return value.strip()

    @field_validator("message")
    @classmethod
    def normalize_message(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Le message est obligatoire.")
        return normalized


class AiExecutedQuery(BaseModel):
    """Métadonnée d'affichage d'une consultation IA réellement réussie."""

    model_config = ConfigDict(extra="forbid")

    sql: str


class AiConsultedSource(BaseModel):
    """Référence documentaire effectivement transmise au modèle."""

    model_config = ConfigDict(extra="forbid")

    title: str
    path: str
    heading: str | None = None


class AiExternalSource(BaseModel):
    """Source Internet effectivement récupérée et transmise au modèle."""

    model_config = ConfigDict(extra="forbid")

    title: str
    organization: str
    url: str
    source_id: str
    category: str
    document_date: str | None = None


class AiChatResponse(BaseModel):
    """Réponse publique sans résultat SQL ni structure interne Mistral."""

    model_config = ConfigDict(extra="forbid")

    answer: str
    conversation_summary: str = ""
    recent_messages: list[AiChatMessage] = Field(default_factory=list)
    executed_queries: list[AiExecutedQuery] = Field(default_factory=list)
    consulted_sources: list[AiConsultedSource] = Field(default_factory=list)
    external_sources: list[AiExternalSource] = Field(default_factory=list)


class InteractiveSqlRequest(BaseModel):
    """Instruction de lecture saisie dans l'outil SQL utilisateur."""

    model_config = ConfigDict(extra="forbid")

    sql: str


class InteractiveSqlPreset(BaseModel):
    """Requête de lecture prédéfinie et versionnée par l'application."""

    model_config = ConfigDict(extra="forbid")

    id: str
    label: str
    description: str
    sql: str


class DataViewFieldSelection(BaseModel):
    """Champ fermé demandé au constructeur de vues."""

    model_config = ConfigDict(extra="forbid")

    table: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")
    field: str = Field(min_length=1, max_length=40, pattern=r"^[a-z_]+$")


class DataViewDefinition(BaseModel):
    """Définition réutilisable d'une vue et de son filtre Patrimoine."""

    model_config = ConfigDict(extra="forbid")

    root: DataViewRoot
    fields: list[DataViewFieldSelection] = Field(
        min_length=1,
        max_length=DATA_VIEW_MAX_FIELDS,
    )
    hidden_troncon_ids: list[UUID] = Field(
        default_factory=list,
        max_length=DATA_VIEW_MAX_HIDDEN_TRONCON_IDS,
    )

    @model_validator(mode="after")
    def reject_duplicates(self) -> "DataViewDefinition":
        keys = [(field.table, field.field) for field in self.fields]
        if len(keys) != len(set(keys)):
            raise ValueError("Un champ ne peut être ajouté qu’une fois.")
        if len(self.hidden_troncon_ids) != len(set(self.hidden_troncon_ids)):
            raise ValueError("Un tronçon masqué ne peut être indiqué qu’une fois.")
        return self


class DataViewQueryRequest(DataViewDefinition):
    """Définition d'une vue accompagnée d'une fenêtre de pagination."""

    offset: int = Field(default=0, ge=0)
    page_size: int = Field(
        default=DATA_VIEW_DEFAULT_PAGE_SIZE,
        ge=1,
        le=DATA_VIEW_MAX_PAGE_SIZE,
    )


class DataHomeViewQueryRequest(BaseModel):
    """Filtre et pagination de l'accueil, dont la vue reste interne."""

    model_config = ConfigDict(extra="forbid")

    hidden_troncon_ids: list[UUID] = Field(
        default_factory=list,
        max_length=DATA_VIEW_MAX_HIDDEN_TRONCON_IDS,
    )
    offset: int = Field(default=0, ge=0)
    page_size: int = Field(
        default=DATA_VIEW_DEFAULT_PAGE_SIZE,
        ge=1,
        le=DATA_VIEW_MAX_PAGE_SIZE,
    )

    @field_validator("hidden_troncon_ids")
    @classmethod
    def reject_duplicate_hidden_ids(cls, value: list[UUID]) -> list[UUID]:
        if len(value) != len(set(value)):
            raise ValueError("Un tronçon masqué ne peut être indiqué qu’une fois.")
        return value


class DataGeoPackageExportRequest(DataViewDefinition):
    """Définition serveur de la vue complète à convertir en GeoPackage."""

    entity_classes: list[
        Annotated[str, Field(min_length=1, max_length=40, pattern=r"^[a-z][a-z0-9_]*$")]
    ] = Field(min_length=1, max_length=6)

    @model_validator(mode="after")
    def validate_entity_classes(self) -> "DataGeoPackageExportRequest":
        allowed_classes = {
            "systemes", "digues", "troncons", "desordres", "observations", "photos",
        }
        if len(self.entity_classes) != len(set(self.entity_classes)):
            raise ValueError("Une classe d’entité ne peut être exportée qu’une fois.")
        if not set(self.entity_classes) <= allowed_classes:
            raise ValueError("Classe d’entité non autorisée pour l’export.")
        return self


class DataCsvExportRequest(DataViewDefinition):
    """Définition serveur de la vue complète à convertir en CSV."""


class NamedObjectCreate(BaseModel):
    """Champs communs réellement persistés pour les objets patrimoniaux."""

    model_config = ConfigDict(extra="forbid")

    libelle: str
    valid: bool = True

    @field_validator("libelle")
    @classmethod
    def validate_libelle(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Le libellé est obligatoire.")
        return normalized


class SystemeEndiguementCreate(NamedObjectCreate):
    """Création d'un système d'endiguement opérationnel."""

    commentaire: str | None = None
    designation: str | None = None
    population_protegee: int | None = Field(default=None, ge=0)
    classement: str | None = None
    niveau_protection: str | None = None

    @field_validator(
        "commentaire", "designation", "classement", "niveau_protection"
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class SystemeEndiguementUpdate(SystemeEndiguementCreate):
    """Modification des champs métier éditables d'un système."""


class DigueCreate(NamedObjectCreate):
    """Création d'une digue avec un système parent actif."""

    systeme_endiguement_id: UUID
    commentaire: str | None = None
    designation: str | None = None

    @field_validator("commentaire", "designation")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class DigueUpdate(NamedObjectCreate):
    """Modification des champs métier éditables d'une digue."""

    commentaire: str | None = None
    designation: str | None = None

    @field_validator("commentaire", "designation")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None


class PointDesordreUpdate(BaseModel):
    """Attributs et une seule opération spatiale dans la même mutation."""

    model_config = ConfigDict(extra="forbid")

    designation: str | None = None
    type_desordre_id: str | None = None
    categorie_desordre_id: str | None = None
    cote_id: str | None = None
    position_id: str | None = None
    source_id: str | None = None
    commentaire: str | None = None
    lieu_dit: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    valid: bool | None = None
    troncon_ids: list[UUID] | None = None
    x: FiniteFloat | None = None
    y: FiniteFloat | None = None
    longitude: FiniteFloat | None = None
    latitude: FiniteFloat | None = None
    geometry: LineStringGeometry | PolygonGeometry | None = None
    line_endpoints: LineEndpoints | None = None
    reperage: PointReperageUpdate | None = None

    @model_validator(mode="after")
    def validate_coordinate_authority(self) -> "PointDesordreUpdate":
        supplied = self.model_fields_set
        xy_fields = {"x", "y"}
        lonlat_fields = {"longitude", "latitude"}
        xy_supplied = bool(supplied & xy_fields)
        lonlat_supplied = bool(supplied & lonlat_fields)
        operations = supplied & {"geometry", "line_endpoints", "reperage"}
        if any(getattr(self, name) is None for name in operations):
            raise ValueError("Une opération géométrique ne peut pas être nulle.")
        if len(operations) + xy_supplied + lonlat_supplied > 1:
            raise ValueError("Une seule famille de localisation peut être modifiée par opération.")

        if not supplied:
            raise ValueError("Au moins un champ doit être fourni.")
        if "valid" in supplied and self.valid is None:
            raise ValueError("La validité du Désordre ne peut pas être nulle.")
        if xy_supplied and not xy_fields <= supplied:
            raise ValueError("X et Y doivent être fournis ensemble.")
        if lonlat_supplied and not lonlat_fields <= supplied:
            raise ValueError("Longitude et latitude doivent être fournies ensemble.")
        if xy_supplied and (
            self.x is None or self.y is None
        ):
            raise ValueError("X et Y ne peuvent pas être nuls.")
        if lonlat_supplied and (
            self.longitude is None or self.latitude is None
        ):
            raise ValueError("Longitude et latitude ne peuvent pas être nulles.")
        if lonlat_supplied and not (
            -180 <= self.longitude <= 180 and -90 <= self.latitude <= 90
        ):
            raise ValueError("Longitude/latitude hors domaine EPSG:4326.")
        if xy_supplied and lonlat_supplied:
            raise ValueError(
                "Une seule famille de localisation peut être modifiée par opération."
            )
        if self.troncon_ids is not None and len(set(self.troncon_ids)) != len(
            self.troncon_ids
        ):
            raise ValueError("Un tronçon ne peut être associé qu'une fois.")
        return self


class PointReperageUpdate(BaseModel):
    """Famille autoritaire de bornage pour un désordre Point ou LineString."""

    model_config = ConfigDict(extra="forbid")

    borne_debut_id: UUID
    distance_debut_m: FiniteFloat
    position_debut_relative: Literal[
        "AVANT_BORNE", "SUR_BORNE", "APRES_BORNE"
    ]
    borne_fin_id: UUID | None = None
    distance_fin_m: FiniteFloat | None = None
    position_fin_relative: Literal[
        "AVANT_BORNE", "SUR_BORNE", "APRES_BORNE"
    ] | None = None

    @model_validator(mode="after")
    def validate_distance(self) -> "PointReperageUpdate":
        if self.distance_debut_m < 0:
            raise ValueError("La distance doit être positive ou nulle.")
        if (
            self.position_debut_relative == "SUR_BORNE"
            and self.distance_debut_m != 0
        ):
            raise ValueError("La distance doit être nulle pour une position sur borne.")
        end_values = (
            self.borne_fin_id,
            self.distance_fin_m,
            self.position_fin_relative,
        )
        if sum(value is not None for value in end_values) not in (0, 3):
            raise ValueError("Le bornage de fin doit être fourni complètement.")
        if self.distance_fin_m is not None and self.distance_fin_m < 0:
            raise ValueError("La distance de fin doit être positive ou nulle.")
        if (
            self.position_fin_relative == "SUR_BORNE"
            and self.distance_fin_m != 0
        ):
            raise ValueError(
                "La distance de fin doit être nulle pour une position sur borne."
            )
        return self


class BornageGeometryQuery(PointReperageUpdate):
    """Calcul spatial en lecture seule dans un système explicitement sélectionné."""

    systeme_reperage_id: UUID


class LineStringGeometry(BaseModel):
    """Géométrie GeoJSON linéaire reçue en EPSG:4326."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["LineString"]
    coordinates: list[tuple[FiniteFloat, FiniteFloat]]

    @model_validator(mode="after")
    def validate_vertices(self) -> "LineStringGeometry":
        if len(self.coordinates) < 2:
            raise ValueError("Une LineString exige au moins deux sommets.")
        if len(set(self.coordinates)) < 2:
            raise ValueError("Une LineString exige deux sommets distincts.")
        for longitude, latitude in self.coordinates:
            if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                raise ValueError(
                    "Les sommets doivent être des longitude/latitude EPSG:4326 valides."
                )
        return self


class PointGeometry(BaseModel):
    """Point GeoJSON reçu en EPSG:4326."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Point"]
    coordinates: tuple[FiniteFloat, FiniteFloat]

    @model_validator(mode="after")
    def validate_position(self) -> "PointGeometry":
        longitude, latitude = self.coordinates
        if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
            raise ValueError("Le Point doit contenir une longitude/latitude valide.")
        return self


class MobilierCreate(BaseModel):
    """Création d'un Mobilier avec un Point EPSG:4326 obligatoire."""

    model_config = ConfigDict(extra="forbid")

    designation: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    type_id: str = Field(min_length=1)
    troncon_id: UUID | None = None
    desordre_ids: list[UUID] = Field(default_factory=list)
    geometry: PointGeometry
    valid: bool = True

    @field_validator("designation", "commentaire")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("type_id")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Le type de Mobilier est obligatoire.")
        return normalized

    @model_validator(mode="after")
    def validate_relations(self) -> "MobilierCreate":
        if len(self.desordre_ids) != len(set(self.desordre_ids)):
            raise ValueError("desordre_ids contient des doublons.")
        return self


class MobilierUpdate(BaseModel):
    """Modification partielle sans suppression implicite de la géométrie."""

    model_config = ConfigDict(extra="forbid")

    designation: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    type_id: str | None = None
    troncon_id: UUID | None = None
    desordre_ids: list[UUID] | None = None
    geometry: PointGeometry | None = None
    valid: bool | None = None

    @field_validator("designation", "commentaire")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("type_id")
    @classmethod
    def normalize_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_patch(self) -> "MobilierUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        if "type_id" in self.model_fields_set and self.type_id is None:
            raise ValueError("Le type de Mobilier ne peut pas être nul.")
        if "valid" in self.model_fields_set and self.valid is None:
            raise ValueError("La validité ne peut pas être nulle.")
        if "desordre_ids" in self.model_fields_set and self.desordre_ids is None:
            raise ValueError("desordre_ids ne peut pas être nul.")
        if (self.desordre_ids is not None
                and len(self.desordre_ids) != len(set(self.desordre_ids))):
            raise ValueError("desordre_ids contient des doublons.")
        if "geometry" in self.model_fields_set and self.geometry is None:
            raise ValueError(
                "La géométrie du Mobilier est obligatoire et ne peut pas être retirée."
            )
        return self


class EquipementMesureCreate(BaseModel):
    """Création d'un Équipement de mesure avec Point obligatoire."""

    model_config = ConfigDict(extra="forbid")

    designation: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    type_id: str = Field(min_length=1)
    troncon_id: UUID | None = None
    desordre_ids: list[UUID] = Field(default_factory=list)
    geometry: PointGeometry
    valid: bool = True

    @field_validator("designation", "commentaire")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("type_id")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Le type d'Équipement est obligatoire.")
        return normalized

    @model_validator(mode="after")
    def validate_relations(self) -> "EquipementMesureCreate":
        if len(self.desordre_ids) != len(set(self.desordre_ids)):
            raise ValueError("desordre_ids contient des doublons.")
        return self


class EquipementMesureUpdate(BaseModel):
    """Modification partielle sans suppression implicite du Point."""

    model_config = ConfigDict(extra="forbid")

    designation: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    type_id: str | None = None
    troncon_id: UUID | None = None
    desordre_ids: list[UUID] | None = None
    geometry: PointGeometry | None = None
    valid: bool | None = None

    @field_validator("designation", "commentaire")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("type_id")
    @classmethod
    def normalize_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_patch(self) -> "EquipementMesureUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        if "type_id" in self.model_fields_set and self.type_id is None:
            raise ValueError("Le type d'Équipement ne peut pas être nul.")
        if "valid" in self.model_fields_set and self.valid is None:
            raise ValueError("La validité ne peut pas être nulle.")
        if "desordre_ids" in self.model_fields_set and self.desordre_ids is None:
            raise ValueError("desordre_ids ne peut pas être nul.")
        if (self.desordre_ids is not None
                and len(self.desordre_ids) != len(set(self.desordre_ids))):
            raise ValueError("desordre_ids contient des doublons.")
        if "geometry" in self.model_fields_set and self.geometry is None:
            raise ValueError(
                "La géométrie de l'Équipement est obligatoire et ne peut pas être retirée."
            )
        return self


ReseauTechniqueGeometry = Annotated[
    PointGeometry | LineStringGeometry, Field(discriminator="type"),
]


class ReseauTechniqueCreate(BaseModel):
    """Création d'un Réseau technique Point ou LineString facultatif."""

    model_config = ConfigDict(extra="forbid")

    designation: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    type_id: str = Field(min_length=1)
    troncon_id: UUID | None = None
    desordre_ids: list[UUID] = Field(default_factory=list)
    geometry: ReseauTechniqueGeometry | None = None
    valid: bool = True

    @field_validator("designation", "commentaire")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("type_id")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("Le type de Réseau est obligatoire.")
        return normalized

    @model_validator(mode="after")
    def validate_relations(self) -> "ReseauTechniqueCreate":
        if len(self.desordre_ids) != len(set(self.desordre_ids)):
            raise ValueError("desordre_ids contient des doublons.")
        return self


class ReseauTechniqueUpdate(BaseModel):
    """Modification partielle, avec changement Point/LineString autorisé."""

    model_config = ConfigDict(extra="forbid")

    designation: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    type_id: str | None = None
    troncon_id: UUID | None = None
    desordre_ids: list[UUID] | None = None
    geometry: ReseauTechniqueGeometry | None = None
    valid: bool | None = None

    @field_validator("designation", "commentaire")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @field_validator("type_id")
    @classmethod
    def normalize_type(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return value.strip() or None

    @model_validator(mode="after")
    def validate_patch(self) -> "ReseauTechniqueUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        if "type_id" in self.model_fields_set and self.type_id is None:
            raise ValueError("Le type de Réseau ne peut pas être nul.")
        if "valid" in self.model_fields_set and self.valid is None:
            raise ValueError("La validité ne peut pas être nulle.")
        if "desordre_ids" in self.model_fields_set and self.desordre_ids is None:
            raise ValueError("desordre_ids ne peut pas être nul.")
        if (self.desordre_ids is not None
                and len(self.desordre_ids) != len(set(self.desordre_ids))):
            raise ValueError("desordre_ids contient des doublons.")
        if "geometry" in self.model_fields_set and self.geometry is None:
            raise ValueError(
                "Le retrait d'une géométrie Réseau n'est pas défini dans F3."
            )
        return self


class PolygonGeometry(BaseModel):
    """Polygon GeoJSON simple ou troué reçu en EPSG:4326."""

    model_config = ConfigDict(extra="forbid")

    type: Literal["Polygon"]
    coordinates: list[list[tuple[FiniteFloat, FiniteFloat]]]

    @model_validator(mode="after")
    def validate_rings(self) -> "PolygonGeometry":
        if not self.coordinates:
            raise ValueError("Un Polygon exige un anneau extérieur.")
        for ring in self.coordinates:
            if len(ring) < 4:
                raise ValueError("Chaque anneau d'un Polygon exige quatre positions.")
            if ring[0] != ring[-1]:
                raise ValueError("Chaque anneau d'un Polygon doit être fermé.")
            for longitude, latitude in ring:
                if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                    raise ValueError(
                        "Les sommets doivent être des longitude/latitude "
                        "EPSG:4326 valides."
                    )
        return self


OuvrageParticulierGeometry = Annotated[
    PointGeometry | LineStringGeometry | PolygonGeometry,
    Field(discriminator="type"),
]


class OuvrageParticulierCreate(BaseModel):
    """Saisie de l'ouvrage particulier métier."""

    model_config = ConfigDict(extra="forbid")

    type_id: str | None = None
    designation: str | None = None
    libelle: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    troncon_id: UUID | None = None
    desordre_ids: list[UUID] = Field(default_factory=list)
    geometry: OuvrageParticulierGeometry
    valid: bool = True

    @field_validator("type_id", "designation", "libelle", "commentaire")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_relations(self) -> "OuvrageParticulierCreate":
        if len(self.desordre_ids) != len(set(self.desordre_ids)):
            raise ValueError("desordre_ids contient des doublons.")
        return self


class OuvrageParticulierUpdate(BaseModel):
    """Édition métier de l'ouvrage particulier."""

    model_config = ConfigDict(extra="forbid")

    type_id: str | None = None
    designation: str | None = None
    libelle: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    troncon_id: UUID | None = None
    desordre_ids: list[UUID] | None = None
    geometry: OuvrageParticulierGeometry | None = None
    valid: bool | None = None

    @field_validator("type_id", "designation", "libelle", "commentaire")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> "OuvrageParticulierUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        if "valid" in self.model_fields_set and self.valid is None:
            raise ValueError("La validité ne peut pas être nulle.")
        if "desordre_ids" in self.model_fields_set and self.desordre_ids is None:
            raise ValueError("desordre_ids ne peut pas être nul.")
        if (self.desordre_ids is not None
                and len(self.desordre_ids) != len(set(self.desordre_ids))):
            raise ValueError("desordre_ids contient des doublons.")
        if "geometry" in self.model_fields_set and self.geometry is None:
            raise ValueError("La géométrie de l'Ouvrage particulier est obligatoire et ne peut pas être retirée.")
        return self


DesordreGeometry = Annotated[
    PointGeometry | LineStringGeometry | PolygonGeometry,
    Field(discriminator="type"),
]


TronconLitGeometry = Annotated[
    LineStringGeometry | PolygonGeometry, Field(discriminator="type"),
]
MorphologieLitType = Literal["ILE", "BANC", "ATTERISSEMENT", "INDETERMINE"]
LitCharacteristicFamily = Literal[
    "largeurs", "pentes", "domanialites", "regimes-ecoulement", "occupations-riveraines",
]


class TronconLitCreate(BaseModel):
    """Occurrence autonome ; absence de géométrie examinée selon la validité."""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    designation: str | None = None
    libelle: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    digue_ids: list[UUID] = Field(default_factory=list)
    geometry: TronconLitGeometry | None = None
    valid: bool = True

    @field_validator("designation", "libelle", "commentaire")
    @classmethod
    def optional_text(cls, value):
        return value.strip() or None if value is not None else None

    @field_validator("digue_ids")
    @classmethod
    def distinct_digues(cls, value):
        if len(value) != len(set(value)):
            raise ValueError("digue_ids contient des doublons.")
        return value


class TronconLitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    designation: str | None = None
    libelle: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    digue_ids: list[UUID] | None = None
    geometry: TronconLitGeometry | None = None
    valid: bool | None = None

    _optional_text = field_validator("designation", "libelle", "commentaire")(
        TronconLitCreate.optional_text.__func__)

    @model_validator(mode="after")
    def patch(self):
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        for key in ("valid", "digue_ids", "geometry"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} ne peut pas être nul ; omettez le champ pour le conserver.")
        if self.digue_ids is not None:
            TronconLitCreate.distinct_digues(self.digue_ids)
        return self


class ElementMorphologiqueLitCreate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    id: UUID
    type_morphologie: MorphologieLitType
    designation: str | None = None
    libelle: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    segment_id: UUID | None = None
    longueur: Decimal | None = None
    largeur: Decimal | None = None
    rattache_berge: bool | None = None
    vegetalise: bool | None = None
    type_zone_id: str | None = None
    geometry: DesordreGeometry | None = None
    valid: bool = True

    _optional_text = field_validator("designation", "libelle", "commentaire", "type_zone_id")(
        TronconLitCreate.optional_text.__func__)

    @field_validator("longueur", "largeur", mode="before")
    @classmethod
    def numeric_not_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("Une valeur numérique ne peut pas être booléenne.")
        return value

    @model_validator(mode="after")
    def zone_type(self):
        if self.type_zone_id is not None and self.type_morphologie != "ATTERISSEMENT":
            raise ValueError("Le type d'atterrissement exige le sous-type ATTERISSEMENT.")
        return self


class ElementMorphologiqueLitUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    type_morphologie: MorphologieLitType | None = None
    designation: str | None = None
    libelle: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    segment_id: UUID | None = None
    longueur: Decimal | None = None
    largeur: Decimal | None = None
    rattache_berge: bool | None = None
    vegetalise: bool | None = None
    type_zone_id: str | None = None
    geometry: DesordreGeometry | None = None
    valid: bool | None = None

    _optional_text = field_validator("designation", "libelle", "commentaire", "type_zone_id")(
        TronconLitCreate.optional_text.__func__)
    _numeric_not_boolean = field_validator("longueur", "largeur", mode="before")(
        ElementMorphologiqueLitCreate.numeric_not_boolean.__func__)

    @model_validator(mode="after")
    def patch(self):
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        for key in ("valid", "type_morphologie", "geometry"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} ne peut pas être nul ; omettez le champ pour le conserver.")
        return self


class LitCharacteristicValues(BaseModel):
    """Champs propres aux cinq familles ; la route vérifie leur appartenance."""

    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    designation: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    pr_debut: Decimal | None = None
    pr_fin: Decimal | None = None
    largeur_moyenne: Decimal | None = None
    pente_moyenne: Decimal | None = None
    type_id: str | None = None
    regime_id: str | None = None
    type_occupation_id: str | None = None
    rive_id: str | None = None

    _optional_text = field_validator(
        "designation", "commentaire", "type_id", "regime_id", "type_occupation_id", "rive_id",
    )(TronconLitCreate.optional_text.__func__)
    _numeric_not_boolean = field_validator(
        "pr_debut", "pr_fin", "largeur_moyenne", "pente_moyenne", mode="before",
    )(ElementMorphologiqueLitCreate.numeric_not_boolean.__func__)

    def validate_family(self, family: str):
        specific = {
            "largeurs": {"largeur_moyenne"}, "pentes": {"pente_moyenne"},
            "domanialites": {"type_id"}, "regimes-ecoulement": {"regime_id"},
            "occupations-riveraines": {"type_occupation_id", "rive_id"},
        }
        all_specific = set().union(*specific.values())
        if (self.model_fields_set & all_specific) - specific[family]:
            raise ValueError("Un attribut fourni appartient à une autre famille de caractéristique.")
        return self


class LitCharacteristicCreate(LitCharacteristicValues):
    id: UUID
    valid: bool = True


class LitCharacteristicUpdate(LitCharacteristicValues):
    valid: bool | None = None

    @model_validator(mode="after")
    def patch(self):
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        if "valid" in self.model_fields_set and self.valid is None:
            raise ValueError("La validité ne peut pas être nulle.")
        return self


CHEMINEMENT_POLYGON_TYPE_IDS = frozenset({"CAC"})


def cheminement_geometry_types(type_id: str | None) -> frozenset[str]:
    """Types GeoJSON autorisés par le type métier canonique du Cheminement."""

    return (
        frozenset({"LineString", "Polygon"})
        if type_id in CHEMINEMENT_POLYGON_TYPE_IDS
        else frozenset({"LineString"})
    )


CheminementGeometry = Annotated[
    LineStringGeometry | PolygonGeometry,
    Field(discriminator="type"),
]


class CheminementCreate(BaseModel):
    """Saisie métier d'un cheminement et de ses liens explicites."""

    model_config = ConfigDict(extra="forbid")
    type_cheminement_id: str = Field(min_length=1)
    designation: str | None = None
    libelle: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    largeur: FiniteFloat | None = None
    dimension_horizontale: FiniteFloat | None = None
    dimension_verticale: FiniteFloat | None = None
    numero_secteur: int | None = None
    materiau_id: str | None = None
    revetement_id: str | None = None
    usage_id: str | None = None
    statut_acces: Literal["PUBLIC", "PRIVE"] | None = None
    type_voie_id: str | None = None
    position_id: str | None = None
    cote_id: str | None = None
    securite_id: str | None = None
    orientation_id: str | None = None
    position_haut_id: str | None = None
    position_bas_id: str | None = None
    revetement_haut_id: str | None = None
    revetement_bas_id: str | None = None
    source_id: str | None = None
    troncon_ids: list[UUID] = Field(default_factory=list)
    desordre_ids: list[UUID] = Field(default_factory=list)
    geometry: CheminementGeometry
    valid: bool = True

    @field_validator("designation", "libelle", "commentaire")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @field_validator("type_cheminement_id")
    @classmethod
    def normalize_type(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Le type de Cheminement est obligatoire.")
        return value.strip()

    @model_validator(mode="after")
    def validate_relations(self) -> "CheminementCreate":
        if len(set(self.troncon_ids)) != len(self.troncon_ids) or len(set(self.desordre_ids)) != len(self.desordre_ids):
            raise ValueError("Les relations Cheminement ne doivent pas contenir de doublons.")
        if self.geometry.type not in cheminement_geometry_types(self.type_cheminement_id):
            raise ValueError(
                "La géométrie Polygon est réservée au type Chemin d'accès technique (CAC)."
            )
        return self


class CheminementUpdate(BaseModel):
    """Modification partielle ; listes présentes = remplacement atomique."""

    model_config = ConfigDict(extra="forbid")
    type_cheminement_id: str | None = None
    designation: str | None = None
    libelle: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    largeur: FiniteFloat | None = None
    dimension_horizontale: FiniteFloat | None = None
    dimension_verticale: FiniteFloat | None = None
    numero_secteur: int | None = None
    materiau_id: str | None = None
    revetement_id: str | None = None
    usage_id: str | None = None
    statut_acces: Literal["PUBLIC", "PRIVE"] | None = None
    type_voie_id: str | None = None
    position_id: str | None = None
    cote_id: str | None = None
    securite_id: str | None = None
    orientation_id: str | None = None
    position_haut_id: str | None = None
    position_bas_id: str | None = None
    revetement_haut_id: str | None = None
    revetement_bas_id: str | None = None
    source_id: str | None = None
    troncon_ids: list[UUID] | None = None
    desordre_ids: list[UUID] | None = None
    geometry: CheminementGeometry | None = None
    valid: bool | None = None

    @field_validator("designation", "libelle", "commentaire")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> "CheminementUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        for key in ("type_cheminement_id", "valid", "troncon_ids", "desordre_ids", "geometry"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} ne peut pas être nul.")
        if self.type_cheminement_id is not None:
            self.type_cheminement_id = self.type_cheminement_id.strip()
            if not self.type_cheminement_id:
                raise ValueError("Le type de Cheminement est obligatoire.")
        for key in ("troncon_ids", "desordre_ids"):
            values = getattr(self, key)
            if values is not None and len(values) != len(set(values)):
                raise ValueError(f"{key} contient des doublons.")
        if (self.geometry is not None and self.type_cheminement_id is not None
                and self.geometry.type not in cheminement_geometry_types(
                    self.type_cheminement_id
                )):
            raise ValueError(
                "La géométrie Polygon est réservée au type Chemin d'accès technique (CAC)."
            )
        return self



class AmenagementHydrauliqueCreate(BaseModel):
    """Champs métier de la racine AH et tronçons explicitement choisis."""

    model_config = ConfigDict(extra="forbid")
    designation: str | None = None
    libelle: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    troncon_ids: list[UUID] = Field(default_factory=list)
    troncon_versions: dict[UUID, Annotated[int, Field(strict=True, ge=1, le=9007199254740991)]] = Field(default_factory=dict)
    zone_ids: list[UUID] = Field(default_factory=list)
    desordre_ids: list[UUID] = Field(default_factory=list)
    valid: bool | None = None

    @field_validator("designation", "libelle", "commentaire")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_relations(self) -> "AmenagementHydrauliqueCreate":
        for field in ("troncon_ids", "zone_ids", "desordre_ids"):
            values = getattr(self, field)
            if values is not None and len(values) != len(set(values)):
                raise ValueError(f"{field} contient des doublons.")
        return self


class AmenagementHydrauliqueUpdate(AmenagementHydrauliqueCreate):
    """Modification partielle ; la liste présente remplace tous les liens."""

    troncon_ids: list[UUID] | None = None
    zone_ids: list[UUID] | None = None
    desordre_ids: list[UUID] | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> "AmenagementHydrauliqueUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        if "troncon_ids" in self.model_fields_set and self.troncon_ids is None:
            raise ValueError("Les tronçons ne peuvent pas être nuls.")
        if "desordre_ids" in self.model_fields_set and self.desordre_ids is None:
            raise ValueError("desordre_ids ne peut pas être nul.")
        if "zone_ids" in self.model_fields_set and self.zone_ids is None:
            raise ValueError("zone_ids ne peut pas être nul.")
        for field in ("troncon_ids", "zone_ids", "desordre_ids"):
            values = getattr(self, field)
            if values is not None and len(values) != len(set(values)):
                raise ValueError(f"{field} contient des doublons.")
        return self


class ZoneStockageCreate(BaseModel):
    """Zone native : Polygon obligatoire, AH et réseaux uniquement explicites."""

    model_config = ConfigDict(extra="forbid")
    type_id: str | None = None
    designation: str | None = None
    libelle: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    capacite_stockage: float | None = Field(default=None, allow_inf_nan=False)
    profondeur_moyenne: float | None = Field(default=None, allow_inf_nan=False)
    superficie: float | None = Field(default=None, allow_inf_nan=False)
    collectivite_competente_id: UUID | None = None
    fonctionnement_id: str | None = None
    amenagement_hydraulique_id: UUID | None = None
    reseau_hydraulique_ids: list[UUID] = Field(default_factory=list)
    geometry: PolygonGeometry
    valid: bool = True

    @field_validator("type_id", "designation", "libelle", "commentaire", "fonctionnement_id")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_relations(self) -> "ZoneStockageCreate":
        if (self.reseau_hydraulique_ids is not None
                and len(self.reseau_hydraulique_ids) != len(set(self.reseau_hydraulique_ids))):
            raise ValueError("reseau_hydraulique_ids contient des doublons.")
        return self


class ZoneStockageUpdate(ZoneStockageCreate):
    geometry: PolygonGeometry | None = None
    valid: bool | None = None
    reseau_hydraulique_ids: list[UUID] | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> "ZoneStockageUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        if "geometry" in self.model_fields_set and self.geometry is None:
            raise ValueError("La géométrie ne peut pas être retirée.")
        if "valid" in self.model_fields_set and self.valid is None:
            raise ValueError("valid ne peut pas être nul.")
        if "reseau_hydraulique_ids" in self.model_fields_set and self.reseau_hydraulique_ids is None:
            raise ValueError("reseau_hydraulique_ids ne peut pas être nul.")
        return self



PrestationObjectFamily = Literal[
    "desordres", "ouvrages_hydrauliques", "ouvrages_particuliers",
    "mobilier", "equipements_mesure", "reseaux_techniques",
    "cheminements", "vegetation", "amenagements_hydrauliques",
]


class PrestationEditable(BaseModel):
    """Attributs métier cibles ; aucune provenance V2 n'est acceptée."""

    model_config = ConfigDict(extra="forbid")
    type_prestation_id: str | None = None
    designation: str | None = None
    libelle: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    cout_global: FiniteFloat | None = Field(default=None, ge=0)
    cout_metre: FiniteFloat | None = Field(default=None, ge=0)
    realisation_interne: bool | None = None
    valid: bool | None = None
    geometry: DesordreGeometry | None = None

    @field_validator("type_prestation_id", "designation", "libelle", "commentaire")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None


class PrestationCreate(PrestationEditable):
    """Nouvelle action réalisée ; date de fin explicite sans statut artificiel."""

    id: UUID | None = None
    date_fin: date
    troncon_ids: list[UUID] = Field(default_factory=list)
    object_ids: dict[PrestationObjectFamily, list[UUID]] = Field(default_factory=dict)
    intervenant_ids: list[UUID] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_links(self) -> "PrestationCreate":
        if (len(self.troncon_ids) != len(set(self.troncon_ids))
                or len(self.intervenant_ids) != len(set(self.intervenant_ids))
                or any(len(ids) != len(set(ids)) for ids in self.object_ids.values())):
            raise ValueError("Les relations ne doivent pas contenir de doublons.")
        return self


class PrestationUpdate(PrestationEditable):
    """Patch métier ; les listes présentes remplacent les liens éditables, sans toucher aux cibles hors DTO."""

    troncon_ids: list[UUID] | None = None
    object_ids: dict[PrestationObjectFamily, list[UUID]] | None = None
    intervenant_ids: list[UUID] | None = None

    @model_validator(mode="after")
    def validate_patch(self) -> "PrestationUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        if "geometry" in self.model_fields_set and self.geometry is None:
            raise ValueError("Le retrait de la géométrie n'est pas défini.")
        for field in ("troncon_ids", "object_ids", "intervenant_ids"):
            if field in self.model_fields_set and getattr(self, field) is None:
                raise ValueError(f"{field} ne peut pas être nul.")
        if ((self.troncon_ids is not None
             and len(self.troncon_ids) != len(set(self.troncon_ids)))
            or (self.intervenant_ids is not None
                and len(self.intervenant_ids) != len(set(self.intervenant_ids)))
            or (self.object_ids is not None and any(
                len(ids) != len(set(ids)) for ids in self.object_ids.values()))):
            raise ValueError("Les relations ne doivent pas contenir de doublons.")
        return self


class VegetationCreate(BaseModel):
    """Végétation et rattachements métier explicites, sans parent imposé."""

    model_config = ConfigDict(extra="forbid")
    troncon_ids: list[UUID] = Field(default_factory=list)
    amenagement_hydraulique_ids: list[UUID] = Field(default_factory=list)
    desordre_ids: list[UUID] = Field(default_factory=list)
    problematique_ids: list[str] = Field(default_factory=list)
    famille_vegetation: Literal["HERBACEES", "BUISSONS_ARBUSTES", "ARBRES"]
    disposition: Literal["ISOLE", "LIGNE", "SURFACE"]
    invasif: bool = False
    sous_type: Literal["ARBRE_SUR_PIED", "SOUCHE"] | None = None
    designation: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    contact_eau: bool | None = None
    densite: FiniteFloat | None = None
    hauteur_m: FiniteFloat | None = None
    diametre_cm: FiniteFloat | None = None
    type_cote_id: str | None = None
    type_position_id: str | None = None
    etat_sanitaire_id: str | None = None
    classe_hauteur_id: str | None = None
    classe_diametre_id: str | None = None
    espece_id: str | None = None
    geometry: DesordreGeometry
    valid: bool = True

    @field_validator("designation", "commentaire", "type_cote_id", "type_position_id", "etat_sanitaire_id",
                     "classe_hauteur_id", "classe_diametre_id",
                     "espece_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_relations(self) -> "VegetationCreate":
        if ((self.famille_vegetation == "ARBRES") != (self.sous_type is not None)):
            raise ValueError("Le sous-type est obligatoire pour un Arbre et interdit ailleurs.")
        if any(not value.strip() for value in self.problematique_ids):
            raise ValueError("problematique_ids contient une valeur vide.")
        self.problematique_ids = [value.strip() for value in self.problematique_ids]
        for field in ("troncon_ids", "amenagement_hydraulique_ids",
                      "problematique_ids", "desordre_ids"):
            values = getattr(self, field)
            if len(values) != len(set(values)):
                raise ValueError(f"{field} contient un doublon.")
        return self


class VegetationUpdate(BaseModel):
    """Modification métier, sans écriture des plans de gestion."""

    model_config = ConfigDict(extra="forbid")
    troncon_ids: list[UUID] | None = None
    amenagement_hydraulique_ids: list[UUID] | None = None
    desordre_ids: list[UUID] | None = None
    problematique_ids: list[str] | None = None
    famille_vegetation: Literal["HERBACEES", "BUISSONS_ARBUSTES", "ARBRES"] | None = None
    disposition: Literal["ISOLE", "LIGNE", "SURFACE"] | None = None
    invasif: bool | None = None
    sous_type: Literal["ARBRE_SUR_PIED", "SOUCHE"] | None = None
    designation: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    contact_eau: bool | None = None
    densite: FiniteFloat | None = None
    hauteur_m: FiniteFloat | None = None
    diametre_cm: FiniteFloat | None = None
    type_cote_id: str | None = None
    type_position_id: str | None = None
    etat_sanitaire_id: str | None = None
    classe_hauteur_id: str | None = None
    classe_diametre_id: str | None = None
    espece_id: str | None = None
    geometry: DesordreGeometry | None = None
    valid: bool | None = None

    @field_validator("designation", "commentaire", "type_cote_id", "type_position_id", "etat_sanitaire_id",
                     "classe_hauteur_id", "classe_diametre_id",
                     "espece_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_patch(self) -> "VegetationUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        for key in ("famille_vegetation", "disposition", "invasif", "valid", "geometry", "troncon_ids",
                    "amenagement_hydraulique_ids", "problematique_ids",
                    "desordre_ids"):
            if key in self.model_fields_set and getattr(self, key) is None:
                raise ValueError(f"{key} ne peut pas être nul.")
        if self.problematique_ids is not None:
            if any(not value.strip() for value in self.problematique_ids):
                raise ValueError("problematique_ids contient une valeur vide.")
            self.problematique_ids = [value.strip() for value in self.problematique_ids]
        for key in ("troncon_ids", "amenagement_hydraulique_ids",
                    "problematique_ids", "desordre_ids"):
            values = getattr(self, key)
            if values is not None and len(values) != len(set(values)):
                raise ValueError(f"{key} contient un doublon.")
        return self


class LineEndpoints(BaseModel):
    """Extrémités autoritaires d'une LineString dans un CRS explicite."""

    model_config = ConfigDict(extra="forbid")

    crs: str = Field(min_length=1, max_length=32)
    debut: tuple[FiniteFloat, FiniteFloat]
    fin: tuple[FiniteFloat, FiniteFloat]

    @model_validator(mode="after")
    def validate_endpoints(self) -> "LineEndpoints":
        self.crs = self.crs.strip().upper()
        if self.debut == self.fin:
            raise ValueError("Le début et la fin doivent être distincts.")
        if self.crs == "EPSG:4326":
            for longitude, latitude in (self.debut, self.fin):
                if not -180 <= longitude <= 180 or not -90 <= latitude <= 90:
                    raise ValueError("Extrémité hors domaine EPSG:4326.")
        return self


class DesordreCreate(BaseModel):
    """Création d'un désordre, avec une seule autorité géométrique."""

    model_config = ConfigDict(extra="forbid")

    designation: str | None = None
    type_desordre_id: str | None = None
    categorie_desordre_id: str | None = None
    cote_id: str | None = None
    position_id: str | None = None
    source_id: str | None = None
    commentaire: str | None = None
    lieu_dit: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    valid: bool = True
    troncon_ids: list[UUID] = Field(default_factory=list)
    geometry_type: Literal["Point", "LineString", "Polygon"] | None = None
    geometry: DesordreGeometry | None = None
    line_endpoints: LineEndpoints | None = None
    reperage: PointReperageUpdate | None = None
    x: FiniteFloat | None = None
    y: FiniteFloat | None = None
    longitude: FiniteFloat | None = None
    latitude: FiniteFloat | None = None

    @field_validator(
        "designation", "type_desordre_id", "categorie_desordre_id",
        "cote_id", "position_id", "source_id",
        "commentaire", "lieu_dit",
    )
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_location_authority(self) -> "DesordreCreate":
        xy_supplied = self.x is not None or self.y is not None
        lonlat_supplied = (
            self.longitude is not None or self.latitude is not None
        )
        if xy_supplied and (
            self.x is None or self.y is None
        ):
            raise ValueError("X et Y doivent être fournis ensemble.")
        if lonlat_supplied and (
            self.longitude is None or self.latitude is None
        ):
            raise ValueError("Longitude et latitude doivent être fournies ensemble.")
        authority_count = (
            int(self.geometry is not None)
            + int(self.line_endpoints is not None)
            + int(self.reperage is not None)
            + int(xy_supplied)
            + int(lonlat_supplied)
        )
        if authority_count != 1:
            raise ValueError(
                "Fournir exactement une géométrie, une paire X/Y ou une paire "
                "longitude/latitude."
            )
        if (xy_supplied or lonlat_supplied) and self.geometry is not None:
            raise ValueError("Les coordonnées numériques sont réservées au Point.")
        if lonlat_supplied and not (
            -180 <= self.longitude <= 180
            and -90 <= self.latitude <= 90
        ):
            raise ValueError("Longitude/latitude hors domaine EPSG:4326.")
        if len(set(self.troncon_ids)) != len(self.troncon_ids):
            raise ValueError("Un tronçon ne peut être associé qu'une fois.")
        inferred_type = (
            self.geometry.type
            if self.geometry is not None
            else "LineString" if self.line_endpoints is not None
            else "Point" if xy_supplied or lonlat_supplied
            else self.geometry_type
        )
        if self.geometry_type is not None and inferred_type != self.geometry_type:
            raise ValueError("Le type géométrique contredit l'autorité fournie.")
        if inferred_type == "Point" and len(self.troncon_ids) > 1:
            raise ValueError("Un désordre Point accepte au plus un tronçon.")
        if self.reperage is not None:
            if len(self.troncon_ids) != 1:
                raise ValueError("Le bornage exige exactement un tronçon.")
            has_end = self.reperage.borne_fin_id is not None
            if inferred_type == "Point" and has_end:
                raise ValueError("Un Point ne possède pas de bornage de fin.")
            if inferred_type == "LineString" and not has_end:
                raise ValueError("Une LineString exige un bornage de fin.")
            if inferred_type not in ("Point", "LineString"):
                raise ValueError("Le bornage est réservé aux Point et LineString.")
        return self


TronconType = Literal["INDETERMINE", "BERGE", "CRETE", "TALUS", "TALUS_RISBERME", "RISBERME", "FRANC_BORD"]


class TronconGeometry(BaseModel):
    """GeoJSON EPSG:4326 XY/XYZ ; aucune règle de forme classique implicite."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon", "GeometryCollection"]
    coordinates: list | None = None
    geometries: list[TronconGeometry] | None = None

    @model_validator(mode="after")
    def validate_coordinates(self):
        import math
        if self.type == "GeometryCollection":
            if self.geometries is None or "coordinates" in self.model_fields_set:
                raise ValueError("Une GeometryCollection exige geometries, sans coordinates.")
        else:
            if self.coordinates is None or "geometries" in self.model_fields_set:
                raise ValueError("La géométrie exige coordinates, sans geometries.")
            depth = {"Point":0,"LineString":1,"MultiPoint":1,"Polygon":2,"MultiLineString":2,"MultiPolygon":3}[self.type]
            def check(value, nesting):
                if not isinstance(value, (list, tuple)):
                    raise ValueError("Structure de coordonnées GeoJSON invalide.")
                if nesting:
                    for child in value: check(child, nesting-1)
                elif value:
                    if len(value) not in (2,3) or any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in value):
                        raise ValueError("Les positions GeoJSON exigent deux ou trois nombres finis.")
                    if not -180 <= value[0] <= 180 or not -90 <= value[1] <= 90:
                        raise ValueError("Coordonnées hors domaine EPSG:4326.")
            check(self.coordinates, depth)
        return self


class TronconFields(BaseModel):
    """Champs C1 éditables ; les valeurs explicites ne sont pas normalisées."""
    model_config = ConfigDict(extra="forbid")
    type_physique_id: TronconType | None = None
    cote_physique_id: str | None = None
    digue_id: UUID | None = None
    amenagement_hydraulique_id: UUID | None = None
    libelle: str | None = None
    designation: str | None = None
    commentaire: str | None = None
    date_debut: date | None = None
    date_fin: date | None = None
    geometry: TronconGeometry | None = None
    valid: bool | None = Field(default=None, strict=True)
    type_rive_id: str | None = None
    type_troncon_id: str | None = None
    typologie_troncon_id: str | None = None
    systeme_reperage_defaut_id: UUID | None = None
    troncon_support_id: UUID | None = None
    segment_hydrographique_support_id: UUID | None = None
    berge_support_id: UUID | None = None
    source_id: str | None = None
    cote_id: str | None = None
    position_id: str | None = None
    fonction_id: str | None = None
    fonction_haut_id: str | None = None
    fonction_bas_id: str | None = None
    nature_id: str | None = None
    nature_haut_id: str | None = None
    nature_bas_id: str | None = None
    materiau_id: str | None = None
    materiau_haut_id: str | None = None
    materiau_bas_id: str | None = None
    largeur_franc_bord_id: str | None = None
    num_couche: int | None = Field(default=None, strict=True)
    epaisseur: FiniteFloat | None = None
    largeur_moyenne: FiniteFloat | None = None
    epaisseur_sommet: FiniteFloat | None = None
    pente_interieure: FiniteFloat | None = None
    longueur_rampant_haut: FiniteFloat | None = None
    longueur_rampant_bas: FiniteFloat | None = None
    hauteur_murette: FiniteFloat | None = None
    largeur: FiniteFloat | None = None

    @model_validator(mode="after")
    def non_nullable_fields(self):
        for field in ("type_physique_id", "valid"):
            if field in self.model_fields_set and getattr(self,field) is None:
                raise ValueError(f"{field} ne peut pas être nul.")
        return self


class TronconCreate(TronconFields):
    id: UUID | None = None
    type_physique_id: TronconType = "INDETERMINE"
    valid: bool = Field(default=True, strict=True)


class TronconUpdate(TronconFields):
    @model_validator(mode="after")
    def non_empty_patch(self):
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        return self


ObservationCreateParentResource = Literal[
    "desordres", "troncons", "ouvrages_hydrauliques", "equipements_mesure",
    "cheminements", "mobilier", "reseaux_techniques",
    "amenagements_hydrauliques", "zones_stockage", "vegetation", "ouvrages_particuliers",
    "prestations",
]
ObservationParentResource = ObservationCreateParentResource | Literal[
    "structures_amenagements_hydrauliques",
    "organes_protection_collective",
    "ouvrages_telecom_energie", "ouvrages_voirie", "reseaux_hydrauliques",
    "structures_digue", "profils_travers", "objets_mesures_hydrauliques",
    "structures_protection", "segments_hydrographiques", "berges",
    "traces_positions_troncons",
    "composants_berges", "elements_morphologiques_lit",
]


class ObservationParentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    resource: ObservationCreateParentResource
    id: UUID


class ObservationCreate(BaseModel):
    """Intention de création sérialisable avec identité définitive du client."""

    model_config = ConfigDict(extra="forbid")
    id: UUID
    parent: ObservationParentInput
    designation: str | None = None
    date: datetime.date | None = None
    evolution: str | None = None
    suite_apporter_id: str | None = None
    suite: str | None = None
    urgence_id: str | None = None
    nombre_desordres: int | None = Field(default=None, ge=0, le=2147483647, strict=True)
    valid: bool = Field(default=True, strict=True)

    @field_validator("designation", "evolution", "suite_apporter_id", "suite", "urgence_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        return value.strip() or None if value is not None else None

    @model_validator(mode="after")
    def validate_urgency_parent(self) -> "ObservationCreate":
        if self.urgence_id is not None and self.parent.resource != "desordres":
            raise ValueError("L'urgence est réservée aux Observations de Désordre.")
        return self


class ObservationParent(BaseModel):
    """Parent direct désigné par l'une des FK métier."""

    model_config = ConfigDict(extra="forbid")

    resource: ObservationParentResource
    id: UUID
    designation: str = Field(min_length=1)
    # Qualifie le Tronçon unifié parent direct, lorsqu'il est démontré.
    # Cette qualification de lecture n'ouvre pas leur création ou édition.
    type_physique_id: TronconType | None = None
    canonical_resource: Literal["troncons"] | None = None
    relation_role: str | None = None


class ObservationUpdate(BaseModel):
    """Édition partielle ; les champs Désordre sont contrôlés sur la ligne verrouillée."""

    model_config = ConfigDict(extra="forbid")

    designation: str | None = None
    date: datetime.date | None = None
    evolution: str | None = None
    suite_apporter_id: str | None = None
    suite: str | None = None
    urgence_id: str | None = None
    nombre_desordres: int | None = Field(default=None, ge=0, le=2147483647, strict=True)

    @field_validator("designation", "evolution", "suite_apporter_id", "suite", "urgence_id")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def require_update(self) -> "ObservationUpdate":
        if not self.model_fields_set:
            raise ValueError("Au moins un champ doit être fourni.")
        return self


class DesordreGeometryUpdate(BaseModel):
    """Modification cartographique d'une LineString ou d'un Polygon."""

    model_config = ConfigDict(extra="forbid")

    geometry: LineStringGeometry | PolygonGeometry
