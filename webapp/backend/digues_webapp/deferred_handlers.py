"""Handlers Digues conservés pour adaptation du schéma et de l’accès."""

from __future__ import annotations

import csv
import hashlib
import io
import logging
import os
from pathlib import Path
from typing import Annotated, Any, Literal
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from .basemap_catalog import basemap_catalog_payload
from .geometry_capabilities import (
    geometry_capabilities, load_geometry_model, require_geometry_contract_version,
)

from .desordre_catalog import register_routes as register_desordre_catalog_routes
from .desordre_pdf import register_routes as register_desordre_pdf_routes
from .photos import register_routes as register_photo_routes
from .resource_mutations import execute_mutation
from . import queries as lit_queries
from .resource_journal import mutation_origin, register_routes as register_resource_journal_routes
from .offline_projection import register_routes as register_offline_projection_routes
from .ai import AiServiceError, chat_with_mistral
from .database import (
    BusinessCRSMetadataError,
    WebDatabaseError,
    business_crs_payload,
    get_connection,
    get_sql_connection,
    get_write_connection,
    read_business_crs,
)
from .interactive_sql import (
    InteractiveSqlExecutionError,
    InteractiveSqlValidationError,
    execute_interactive_query,
)
from .models import (
    TronconLitCreate, TronconLitUpdate,
    ElementMorphologiqueLitCreate, ElementMorphologiqueLitUpdate,
    LitCharacteristicCreate, LitCharacteristicUpdate, LitCharacteristicFamily,
    PrestationCreate,
    PrestationUpdate,
    AmenagementHydrauliqueCreate,
    AmenagementHydrauliqueUpdate,
    ZoneStockageCreate,
    ZoneStockageUpdate,
    VegetationCreate,
    VegetationUpdate,
    CheminementCreate,
    CheminementUpdate,
    AiChatRequest,
    AiChatResponse,
    BaseSummaryResponse,
    DATA_VIEW_MAX_HIDDEN_TRONCON_IDS,
    DataCsvExportRequest,
    DataGeoPackageExportRequest,
    DataHomeViewQueryRequest,
    DataViewQueryRequest,
    DesordreCreate,
    DesordreAssociatedObjectsResponse,
    DesordreSearchResponse,
    DesordreSelectionResponse,
    DigueCreate,
    DigueUpdate,
    EquipementMesureCreate,
    EquipementMesureUpdate,
    LineEndpoints,
    DesordreGeometryUpdate,
    MobilierCreate,
    MobilierUpdate,
    ObservationCreate,
    ObservationUpdate,
    OuvrageParticulierCreate,
    OuvrageParticulierUpdate,
    PointDesordreUpdate,
    PointReperageUpdate,
    BornageGeometryQuery,
    ReseauTechniqueCreate,
    ReseauTechniqueUpdate,
    SystemeEndiguementCreate,
    SystemeEndiguementUpdate,
    TronconCreate,
    TronconUpdate,
    InteractiveSqlRequest,
    InteractiveSqlPreset,
)
from .data_views import (
    DataViewExportLimitError,
    DataViewValidationError,
    execute_home_view,
    execute_view_export,
    execute_view_query,
    view_catalog,
)
from .geopackage import DataGeoPackageError, build_geopackage
from .queries import (
    PrestationNotFoundError,
    PrestationValidationError,
    AmenagementHydrauliqueNotFoundError,
    AmenagementHydrauliqueValidationError,
    ZoneStockageNotFoundError,
    ZoneStockageValidationError,
    VegetationNotFoundError,
    VegetationValidationError,
    CheminementNotFoundError,
    CheminementValidationError,
    DesordreCreationError,
    DesordreAssociatedObjectsCursorError,
    DesordreNotFoundError,
    DesordreSearchCursorError,
    EquipementMesureNotFoundError,
    EquipementMesureValidationError,
    HeritageCreationError,
    HeritageNotFoundError,
    LineDesordreNotFoundError,
    LineDesordreUpdateError,
    MobilierNotFoundError,
    MobilierValidationError,
    ObservationNotFoundError,
    ObservationUpdateError,
    OuvrageParticulierNotFoundError,
    OuvrageParticulierValidationError,
    PointDesordreNotFoundError,
    PointReperageUnavailableError,
    PointReperageUpdateError,
    ReseauTechniqueNotFoundError,
    ReseauTechniqueValidationError,
    PointDesordreUpdateError,
    create_digue,
    create_cheminement,
    create_prestation,
    create_amenagement_hydraulique,
    create_zone_stockage,
    create_vegetation,
    create_desordre,
    create_equipement_mesure,
    create_mobilier,
    create_ouvrage_particulier,
    create_reseau_technique,
    create_systeme_endiguement,
    create_troncon,
    fetch_desordres,
    fetch_desordre_associated_objects,
    fetch_desordre_selection,
    fetch_cheminement,
    fetch_cheminement_collection,
    fetch_cheminement_observations,
    fetch_prestation,
    fetch_prestation_collection,
    fetch_prestation_options,
    fetch_prestation_observations,
    fetch_amenagement_hydraulique,
    fetch_amenagement_hydraulique_collection,
    fetch_amenagement_hydraulique_observations,
    fetch_amenagement_hydraulique_relation_options,
    fetch_zone_stockage,
    fetch_zone_stockage_collection,
    fetch_zone_stockage_relation_options,
    fetch_zone_stockage_observations,
    fetch_vegetation,
    fetch_vegetation_collection,
    fetch_vegetation_relations_options,
    fetch_vegetation_observations,
    fetch_desordre,
    fetch_desordre_observations,
    fetch_troncon_observations,
    fetch_equipement_mesure,
    fetch_equipement_mesure_collection,
    fetch_equipement_mesure_observations,
    fetch_mobilier,
    fetch_mobilier_collection,
    fetch_mobilier_observations,
    fetch_ouvrage_particulier,
    fetch_ouvrage_particulier_collection,
    fetch_ouvrage_particulier_observations,
    fetch_reseau_technique,
    fetch_reseau_technique_collection,
    fetch_reseau_technique_observations,
    fetch_digue,
    fetch_troncon_lit,
    fetch_element_morphologique,
    fetch_categories_desordre,
    fetch_reference_catalog,
    fetch_observation,
    create_observation,
    fetch_systemes_endiguement,
    fetch_systeme_endiguement,
    fetch_troncon,
    fetch_troncon_options,
    fetch_troncon_reperage_options,
    transform_position,
    position_vers_reperage,
    fetch_types_desordre,
    fetch_troncons,
    update_digue,
    update_cheminement,
    update_prestation,
    update_amenagement_hydraulique,
    update_zone_stockage,
    update_vegetation,
    update_equipement_mesure,
    update_line_desordre_geometry,
    update_line_desordre_endpoints,
    update_mobilier,
    update_ouvrage_particulier,
    update_reseau_technique,
    update_observation,
    update_point_desordre,
    update_point_reperage,
    update_systeme_endiguement,
    update_troncon,
    search_desordres,
)
from .schema_context import AiSchemaUnavailableError, get_ai_schema_context
from .sql_presets import list_sql_presets
from .territoire import (
    TerritoireConflictError,
    TerritoirePersistenceError,
    fetch_territoire_administratif,
    replace_territoire_administratif,
)
from .territoire_import import (
    MAX_TERRITORY_UPLOAD_BYTES,
    TerritoireImportConfigurationError,
    TerritoireImportError,
    import_territoire_geometry,
)
from .weather import (
    InvalidWeatherStepError,
    TerritoryRequiredError,
    WeatherConfigurationError,
    WeatherDataUnavailableError,
    WeatherServiceUnavailableError,
    fetch_precipitation_forecast_steps,
    fetch_precipitations,
)
from .vigicrues import (
    VigicruesDataError,
    VigicruesServiceUnavailableError,
    VigicruesTerritoryRequiredError,
)
from .hydrometry import (
    HydrometryDataError,
    HydrometryServiceUnavailableError,
    fetch_hydrometric_sites,
)


