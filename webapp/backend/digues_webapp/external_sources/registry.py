"""Registre versionné des organismes externes autorisés."""

from __future__ import annotations

from dataclasses import dataclass


LEGAL_PRIMARY = "LEGAL_PRIMARY"
STATE = "STATE"
TECHNICAL_PUBLIC = "TECHNICAL_PUBLIC"
PROFESSIONAL = "PROFESSIONAL"
LOCAL = "LOCAL"


@dataclass(frozen=True)
class ExternalSource:
    source_id: str
    organization: str
    domains: tuple[str, ...]
    category: str
    authority: str
    description: str
    adapter: str | None = None
    allow_subdomains: bool = True


DREAL_DOMAINS = (
    "auvergne-rhone-alpes.developpement-durable.gouv.fr",
    "bourgogne-franche-comte.developpement-durable.gouv.fr",
    "bretagne.developpement-durable.gouv.fr",
    "centre-val-de-loire.developpement-durable.gouv.fr",
    "corse.developpement-durable.gouv.fr",
    "grand-est.developpement-durable.gouv.fr",
    "hauts-de-france.developpement-durable.gouv.fr",
    "ile-de-france.developpement-durable.gouv.fr",
    "normandie.developpement-durable.gouv.fr",
    "nouvelle-aquitaine.developpement-durable.gouv.fr",
    "occitanie.developpement-durable.gouv.fr",
    "paca.developpement-durable.gouv.fr",
    "pays-de-la-loire.developpement-durable.gouv.fr",
)


EXTERNAL_SOURCE_REGISTRY = {
    source.source_id: source
    for source in (
        ExternalSource(
            "legifrance",
            "Légifrance",
            ("legifrance.gouv.fr",),
            LEGAL_PRIMARY,
            "Référence juridique primaire",
            "Textes législatifs et réglementaires en vigueur et consolidés.",
            adapter="legifrance_piste",
        ),
        ExternalSource(
            "ecologie",
            "Ministère chargé de l’écologie",
            ("ecologie.gouv.fr",),
            STATE,
            "Documentation institutionnelle de l’État",
            "GEMAPI, ouvrages hydrauliques et prévention des risques.",
        ),
        ExternalSource(
            "services_etat_territoriaux",
            "Services déconcentrés de l’État",
            DREAL_DOMAINS,
            STATE,
            "Documentation administrative territoriale",
            "Liste explicite des domaines DREAL régionaux ; DDT(M) et préfectures "
            "seront ajoutées au cas par cas.",
            allow_subdomains=False,
        ),
        ExternalSource(
            "georisques",
            "Géorisques",
            ("georisques.gouv.fr",),
            STATE,
            "Information publique sur les risques",
            "Portail national des risques naturels et technologiques.",
        ),
        ExternalSource(
            "vigicrues",
            "Vigicrues",
            ("vigicrues.gouv.fr",),
            STATE,
            "Information hydrologique publique",
            "Vigilance et information sur les crues.",
        ),
        ExternalSource(
            "ofb",
            "Office français de la biodiversité",
            ("ofb.gouv.fr",),
            STATE,
            "Expertise publique biodiversité",
            "Biodiversité et milieux aquatiques.",
        ),
        ExternalSource(
            "cerema",
            "Cerema",
            ("cerema.fr",),
            TECHNICAL_PUBLIC,
            "Documentation technique publique",
            "Expertise technique publique sur les territoires et les ouvrages.",
            adapter="cerema_html",
        ),
        ExternalSource(
            "inrae",
            "INRAE",
            ("inrae.fr",),
            TECHNICAL_PUBLIC,
            "Recherche scientifique publique",
            "Recherche sur l’eau, les risques et les ouvrages.",
        ),
        ExternalSource(
            "france_digues",
            "France Digues",
            ("france-digues.fr",),
            PROFESSIONAL,
            "Documentation professionnelle",
            "Pratiques, ressources métier et retours d’expérience des gestionnaires.",
            adapter="france_digues_html",
        ),
    )
}


def get_source(source_id: str) -> ExternalSource:
    return EXTERNAL_SOURCE_REGISTRY[source_id]


def functional_sources() -> tuple[ExternalSource, ...]:
    return tuple(source for source in EXTERNAL_SOURCE_REGISTRY.values() if source.adapter)