LOGGER = logging.getLogger(__name__)
CSV_FORMULA_PREFIXES = ("=", "+", "-", "@")


def _csv_export_value(value: Any) -> Any:
    """Neutralise uniquement les textes qu'un tableur pourrait exécuter."""
    if not isinstance(value, str):
        return value
    # Les espaces, tabulations et retours initiaux ne doivent pas permettre de
    # dissimuler un préfixe de formule. L'apostrophe précède la valeur intacte.
    if value.lstrip().startswith(CSV_FORMULA_PREFIXES):
        return "'" + value
    return value


def _log_api_error(label: str, exc: Exception) -> None:
    """Journalise une erreur de frontière sans exposer son détail au client."""

    LOGGER.error(
        "%s: %s",
        label,
        exc,
        exc_info=(type(exc), exc, exc.__traceback__),
    )


class GeoJSONResponse(JSONResponse):
    media_type = "application/geo+json"


async def read_limited_body(request: Request, *, limit: int) -> bytes:
    """Lit un corps HTTP brut sans dépasser la limite mémoire applicative."""

    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > limit:
            raise TerritoireImportError(
                "Le fichier importé dépasse la taille maximale."
            )
        chunks.append(chunk)
    return b"".join(chunks)


def register_deferred_handlers(application: FastAPI):
    """Needs the approved access policy and business schema before mounting."""
    @application.exception_handler(WebDatabaseError)
    async def database_error_handler(
        _request: Request, exc: WebDatabaseError
    ) -> JSONResponse:
        _log_api_error("Accès à la base de données impossible", exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "La base de données est temporairement indisponible."},
        )

    @application.exception_handler(BusinessCRSMetadataError)
    async def business_crs_metadata_error_handler(
        _request: Request, exc: BusinessCRSMetadataError
    ) -> JSONResponse:
        _log_api_error("Métadonnée CRS métier invalide", exc)
        return JSONResponse(status_code=503, content={"detail": str(exc)})

    @application.exception_handler(AiServiceError)
    async def ai_service_error_handler(
        _request: Request, exc: AiServiceError
    ) -> JSONResponse:
        _log_api_error("Assistant IA indisponible", exc)
        return JSONResponse(
            status_code=exc.status_code,
            content={"detail": str(exc)},
        )

    @application.exception_handler(AiSchemaUnavailableError)
    async def ai_schema_error_handler(
        _request: Request, exc: AiSchemaUnavailableError
    ) -> JSONResponse:
        _log_api_error("Schéma de l’assistant IA indisponible", exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "L’assistant IA est temporairement indisponible."},
        )

    @application.exception_handler(RequestValidationError)
    async def request_validation_error_handler(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        errors = exc.errors()
        message = "Les données envoyées sont invalides."
        if errors:
            first = errors[0]
            location = first.get("loc", ())
            resource = request.url.path.split("/")[-1]
            labels = {
                "troncons": "Tronçon", "desordres": "Désordre",
                "mobilier": "Mobilier", "equipements-mesure": "Équipement de mesure",
                "ouvrages-particuliers": "Ouvrage particulier",
                "cheminements": "Cheminement", "vegetation": "Végétation",
            }
            context_error = first.get("ctx", {}).get("error")
            if (resource in labels and tuple(location) == ("body", "geometry")
                    and first.get("type") in {"missing", "model_attributes_type"}):
                message = f"La géométrie du {labels[resource]} est obligatoire."
            elif isinstance(context_error, ValueError) and str(context_error).strip():
                message = str(context_error).strip()
            elif first.get("type") == "missing":
                message = "Un champ obligatoire est manquant."
        if not message.endswith((".", "!", "?")):
            message += "."
        return JSONResponse(status_code=422, content={"detail": message})

    @application.exception_handler(InteractiveSqlValidationError)
    async def interactive_sql_validation_error_handler(
        _request: Request, exc: InteractiveSqlValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=400,
            content={"error": {"message": str(exc)}},
        )

    @application.exception_handler(InteractiveSqlExecutionError)
    async def interactive_sql_execution_error_handler(
        _request: Request, exc: InteractiveSqlExecutionError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=408 if exc.timed_out else 400,
            content={"error": exc.public_detail()},
        )

    @application.exception_handler(PointDesordreNotFoundError)
    async def point_not_found_handler(
        _request: Request, exc: PointDesordreNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(PointDesordreUpdateError)
    async def point_update_error_handler(
        _request: Request, exc: PointDesordreUpdateError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(PointReperageUnavailableError)
    async def reperage_unavailable_handler(
        _request: Request, exc: PointReperageUnavailableError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(PointReperageUpdateError)
    async def reperage_update_error_handler(
        _request: Request, exc: PointReperageUpdateError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(DesordreNotFoundError)
    async def desordre_not_found_handler(
        _request: Request, exc: DesordreNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(LineDesordreNotFoundError)
    async def line_not_found_handler(
        _request: Request, exc: LineDesordreNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(LineDesordreUpdateError)
    async def line_update_error_handler(
        _request: Request, exc: LineDesordreUpdateError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(MobilierNotFoundError)
    async def mobilier_not_found_handler(
        _request: Request, exc: MobilierNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(MobilierValidationError)
    async def mobilier_validation_handler(
        _request: Request, exc: MobilierValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(EquipementMesureNotFoundError)
    async def equipement_mesure_not_found_handler(
        _request: Request, exc: EquipementMesureNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(EquipementMesureValidationError)
    async def equipement_mesure_validation_handler(
        _request: Request, exc: EquipementMesureValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(ReseauTechniqueNotFoundError)
    async def reseau_technique_not_found_handler(
        _request: Request, exc: ReseauTechniqueNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(ReseauTechniqueValidationError)
    async def reseau_technique_validation_handler(
        _request: Request, exc: ReseauTechniqueValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(OuvrageParticulierNotFoundError)
    async def ouvrage_particulier_not_found_handler(
        _request: Request, exc: OuvrageParticulierNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(OuvrageParticulierValidationError)
    async def ouvrage_particulier_validation_handler(
        _request: Request, exc: OuvrageParticulierValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(CheminementNotFoundError)
    async def cheminement_not_found_handler(
        _request: Request, exc: CheminementNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(CheminementValidationError)
    async def cheminement_validation_handler(
        _request: Request, exc: CheminementValidationError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(PrestationNotFoundError)
    async def prestation_not_found_handler(
        _request: Request, exc: PrestationNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(PrestationValidationError)
    async def prestation_validation_handler(
        _request: Request, exc: PrestationValidationError,
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(AmenagementHydrauliqueNotFoundError)
    async def amenagement_hydraulique_not_found_handler(
        _request: Request, exc: AmenagementHydrauliqueNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(AmenagementHydrauliqueValidationError)
    async def amenagement_hydraulique_validation_handler(
        _request: Request, exc: AmenagementHydrauliqueValidationError,
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(ZoneStockageNotFoundError)
    async def zone_stockage_not_found_handler(
        _request: Request, exc: ZoneStockageNotFoundError,
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(ZoneStockageValidationError)
    async def zone_stockage_validation_handler(
        _request: Request, exc: ZoneStockageValidationError,
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(VegetationNotFoundError)
    async def vegetation_not_found_handler(_request: Request, exc: VegetationNotFoundError) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(VegetationValidationError)
    async def vegetation_validation_handler(_request: Request, exc: VegetationValidationError) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(ObservationNotFoundError)
    async def observation_not_found_handler(
        _request: Request, exc: ObservationNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(ObservationUpdateError)
    async def observation_update_error_handler(
        _request: Request, exc: ObservationUpdateError
    ) -> JSONResponse:
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    @application.exception_handler(HeritageCreationError)
    async def heritage_creation_error_handler(
        _request: Request, exc: HeritageCreationError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(HeritageNotFoundError)
    async def heritage_not_found_handler(
        _request: Request, exc: HeritageNotFoundError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(DesordreCreationError)
    async def desordre_creation_error_handler(
        _request: Request, exc: DesordreCreationError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(TerritoireImportError)
    async def territoire_import_error_handler(
        _request: Request, exc: TerritoireImportError
    ) -> JSONResponse:
        content: dict[str, Any] = {"detail": str(exc)}
        if exc.available_layers:
            content["layers"] = exc.available_layers
        return JSONResponse(status_code=400, content=content)

    @application.exception_handler(TerritoireImportConfigurationError)
    async def territoire_import_configuration_error_handler(
        _request: Request, exc: TerritoireImportConfigurationError
    ) -> JSONResponse:
        _log_api_error("Configuration de l’import de territoire invalide", exc)
        return JSONResponse(
            status_code=503,
            content={
                "detail": "L’import de territoire n’est pas configuré sur le serveur."
            },
        )

    @application.exception_handler(TerritoirePersistenceError)
    async def territoire_persistence_error_handler(
        _request: Request, exc: TerritoirePersistenceError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(TerritoireConflictError)
    async def territoire_conflict_error_handler(
        _request: Request, exc: TerritoireConflictError
    ) -> JSONResponse:
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    @application.exception_handler(TerritoryRequiredError)
    async def weather_territory_required_handler(
        _request: Request, exc: TerritoryRequiredError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(WeatherConfigurationError)
    async def weather_configuration_error_handler(
        _request: Request, exc: WeatherConfigurationError
    ) -> JSONResponse:
        _log_api_error("Configuration météo invalide", exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "La météo n’est pas configurée sur le serveur."},
        )

    @application.exception_handler(WeatherDataUnavailableError)
    async def weather_data_unavailable_handler(
        _request: Request, exc: WeatherDataUnavailableError
    ) -> JSONResponse:
        _log_api_error("Données météo indisponibles", exc)
        return JSONResponse(
            status_code=502,
            content={"detail": "Les données météo demandées sont indisponibles."},
        )

    @application.exception_handler(WeatherServiceUnavailableError)
    async def weather_service_unavailable_handler(
        _request: Request, exc: WeatherServiceUnavailableError
    ) -> JSONResponse:
        _log_api_error("Service météo indisponible", exc)
        public_messages = {
            "Météo-France n’a pas répondu à temps.",
            "Météo-France a refusé la demande.",
            "Météo-France est temporairement indisponible.",
        }
        message = str(exc)
        if message not in public_messages:
            message = "Météo-France est temporairement indisponible."
        return JSONResponse(status_code=503, content={"detail": message})

    @application.exception_handler(InvalidWeatherStepError)
    async def invalid_weather_step_error_handler(
        _request: Request, exc: InvalidWeatherStepError
    ) -> JSONResponse:
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    @application.exception_handler(VigicruesTerritoryRequiredError)
    async def vigicrues_territory_required_handler(
        _request: Request, exc: VigicruesTerritoryRequiredError
    ) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": str(exc)})

    @application.exception_handler(HydrometryDataError)
    async def hydrometry_data_error_handler(
        _request: Request, exc: HydrometryDataError
    ) -> JSONResponse:
        _log_api_error("Données hydrométriques invalides", exc)
        return JSONResponse(
            status_code=502,
            content={"detail": "Les données hydrométriques reçues sont invalides."},
        )

    @application.exception_handler(HydrometryServiceUnavailableError)
    async def hydrometry_service_error_handler(
        _request: Request, exc: HydrometryServiceUnavailableError
    ) -> JSONResponse:
        _log_api_error("Service hydrométrique indisponible", exc)
        return JSONResponse(
            status_code=503,
            content={"detail": "Un service hydrométrique est temporairement indisponible."},
        )

    @application.exception_handler(VigicruesDataError)
    async def vigicrues_data_error_handler(
        _request: Request, exc: VigicruesDataError
    ) -> JSONResponse:
        _log_api_error("Données Vigicrues invalides", exc)
        return JSONResponse(
            status_code=502,
            content={"detail": "Les données Vigicrues reçues sont invalides."},
        )

    @application.exception_handler(VigicruesServiceUnavailableError)
    async def vigicrues_service_error_handler(
        _request: Request, exc: VigicruesServiceUnavailableError
    ) -> JSONResponse:
        _log_api_error("Service Vigicrues indisponible", exc)
        public_messages = {
            "Vigicrues n’a pas répondu à temps.",
            "Vigicrues est temporairement indisponible.",
        }
        message = str(exc)
        if message not in public_messages:
            message = "Vigicrues est temporairement indisponible."
        return JSONResponse(status_code=503, content={"detail": message})

    @application.get("/api/base-summary", response_model=BaseSummaryResponse)
    def base_summary(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        """Expose les informations générales fiables de la base active."""

        crs = read_business_crs(connection)
        return {"crs": business_crs_payload(crs)}

    @application.get(
        "/api/territoire-administratif",
        response_class=GeoJSONResponse,
    )
    def territoire_administratif(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_territoire_administratif(connection)

    @application.get(
        "/api/hydrometrie/sites",
        response_class=GeoJSONResponse,
    )
    def hydrometric_sites(
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return fetch_hydrometric_sites(connection)

    @application.get("/api/meteo/precipitations")
    def meteo_precipitations(
        valid_time: str | None = Query(None),
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_precipitations(connection, valid_time=valid_time)

    @application.get("/api/meteo/precipitations/echances")
    def meteo_precipitation_forecast_steps(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_precipitation_forecast_steps(connection)

    @application.post(
        "/api/territoire-administratif/import",
        response_class=GeoJSONResponse,
    )
    async def import_territoire_administratif(
        request: Request,
        libelle: str = Query(...),
        replace: bool = Query(False),
        layer: str | None = Query(None),
        x_filename: str | None = Header(default=None, alias="X-Filename"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        payload = await read_limited_body(
            request,
            limit=MAX_TERRITORY_UPLOAD_BYTES,
        )
        business_crs = read_business_crs(connection)
        imported = import_territoire_geometry(
            payload,
            business_crs=business_crs,
            filename=x_filename or "",
            content_type=request.headers.get("content-type", ""),
            layer_name=layer,
        )
        return replace_territoire_administratif(
            connection,
            libelle=libelle,
            wkb=imported.wkb,
            target_srid=imported.target_srid,
            replace=replace,
        )

    @application.post("/api/ai/chat")
    def ai_chat(request: AiChatRequest) -> dict[str, Any]:
        recent_messages = [
            message.model_dump() for message in request.recent_messages
        ]
        schema_context = get_ai_schema_context()
        result = chat_with_mistral(
            recent_messages,
            schema_context,
            conversation_summary=request.conversation_summary,
            current_message=request.message,
        )
        return AiChatResponse(
            answer=result.answer,
            conversation_summary=result.conversation_summary,
            recent_messages=list(result.recent_messages),
            executed_queries=[{"sql": sql} for sql in result.executed_queries],
            consulted_sources=[
                {
                    "title": source.title,
                    "path": source.path,
                    "heading": source.heading,
                }
                for source in result.consulted_sources
            ],
            external_sources=[
                {
                    "title": source.title,
                    "organization": source.organization,
                    "url": source.url,
                    "source_id": source.source_id,
                    "category": source.category,
                    "document_date": source.document_date,
                }
                for source in result.external_sources
            ],
        ).model_dump()

    @application.post("/api/sql/query")
    def interactive_sql_query(
        request: InteractiveSqlRequest,
        connection: Any = Depends(get_sql_connection),
    ) -> dict[str, Any]:
        return execute_interactive_query(connection, request.sql)

    @application.get(
        "/api/sql/presets",
        response_model=list[InteractiveSqlPreset],
    )
    def interactive_sql_presets() -> list[dict[str, str]]:
        return list_sql_presets()

    @application.get("/api/troncons", response_class=GeoJSONResponse)
    def troncons(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_troncons(connection)

    @application.get("/api/systemes-endiguement")
    def systemes_endiguement(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_systemes_endiguement(connection)

    @application.get("/api/systemes-endiguement/{systeme_id}")
    def systeme_endiguement_detail(
        systeme_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_systeme_endiguement(connection, systeme_id)

    @application.get("/api/digues/{digue_id}")
    def digue_detail(
        digue_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_digue(connection, digue_id)

    @application.get("/api/troncons-lit/options")
    def lit_options(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return lit_queries.fetch_lit_options(connection)

    @application.get("/api/troncons-lit", response_class=GeoJSONResponse)
    def troncons_lit_collection(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return lit_queries.fetch_troncon_lit_collection(connection)

    @application.post("/api/troncons-lit", status_code=201, response_class=GeoJSONResponse)
    def create_new_troncon_lit(
        creation: TronconLitCreate,
        operation_id: str = Header(alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        geometry_contract_version: str | None = Header(default=None, alias="Geometry-Contract-Version"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        def write():
            require_geometry_contract_version("troncons-lit", geometry_contract_version)
            return lit_queries.create_troncon_lit(connection, creation)
        return execute_mutation(
            connection, "troncons-lit", "create", write, identifier=creation.id,
            operation_id=operation_id, origin=origin,
            changes=creation.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/troncons-lit/{troncon_lit_id}", response_class=GeoJSONResponse)
    def troncon_lit_detail(
        troncon_lit_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        from .business_routes import require_object

        require_object(connection, "segments_hydrographiques", troncon_lit_id)
        return fetch_troncon_lit(connection, troncon_lit_id)

    @application.put("/api/troncons-lit/{troncon_lit_id}", response_class=GeoJSONResponse)
    def edit_troncon_lit(
        troncon_lit_id: UUID, update: TronconLitUpdate,
        operation_id: str = Header(alias="Operation-Id"),
        if_match: str | None = Header(default=None, alias="If-Match"),
        geometry_contract_version: str | None = Header(default=None, alias="Geometry-Contract-Version"),
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        def write():
            require_geometry_contract_version("troncons-lit", geometry_contract_version)
            return lit_queries.update_troncon_lit(connection, troncon_lit_id, update)
        return execute_mutation(
            connection, "troncons-lit", "update", write, identifier=troncon_lit_id,
            if_match=if_match, operation_id=operation_id, origin=origin,
            changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.post("/api/troncons-lit/{troncon_lit_id}/caracteristiques/{famille}",
                      status_code=201, response_class=GeoJSONResponse)
    def create_lit_characteristic(
        troncon_lit_id: UUID, famille: LitCharacteristicFamily,
        creation: LitCharacteristicCreate,
        operation_id: str = Header(alias="Operation-Id"),
        if_match: str | None = Header(default=None, alias="If-Match"),
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        try:
            creation.validate_family(famille)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return execute_mutation(
            connection, "troncons-lit", "update",
            lambda: lit_queries.create_lit_characteristic(connection, troncon_lit_id, famille, creation),
            identifier=troncon_lit_id, if_match=if_match, operation_id=operation_id, origin=origin,
            changes={"characteristicAction": "create", "family": famille,
                     "componentId": str(creation.id),
                     "changes": creation.model_dump(mode="json", exclude_unset=True)},
        )

    @application.put("/api/troncons-lit/{troncon_lit_id}/caracteristiques/{famille}/{component_id}",
                     response_class=GeoJSONResponse)
    def edit_lit_characteristic(
        troncon_lit_id: UUID, famille: LitCharacteristicFamily, component_id: UUID,
        update: LitCharacteristicUpdate,
        operation_id: str = Header(alias="Operation-Id"),
        if_match: str | None = Header(default=None, alias="If-Match"),
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        try:
            update.validate_family(famille)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return execute_mutation(
            connection, "troncons-lit", "update",
            lambda: lit_queries.update_lit_characteristic(connection, troncon_lit_id, famille, component_id, update),
            identifier=troncon_lit_id, if_match=if_match, operation_id=operation_id, origin=origin,
            changes={"characteristicAction": "update", "family": famille,
                     "componentId": str(component_id),
                     "changes": update.model_dump(mode="json", exclude_unset=True)},
        )

    @application.get("/api/elements-morphologiques-lit", response_class=GeoJSONResponse)
    def elements_morphologiques_lit_collection(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return lit_queries.fetch_element_morphologique_collection(connection)

    @application.post("/api/elements-morphologiques-lit", status_code=201, response_class=GeoJSONResponse)
    def create_new_element_morphologique_lit(
        creation: ElementMorphologiqueLitCreate,
        operation_id: str = Header(alias="Operation-Id"),
        geometry_contract_version: str | None = Header(default=None, alias="Geometry-Contract-Version"),
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        def write():
            require_geometry_contract_version("elements-morphologiques-lit", geometry_contract_version)
            return lit_queries.create_element_morphologique(connection, creation)
        return execute_mutation(
            connection, "elements-morphologiques-lit", "create", write,
            identifier=creation.id, operation_id=operation_id, origin=origin,
            changes=creation.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/elements-morphologiques-lit/{element_id}", response_class=GeoJSONResponse)
    def element_morphologique_detail(
        element_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        from .business_routes import require_object

        require_object(connection, "elements_morphologiques_lit", element_id)
        return fetch_element_morphologique(connection, element_id)

    @application.put("/api/elements-morphologiques-lit/{element_id}", response_class=GeoJSONResponse)
    def edit_element_morphologique_lit(
        element_id: UUID, update: ElementMorphologiqueLitUpdate,
        operation_id: str = Header(alias="Operation-Id"),
        if_match: str | None = Header(default=None, alias="If-Match"),
        geometry_contract_version: str | None = Header(default=None, alias="Geometry-Contract-Version"),
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        def write():
            require_geometry_contract_version("elements-morphologiques-lit", geometry_contract_version)
            return lit_queries.update_element_morphologique(connection, element_id, update)
        return execute_mutation(
            connection, "elements-morphologiques-lit", "update", write,
            identifier=element_id, if_match=if_match, operation_id=operation_id, origin=origin,
            changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/referentiels/types-desordre")
    def types_desordre(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_types_desordre(connection)

    @application.get("/api/referentiels/categories-desordre")
    def categories_desordre(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_categories_desordre(connection)

    @application.get("/api/referentiels/{catalog}")
    def reference_catalog(
        catalog: str,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        try:
            return fetch_reference_catalog(connection, catalog)
        except KeyError as exc:
            raise HTTPException(
                status_code=404, detail="Référentiel inconnu."
            ) from exc

    @application.get("/api/troncons/options")
    def troncon_options(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_troncon_options(connection)

    @application.get("/api/troncons/{troncon_id}", response_class=GeoJSONResponse)
    def troncon_detail(
        troncon_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_troncon(connection, troncon_id)

    @application.get("/api/troncons/{troncon_id}/reperage-options")
    def troncon_reperage_options(
        troncon_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_troncon_reperage_options(connection, troncon_id)

    @application.get("/api/coordinates/transform")
    def transform_coordinates(
        longitude: float, latitude: float,
        connection: Any = Depends(get_connection),
    ) -> dict[str, float]:
        return transform_position(connection, longitude, latitude)

    @application.get("/api/troncons/{troncon_id}/geometrie-bornage")
    def calculate_bornage_geometry(
        troncon_id: UUID,
        systeme_reperage_id: UUID,
        borne_debut_id: UUID,
        distance_debut_m: float = Query(ge=0, allow_inf_nan=False),
        position_debut_relative: Literal["AVANT_BORNE", "SUR_BORNE", "APRES_BORNE"] = Query(),
        borne_fin_id: UUID | None = None,
        distance_fin_m: float | None = Query(default=None, ge=0, allow_inf_nan=False),
        position_fin_relative: Literal["AVANT_BORNE", "SUR_BORNE", "APRES_BORNE"] | None = None,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        try:
            command = BornageGeometryQuery(
                systeme_reperage_id=systeme_reperage_id, borne_debut_id=borne_debut_id,
                distance_debut_m=distance_debut_m, position_debut_relative=position_debut_relative,
                borne_fin_id=borne_fin_id, distance_fin_m=distance_fin_m, position_fin_relative=position_fin_relative,
            )
        except ValueError as exc:
            raise HTTPException(422, "Bornage incomplet ou incohérent.") from exc
        return lit_queries.geometry_from_bornage(connection, troncon_id, command)

    @application.get("/api/troncons/{troncon_id}/position-reperage")
    def calculate_position_reperage(
        troncon_id: UUID, systeme_reperage_id: UUID,
        longitude: float, latitude: float,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return position_vers_reperage(
            connection, troncon_id, systeme_reperage_id, longitude, latitude)

    @application.post("/api/systemes-endiguement", status_code=201)
    def create_systeme(
        creation: SystemeEndiguementCreate,
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(connection, "systemes", "create",
                                lambda: create_systeme_endiguement(connection, creation), origin=origin)

    @application.put("/api/systemes-endiguement/{systeme_id}")
    def edit_systeme(
        systeme_id: UUID,
        update: SystemeEndiguementUpdate,
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "systemes", "update",
            lambda: update_systeme_endiguement(connection, systeme_id, update),
            identifier=systeme_id, if_match=if_match, origin=origin,
        )

    @application.post("/api/digues", status_code=201)
    def create_new_digue(
        creation: DigueCreate,
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(connection, "digues", "create",
                                lambda: create_digue(connection, creation), origin=origin)

    @application.put("/api/digues/{digue_id}")
    def edit_digue(
        digue_id: UUID,
        update: DigueUpdate,
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "digues", "update",
            lambda: update_digue(connection, digue_id, update),
            identifier=digue_id, if_match=if_match, origin=origin,
        )

    @application.post(
        "/api/troncons", status_code=201, response_class=GeoJSONResponse
    )
    def create_new_troncon(
        creation: TronconCreate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(connection, "troncons", "create",
                                lambda: create_troncon(connection, creation), origin=origin, identifier=creation.id,
                                operation_id=operation_id, changes=creation.model_dump(mode="json",exclude_unset=True))

    @application.put(
        "/api/troncons/{troncon_id}", response_class=GeoJSONResponse
    )
    def edit_troncon(
        troncon_id: UUID,
        update: TronconUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "troncons", "update",
            lambda: update_troncon(connection, troncon_id, update),
            identifier=troncon_id, if_match=if_match, origin=origin,
            operation_id=operation_id, changes=update.model_dump(mode="json",exclude_unset=True),
        )

    @application.get("/api/desordres", response_class=GeoJSONResponse)
    def desordres(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_desordres(connection)

    @application.get("/api/desordres/search", response_model=DesordreSearchResponse)
    def desordres_search(
        q: Annotated[str, Query(max_length=200)] = "",
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=2_048)] = None,
        troncon_id: UUID | None = None,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        try:
            return search_desordres(
                connection, query=q, limit=limit, cursor=cursor,
                troncon_id=troncon_id,
            )
        except DesordreSearchCursorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get(
        "/api/desordres/selection", response_model=DesordreSelectionResponse,
    )
    def desordres_selection(
        ids: list[UUID] = Query(),
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        if len(ids) > 100:
            raise HTTPException(
                status_code=400,
                detail="La sélection est limitée à 100 identifiants par requête.",
            )
        return fetch_desordre_selection(connection, ids)

    @application.get(
        "/api/desordres/{desordre_id}/objets-associes",
        response_model=DesordreAssociatedObjectsResponse,
    )
    def desordre_associated_objects(
        desordre_id: UUID,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: Annotated[str | None, Query(max_length=2_048)] = None,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        try:
            return fetch_desordre_associated_objects(
                connection, desordre_id, limit=limit, cursor=cursor,
            )
        except DesordreAssociatedObjectsCursorError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.get("/api/mobilier", response_class=GeoJSONResponse)
    def mobilier_collection(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_mobilier_collection(connection)

    @application.post(
        "/api/mobilier", status_code=201, response_class=GeoJSONResponse
    )
    def create_new_mobilier(
        creation: MobilierCreate,
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "mobilier", "create",
            lambda: create_mobilier(connection, creation), origin=origin,
        )

    @application.get(
        "/api/mobilier/{mobilier_id}", response_class=GeoJSONResponse
    )
    def mobilier_detail(
        mobilier_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_mobilier(connection, mobilier_id)

    @application.put(
        "/api/mobilier/{mobilier_id}", response_class=GeoJSONResponse
    )
    def edit_mobilier(
        mobilier_id: UUID,
        update: MobilierUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "mobilier", "update",
            lambda: update_mobilier(connection, mobilier_id, update),
            identifier=mobilier_id,
            if_match=if_match,
            origin=origin,
            operation_id=operation_id,
            changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/mobilier/{mobilier_id}/observations")
    def mobilier_observations(
        mobilier_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_mobilier_observations(connection, mobilier_id)

    @application.get("/api/equipements-mesure", response_class=GeoJSONResponse)
    def equipements_mesure_collection(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_equipement_mesure_collection(connection)

    @application.post(
        "/api/equipements-mesure", status_code=201,
        response_class=GeoJSONResponse,
    )
    def create_new_equipement_mesure(
        creation: EquipementMesureCreate,
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "equipements-mesure", "create",
            lambda: create_equipement_mesure(connection, creation), origin=origin,
        )

    @application.get(
        "/api/equipements-mesure/{identifier}", response_class=GeoJSONResponse,
    )
    def equipement_mesure_detail(
        identifier: UUID, connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_equipement_mesure(connection, identifier)

    @application.put(
        "/api/equipements-mesure/{identifier}", response_class=GeoJSONResponse,
    )
    def edit_equipement_mesure(
        identifier: UUID,
        update: EquipementMesureUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "equipements-mesure", "update",
            lambda: update_equipement_mesure(connection, identifier, update),
            identifier=identifier, if_match=if_match, origin=origin,
            operation_id=operation_id,
            changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/equipements-mesure/{identifier}/observations")
    def equipement_mesure_observations(
        identifier: UUID, connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_equipement_mesure_observations(connection, identifier)

    @application.get("/api/reseaux-techniques", response_class=GeoJSONResponse)
    def reseaux_techniques_collection(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_reseau_technique_collection(connection)

    @application.post(
        "/api/reseaux-techniques", status_code=201,
        response_class=GeoJSONResponse,
    )
    def create_new_reseau_technique(
        creation: ReseauTechniqueCreate,
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "reseaux-techniques", "create",
            lambda: create_reseau_technique(connection, creation), origin=origin,
        )

    @application.get(
        "/api/reseaux-techniques/{identifier}", response_class=GeoJSONResponse,
    )
    def reseau_technique_detail(
        identifier: UUID, connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_reseau_technique(connection, identifier)

    @application.put(
        "/api/reseaux-techniques/{identifier}", response_class=GeoJSONResponse,
    )
    def edit_reseau_technique(
        identifier: UUID,
        update: ReseauTechniqueUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "reseaux-techniques", "update",
            lambda: update_reseau_technique(connection, identifier, update),
            identifier=identifier, if_match=if_match, origin=origin,
            operation_id=operation_id,
            changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/reseaux-techniques/{identifier}/observations")
    def reseau_technique_observations(
        identifier: UUID, connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_reseau_technique_observations(connection, identifier)



    @application.get("/api/prestations", response_class=GeoJSONResponse)
    def prestations_collection(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_prestation_collection(connection)

    @application.get("/api/prestations/relations/options")
    def prestations_relation_options(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_prestation_options(connection)

    @application.post("/api/prestations", status_code=201, response_class=GeoJSONResponse)
    def create_new_prestation(
        creation: PrestationCreate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "prestations", "create",
            lambda: create_prestation(connection, creation), origin=origin,
            identifier=creation.id, operation_id=operation_id,
            changes=creation.model_dump(mode="json"),
        )

    @application.get("/api/prestations/{identifier}", response_class=GeoJSONResponse)
    def prestation_detail(identifier: UUID, connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_prestation(connection, identifier)

    @application.put("/api/prestations/{identifier}", response_class=GeoJSONResponse)
    def edit_prestation(
        identifier: UUID, update: PrestationUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "prestations", "update",
            lambda: update_prestation(connection, identifier, update),
            identifier=identifier, if_match=if_match, origin=origin,
            operation_id=operation_id,
            changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/prestations/{identifier}/observations")
    def prestation_observations(
        identifier: UUID, connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_prestation_observations(connection, identifier)

    @application.get("/api/amenagements-hydrauliques", response_class=GeoJSONResponse)
    def amenagement_hydraulique_collection(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_amenagement_hydraulique_collection(connection)

    @application.get("/api/amenagements-hydrauliques/relations/options")
    def amenagement_hydraulique_relation_options(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_amenagement_hydraulique_relation_options(connection)

    @application.post(
        "/api/amenagements-hydrauliques", status_code=201,
        response_class=GeoJSONResponse,
    )
    def create_new_amenagement_hydraulique(
        creation: AmenagementHydrauliqueCreate,
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "amenagements-hydrauliques", "create",
            lambda: create_amenagement_hydraulique(connection, creation),
            origin=origin,
        )

    @application.get(
        "/api/amenagements-hydrauliques/{identifier}",
        response_class=GeoJSONResponse,
    )
    def amenagement_hydraulique_detail(
        identifier: UUID, connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_amenagement_hydraulique(connection, identifier)

    @application.put(
        "/api/amenagements-hydrauliques/{identifier}",
        response_class=GeoJSONResponse,
    )
    def edit_amenagement_hydraulique(
        identifier: UUID,
        update: AmenagementHydrauliqueUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "amenagements-hydrauliques", "update",
            lambda: update_amenagement_hydraulique(connection, identifier, update),
            identifier=identifier, if_match=if_match, origin=origin,
            operation_id=operation_id,
            changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/amenagements-hydrauliques/{identifier}/observations")
    def amenagement_hydraulique_observations(
        identifier: UUID, connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_amenagement_hydraulique_observations(connection, identifier)

    @application.get("/api/zones-stockage", response_class=GeoJSONResponse)
    def zone_stockage_collection(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_zone_stockage_collection(connection)

    @application.get("/api/zones-stockage/relations/options")
    def zone_stockage_relation_options(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_zone_stockage_relation_options(connection)

    @application.post("/api/zones-stockage", status_code=201, response_class=GeoJSONResponse)
    def create_new_zone_stockage(
        creation: ZoneStockageCreate, origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(connection, "zones-stockage", "create",
                                lambda: create_zone_stockage(connection, creation), origin=origin)

    @application.get("/api/zones-stockage/{identifier}", response_class=GeoJSONResponse)
    def zone_stockage_detail(identifier: UUID, connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_zone_stockage(connection, identifier)

    @application.put("/api/zones-stockage/{identifier}", response_class=GeoJSONResponse)
    def edit_zone_stockage(
        identifier: UUID, update: ZoneStockageUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "zones-stockage", "update",
            lambda: update_zone_stockage(connection, identifier, update),
            identifier=identifier, if_match=if_match, origin=origin,
            operation_id=operation_id,
            changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/zones-stockage/{identifier}/observations")
    def zone_stockage_observations(
        identifier: UUID, connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_zone_stockage_observations(connection, identifier)

    @application.get("/api/vegetation", response_class=GeoJSONResponse)
    def vegetation_collection(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_vegetation_collection(connection)

    @application.get("/api/vegetation/relations/options")
    def vegetation_relations_options(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_vegetation_relations_options(connection)

    @application.post("/api/vegetation", status_code=201, response_class=GeoJSONResponse)
    def create_new_vegetation(
        creation: VegetationCreate, origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "vegetation", "create",
            lambda: create_vegetation(connection, creation), origin=origin,
        )

    @application.get("/api/vegetation/{identifier}", response_class=GeoJSONResponse)
    def vegetation_detail(identifier: UUID, connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_vegetation(connection, identifier)

    @application.put("/api/vegetation/{identifier}", response_class=GeoJSONResponse)
    def edit_vegetation(
        identifier: UUID, update: VegetationUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "vegetation", "update",
            lambda: update_vegetation(connection, identifier, update),
            identifier=identifier, if_match=if_match, origin=origin,
            operation_id=operation_id,
            changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/vegetation/{identifier}/observations")
    def vegetation_observations(identifier: UUID, connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_vegetation_observations(connection, identifier)

    @application.get("/api/cheminements", response_class=GeoJSONResponse)
    def cheminements_collection(connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_cheminement_collection(connection)

    @application.post("/api/cheminements", status_code=201, response_class=GeoJSONResponse)
    def create_new_cheminement(
        creation: CheminementCreate,
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "cheminements", "create",
            lambda: create_cheminement(connection, creation), origin=origin,
        )

    @application.get("/api/cheminements/{identifier}", response_class=GeoJSONResponse)
    def cheminement_detail(identifier: UUID, connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_cheminement(connection, identifier)

    @application.put("/api/cheminements/{identifier}", response_class=GeoJSONResponse)
    def edit_cheminement(
        identifier: UUID, update: CheminementUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "cheminements", "update",
            lambda: update_cheminement(connection, identifier, update),
            identifier=identifier, if_match=if_match, origin=origin,
            operation_id=operation_id,
            changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/cheminements/{identifier}/observations")
    def cheminement_observations(identifier: UUID, connection: Any = Depends(get_connection)) -> dict[str, Any]:
        return fetch_cheminement_observations(connection, identifier)

    @application.get("/api/geometry-capabilities/{resource}")
    def entity_geometry_capabilities(
        resource: str, connection: Any = Depends(get_connection),
    ) -> JSONResponse:
        return JSONResponse(geometry_capabilities(resource), headers={"Cache-Control": "no-store"})

    @application.get("/api/ouvrages-particuliers", response_class=GeoJSONResponse)
    def ouvrages_particuliers_collection(
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_ouvrage_particulier_collection(connection)

    @application.post(
        "/api/ouvrages-particuliers", status_code=201,
        response_class=GeoJSONResponse,
    )
    def create_new_ouvrage_particulier(
        creation: OuvrageParticulierCreate,
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
        geometry_contract_version: str | None = Header(default=None, alias="Geometry-Contract-Version"),
    ) -> dict[str, Any]:
        def create_with_contract():
            require_geometry_contract_version("ouvrages-particuliers", geometry_contract_version)
            return create_ouvrage_particulier(connection, creation)

        return execute_mutation(
            connection, "ouvrages-particuliers", "create",
            create_with_contract, origin=origin,
        )

    @application.get(
        "/api/ouvrages-particuliers/{identifier}", response_class=GeoJSONResponse,
    )
    def ouvrage_particulier_detail(
        identifier: UUID, connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_ouvrage_particulier(connection, identifier)

    @application.put(
        "/api/ouvrages-particuliers/{identifier}", response_class=GeoJSONResponse,
    )
    def edit_ouvrage_particulier(
        identifier: UUID,
        update: OuvrageParticulierUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
        geometry_contract_version: str | None = Header(default=None, alias="Geometry-Contract-Version"),
    ) -> dict[str, Any]:
        def update_with_contract():
            require_geometry_contract_version("ouvrages-particuliers", geometry_contract_version)
            return update_ouvrage_particulier(connection, identifier, update)

        return execute_mutation(
            connection, "ouvrages-particuliers", "update",
            update_with_contract,
            identifier=identifier, if_match=if_match, origin=origin,
            operation_id=operation_id,
            changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.get("/api/ouvrages-particuliers/{identifier}/observations")
    def ouvrage_particulier_observations(
        identifier: UUID, connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_ouvrage_particulier_observations(connection, identifier)

    @application.get("/api/data/desordres")
    def data_desordres(
        connection: Any = Depends(get_connection),
        hidden_troncon_ids: Annotated[list[UUID] | None, Query()] = None,
        offset: Annotated[int, Query(ge=0)] = 0,
        page_size: Annotated[int, Query(ge=1, le=2_000)] = 2_000,
    ) -> dict[str, Any]:
        hidden_ids = hidden_troncon_ids or []
        if len(hidden_ids) > DATA_VIEW_MAX_HIDDEN_TRONCON_IDS:
            raise HTTPException(
                status_code=422, detail="Filtre Patrimoine trop volumineux."
            )
        return execute_home_view(
            connection, hidden_ids, offset, page_size
        )

    @application.post("/api/data/desordres/query")
    def data_desordres_query(
        request: DataHomeViewQueryRequest,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return execute_home_view(
            connection,
            request.hidden_troncon_ids,
            request.offset,
            request.page_size,
        )

    @application.get("/api/data/view-catalog")
    def data_view_catalog() -> dict[str, Any]:
        return view_catalog()

    @application.post("/api/data/view-query")
    def data_view_query(
        request: DataViewQueryRequest,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        try:
            return execute_view_query(
                connection,
                request.root,
                request.fields,
                request.hidden_troncon_ids,
                request.offset,
                request.page_size,
            )
        except DataViewValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @application.post("/api/data/export-gpkg")
    def data_geopackage_export(
        request: DataGeoPackageExportRequest,
        connection: Any = Depends(get_connection),
    ) -> Response:
        try:
            result = execute_view_export(
                connection,
                request.root,
                request.fields,
                request.hidden_troncon_ids,
                **({"include_native_troncons": True} if "troncons" in request.entity_classes else {}),
            )
            content = build_geopackage(result, request.entity_classes)
        except (DataGeoPackageError, DataViewValidationError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DataViewExportLimitError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        return Response(
            content=content,
            media_type="application/geopackage+sqlite3",
            headers={
                "Content-Disposition": 'attachment; filename="digues-donnees.gpkg"',
            },
        )

    @application.post("/api/data/export-csv")
    def data_csv_export(
        request: DataCsvExportRequest,
        connection: Any = Depends(get_connection),
    ) -> Response:
        try:
            result = execute_view_export(
                connection,
                request.root,
                request.fields,
                request.hidden_troncon_ids,
            )
        except DataViewValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except DataViewExportLimitError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        output = io.StringIO(newline="")
        writer = csv.writer(output, delimiter=";")
        writer.writerow([
            _csv_export_value(column["label"]) for column in result["columns"]
        ])
        writer.writerows(
            [_csv_export_value(value) for value in row["values"]]
            for row in result["rows"]
        )
        return Response(
            content="\ufeff" + output.getvalue(),
            media_type="text/csv; charset=utf-8",
            headers={
                "Content-Disposition": 'attachment; filename="digues-donnees.csv"',
            },
        )

    @application.post(
        "/api/desordres", status_code=201, response_class=GeoJSONResponse
    )
    def create_new_desordre(
        creation: DesordreCreate,
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(connection, "desordres", "create",
                                lambda: create_desordre(connection, creation), origin=origin)

    @application.get("/api/troncons/{troncon_id}/observations")
    def troncon_observations(
        troncon_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_troncon_observations(connection, troncon_id)

    @application.get("/api/desordres/{desordre_id}/observations")
    def desordre_observations(
        desordre_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_desordre_observations(connection, desordre_id)

    @application.post("/api/observations", status_code=201)
    def create_new_observation(
        creation: ObservationCreate,
        operation_id: str = Header(alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "observations", "create",
            lambda: create_observation(connection, creation),
            identifier=creation.id, origin=origin, operation_id=operation_id,
            # Les defaults sont l'état initial, pas un patch partiel. Ne pas
            # modifier la canonicalisation exclude_unset des PUT existants.
            changes=creation.model_dump(mode="json"),
        )

    @application.get("/api/observations/{observation_id}")
    def observation(
        observation_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_observation(connection, observation_id)

    @application.put("/api/observations/{observation_id}")
    def edit_observation(
        observation_id: UUID,
        update: ObservationUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "observations", "update",
            lambda: update_observation(connection, observation_id, update),
            identifier=observation_id, if_match=if_match, origin=origin,
            operation_id=operation_id, changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.get(
        "/api/desordres/{desordre_id}",
        response_class=GeoJSONResponse,
    )
    def desordre_detail(
        desordre_id: UUID,
        connection: Any = Depends(get_connection),
    ) -> dict[str, Any]:
        return fetch_desordre(connection, desordre_id)

    @application.put(
        "/api/desordres/{desordre_id}",
        response_class=GeoJSONResponse,
    )
    def edit_point_desordre(
        desordre_id: UUID,
        update: PointDesordreUpdate,
        operation_id: str | None = Header(default=None, alias="Operation-Id"),
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "desordres", "update",
            lambda: update_point_desordre(connection, desordre_id, update),
            identifier=desordre_id, if_match=if_match, origin=origin,
            operation_id=operation_id, changes=update.model_dump(mode="json", exclude_unset=True),
        )

    @application.put(
        "/api/desordres/{desordre_id}/reperage",
        response_class=GeoJSONResponse,
    )
    def edit_point_reperage(
        desordre_id: UUID,
        update: PointReperageUpdate,
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "desordres", "update",
            lambda: update_point_reperage(connection, desordre_id, update),
            identifier=desordre_id, if_match=if_match, origin=origin,
        )

    @application.put(
        "/api/desordres/{desordre_id}/geometry",
        response_class=GeoJSONResponse,
    )
    def edit_line_desordre_geometry(
        desordre_id: UUID,
        update: DesordreGeometryUpdate,
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "desordres", "update",
            lambda: update_line_desordre_geometry(connection, desordre_id, update),
            identifier=desordre_id, if_match=if_match, origin=origin,
        )

    @application.put(
        "/api/desordres/{desordre_id}/endpoints",
        response_class=GeoJSONResponse,
    )
    def edit_line_desordre_endpoints(
        desordre_id: UUID,
        endpoints: LineEndpoints,
        origin: dict = Depends(mutation_origin),
        if_match: str | None = Header(default=None, alias="If-Match"),
        connection: Any = Depends(get_write_connection),
    ) -> dict[str, Any]:
        return execute_mutation(
            connection, "desordres", "update",
            lambda: update_line_desordre_endpoints(connection, desordre_id, endpoints),
            identifier=desordre_id, if_match=if_match, origin=origin,
        )

    from .frontend_routes import register_frontend_routes
