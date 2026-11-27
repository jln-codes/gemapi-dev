"""Localisation opérationnelle des désordres par repérage linéaire.

Seuls les désordres Point/LineString liés à exactement un tronçon sont
repérables. La géométrie reste la représentation réelle ; les opérations SQL
garantissent l'atomicité des deux sens d'édition.
"""

TABLE_DEFINITIONS = {
    "desordre_localisations_reperage": """
        CREATE TABLE IF NOT EXISTS public.localisations_desordres (
            id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            desordre_id UUID NOT NULL,
            troncon_id UUID NOT NULL,
            systeme_reperage_id UUID NOT NULL,
            borne_debut_id UUID NOT NULL,
            liaison_borne_debut_id UUID NOT NULL,
            distance_debut_m DOUBLE PRECISION NOT NULL,
            position_debut_relative TEXT NOT NULL,
            offset_debut_m DOUBLE PRECISION GENERATED ALWAYS AS (
                CASE position_debut_relative
                    WHEN 'AVANT_BORNE' THEN -distance_debut_m
                    WHEN 'SUR_BORNE' THEN 0.0
                    WHEN 'APRES_BORNE' THEN distance_debut_m
                    ELSE NULL
                END
            ) STORED,
            pr_debut NUMERIC NULL,
            borne_fin_id UUID NULL,
            liaison_borne_fin_id UUID NULL,
            distance_fin_m DOUBLE PRECISION NULL,
            position_fin_relative TEXT NULL,
            offset_fin_m DOUBLE PRECISION GENERATED ALWAYS AS (
                CASE position_fin_relative
                    WHEN 'AVANT_BORNE' THEN -distance_fin_m
                    WHEN 'SUR_BORNE' THEN 0.0
                    WHEN 'APRES_BORNE' THEN distance_fin_m
                    ELSE NULL
                END
            ) STORED,
            pr_fin NUMERIC NULL,
            valid BOOLEAN NOT NULL DEFAULT true,
            CONSTRAINT uq_localisations_desordres__desordre
                UNIQUE (desordre_id),
            CONSTRAINT fk_desordre
                FOREIGN KEY (desordre_id) REFERENCES public.desordres (id),
            CONSTRAINT fk_desordre_troncon
                FOREIGN KEY (desordre_id, troncon_id)
                REFERENCES public.desordres__troncons
                    (desordre_id, troncon_id),
            CONSTRAINT fk_systeme_reperage_troncon
                FOREIGN KEY (systeme_reperage_id, troncon_id)
                REFERENCES public.systemes_reperage (id, troncon_id),
            CONSTRAINT fk_systeme_reperage_borne_debut
                FOREIGN KEY (liaison_borne_debut_id, systeme_reperage_id, borne_debut_id)
                REFERENCES public.systemes_reperage__bornes
                    (id, systeme_reperage_id, borne_id),
            CONSTRAINT fk_systeme_reperage_borne_fin
                FOREIGN KEY (liaison_borne_fin_id, systeme_reperage_id, borne_fin_id)
                REFERENCES public.systemes_reperage__bornes
                    (id, systeme_reperage_id, borne_id),
            CONSTRAINT ck_distance_debut_m
                CHECK (distance_debut_m >= 0),
            CONSTRAINT ck_distance_fin_m
                CHECK (distance_fin_m IS NULL OR distance_fin_m >= 0),
            CONSTRAINT ck_position_debut_relative_valide
                CHECK (position_debut_relative IN (
                    'AVANT_BORNE', 'SUR_BORNE', 'APRES_BORNE'
                )),
            CONSTRAINT ck_position_fin_relative_valide
                CHECK (position_fin_relative IS NULL OR
                    position_fin_relative IN (
                        'AVANT_BORNE', 'SUR_BORNE', 'APRES_BORNE'
                    )),
            CONSTRAINT ck_reperage_fin_complet
                CHECK (
                    num_nonnulls(
                        borne_fin_id, liaison_borne_fin_id, distance_fin_m, position_fin_relative
                    ) IN (0, 4)
                    AND (position_fin_relative <> 'SUR_BORNE'
                         OR distance_fin_m = 0)
                ),
            CONSTRAINT ck_position_debut_relative_coherent
                CHECK (position_debut_relative <> 'SUR_BORNE'
                       OR distance_debut_m = 0)
        )
    """,
}

INDEX_DEFINITIONS = {
    "localisation_liaison_borne_debut": """
        CREATE INDEX IF NOT EXISTS ix_localisation_liaison_borne_debut
        ON public.localisations_desordres (liaison_borne_debut_id)
    """,
    "localisation_liaison_borne_fin": """
        CREATE INDEX IF NOT EXISTS ix_localisation_liaison_borne_fin
        ON public.localisations_desordres (liaison_borne_fin_id)
        WHERE liaison_borne_fin_id IS NOT NULL
    """,
    "desordre_localisations_reperage_desordre_idx": """
        CREATE INDEX IF NOT EXISTS ix_localisations_desordres__desordre
        ON public.localisations_desordres (desordre_id)
    """,
    "desordre_localisations_reperage_troncon_idx": """
        CREATE INDEX IF NOT EXISTS ix_localisations_desordres__troncon
        ON public.localisations_desordres (troncon_id)
    """,
    "desordre_localisations_reperage_systeme_idx": """
        CREATE INDEX IF NOT EXISTS ix_localisations_desordres__systeme_reperage
        ON public.localisations_desordres (systeme_reperage_id)
    """,
}

FUNCTION_DEFINITIONS = {
    "resoudre_occurrence_borne": """
        CREATE OR REPLACE FUNCTION public.resoudre_occurrence_borne(p_systeme uuid, p_borne uuid) RETURNS uuid
        LANGUAGE plpgsql STABLE SET search_path = pg_catalog AS $$
        DECLARE occurrences uuid[];
        BEGIN
            IF p_borne IS NULL THEN RETURN NULL; END IF;
            SELECT array_agg(id) INTO occurrences FROM (
                SELECT id FROM public.systemes_reperage__bornes
                WHERE systeme_reperage_id=p_systeme AND borne_id=p_borne LIMIT 2
            ) AS candidats;
            IF cardinality(occurrences) = 1 THEN RETURN occurrences[1]; END IF;
            IF occurrences IS NULL THEN
                RAISE EXCEPTION 'Borne absente du système de repérage' USING ERRCODE='23503';
            END IF;
            RAISE EXCEPTION 'Plusieurs occurrences de borne : préciser la liaison' USING ERRCODE='23514';
        END;
        $$;
    """,
    "maintenir_occurrences_localisation_desordre": """
        CREATE OR REPLACE FUNCTION public.maintenir_occurrences_localisation_desordre() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog AS $$
        BEGIN
            IF TG_OP = 'UPDATE' THEN
                IF (NEW.systeme_reperage_id,NEW.borne_debut_id) IS DISTINCT FROM (OLD.systeme_reperage_id,OLD.borne_debut_id)
                   AND NEW.liaison_borne_debut_id IS NOT DISTINCT FROM OLD.liaison_borne_debut_id THEN
                    NEW.liaison_borne_debut_id := NULL;
                END IF;
                IF (NEW.systeme_reperage_id,NEW.borne_fin_id) IS DISTINCT FROM (OLD.systeme_reperage_id,OLD.borne_fin_id)
                   AND NEW.liaison_borne_fin_id IS NOT DISTINCT FROM OLD.liaison_borne_fin_id THEN
                    NEW.liaison_borne_fin_id := NULL;
                END IF;
            END IF;
            IF NEW.liaison_borne_debut_id IS NULL THEN
                NEW.liaison_borne_debut_id := public.resoudre_occurrence_borne(NEW.systeme_reperage_id,NEW.borne_debut_id);
            END IF;
            IF NEW.liaison_borne_fin_id IS NULL THEN
                NEW.liaison_borne_fin_id := public.resoudre_occurrence_borne(NEW.systeme_reperage_id,NEW.borne_fin_id);
            END IF;
            RETURN NEW;
        END;
        $$;
    """,
    "synchroniser_desordre_reperage": """
        CREATE OR REPLACE FUNCTION public.synchroniser_reperage_desordre(
            p_desordre_id UUID,
            p_systeme_reperage_id UUID DEFAULT NULL
        ) RETURNS TEXT
        LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, public__DIGUES_APP_POSTGIS_SEARCH_PATH_SUFFIX__
        AS $function$
        DECLARE
            v_nombre_troncons INTEGER;
            v_troncon_id UUID;
            v_systeme_id UUID;
            v_geometry geometry;
            v_type TEXT;
            v_debut RECORD;
            v_fin RECORD;
            v_fin_borne_id UUID;
            v_fin_distance_m DOUBLE PRECISION;
            v_fin_position_relative TEXT;
            v_fin_pr NUMERIC;
            v_guard TEXT;
        BEGIN
            SELECT count(*)::integer, min(l.troncon_id::text)::uuid
            INTO v_nombre_troncons, v_troncon_id
            FROM public.desordres__troncons AS l
            WHERE l.desordre_id = p_desordre_id;
            SELECT d.geometry, GeometryType(d.geometry)
            INTO v_geometry, v_type
            FROM public.desordres AS d WHERE d.id = p_desordre_id;
            IF NOT FOUND THEN RETURN 'REFERENCE_ABSENTE'; END IF;

            IF v_nombre_troncons <> 1 OR v_geometry IS NULL
               OR v_type NOT IN ('POINT', 'LINESTRING') THEN
                DELETE FROM public.localisations_desordres
                WHERE desordre_id = p_desordre_id;
                RETURN CASE
                    WHEN v_nombre_troncons = 0 THEN 'AUCUN_TRONCON'
                    WHEN v_nombre_troncons > 1 THEN 'PLUSIEURS_TRONCONS'
                    ELSE 'TYPE_NON_REPERABLE'
                END;
            END IF;

            SELECT sr.id INTO v_systeme_id
            FROM public.systemes_reperage AS sr
            WHERE sr.id = p_systeme_reperage_id
              AND sr.troncon_id = v_troncon_id;
            IF v_systeme_id IS NULL THEN
                SELECT l.systeme_reperage_id INTO v_systeme_id
                FROM public.localisations_desordres AS l
                JOIN public.systemes_reperage AS sr
                  ON sr.id = l.systeme_reperage_id
                 AND sr.troncon_id = v_troncon_id
                WHERE l.desordre_id = p_desordre_id;
            END IF;
            IF v_systeme_id IS NULL THEN
                SELECT t.systeme_reperage_defaut_id INTO v_systeme_id
                FROM public.troncons AS t WHERE t.id = v_troncon_id;
            END IF;
            IF v_systeme_id IS NULL THEN
                DELETE FROM public.localisations_desordres
                WHERE desordre_id = p_desordre_id;
                RETURN 'SYSTEME_ABSENT';
            END IF;

            SELECT conversion.* INTO v_debut
            FROM public.xy_vers_reperage(
                v_troncon_id, v_systeme_id,
                CASE v_type WHEN 'POINT' THEN v_geometry
                    ELSE ST_StartPoint(v_geometry) END
            ) AS conversion;
            IF v_debut.statut <> 'OK' OR v_debut.borne_id IS NULL THEN
                DELETE FROM public.localisations_desordres
                WHERE desordre_id = p_desordre_id;
                RETURN v_debut.statut;
            END IF;
            IF v_type = 'LINESTRING' THEN
                SELECT conversion.* INTO v_fin
                FROM public.xy_vers_reperage(
                    v_troncon_id, v_systeme_id, ST_EndPoint(v_geometry)
                ) AS conversion;
                IF v_fin.statut <> 'OK' OR v_fin.borne_id IS NULL THEN
                    DELETE FROM public.localisations_desordres
                    WHERE desordre_id = p_desordre_id;
                    RETURN v_fin.statut;
                END IF;
                v_fin_borne_id := v_fin.borne_id;
                v_fin_distance_m := v_fin.distance_borne_m;
                v_fin_position_relative := v_fin.position_relative;
                v_fin_pr := v_fin.pr;
            END IF;

            v_guard := current_setting('digues_app.reperage_guard', true);
            PERFORM set_config('digues_app.reperage_guard', 'GEOMETRIE', true);
            INSERT INTO public.localisations_desordres (
                desordre_id, troncon_id, systeme_reperage_id,
                borne_debut_id, distance_debut_m, position_debut_relative,
                pr_debut, borne_fin_id, distance_fin_m,
                position_fin_relative, pr_fin, valid
            ) VALUES (
                p_desordre_id, v_troncon_id, v_systeme_id,
                v_debut.borne_id, v_debut.distance_borne_m,
                v_debut.position_relative, v_debut.pr,
                v_fin_borne_id, v_fin_distance_m,
                v_fin_position_relative, v_fin_pr, true
            )
            ON CONFLICT (desordre_id) DO UPDATE SET
                troncon_id = EXCLUDED.troncon_id,
                systeme_reperage_id = EXCLUDED.systeme_reperage_id,
                borne_debut_id = EXCLUDED.borne_debut_id,
                distance_debut_m = EXCLUDED.distance_debut_m,
                position_debut_relative = EXCLUDED.position_debut_relative,
                pr_debut = EXCLUDED.pr_debut,
                borne_fin_id = EXCLUDED.borne_fin_id,
                distance_fin_m = EXCLUDED.distance_fin_m,
                position_fin_relative = EXCLUDED.position_fin_relative,
                pr_fin = EXCLUDED.pr_fin;
            PERFORM set_config('digues_app.reperage_guard', coalesce(v_guard, ''), true);
            RETURN 'OK';
        END
        $function$
    """,
    "appliquer_desordre_reperage": """
        CREATE OR REPLACE FUNCTION public.appliquer_reperage_desordre()
        RETURNS trigger
        LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, public__DIGUES_APP_POSTGIS_SEARCH_PATH_SUFFIX__
        AS $function$
        DECLARE
            v_nombre_troncons INTEGER;
            v_troncon_id UUID;
            v_geometry geometry;
            v_type TEXT;
            v_troncon_geometry geometry;
            v_debut RECORD;
            v_fin RECORD;
            v_fraction_debut DOUBLE PRECISION;
            v_fraction_fin DOUBLE PRECISION;
            v_guard TEXT;
        BEGIN
            IF current_setting('digues_app.reperage_guard', true) = 'GEOMETRIE' THEN
                RETURN NEW;
            END IF;
            SELECT count(*)::integer, min(l.troncon_id::text)::uuid
            INTO v_nombre_troncons, v_troncon_id
            FROM public.desordres__troncons AS l
            WHERE l.desordre_id = NEW.desordre_id;
            IF v_nombre_troncons <> 1 OR NEW.troncon_id <> v_troncon_id THEN
                RAISE EXCEPTION
                    'Le repérage exige exactement un tronçon associé au désordre.';
            END IF;
            SELECT d.geometry, GeometryType(d.geometry), ST_Force2D(coalesce(t.geometry_reperage, t.geometry))
            INTO v_geometry, v_type, v_troncon_geometry
            FROM public.desordres AS d
            JOIN public.troncons AS t ON t.id = v_troncon_id
            WHERE d.id = NEW.desordre_id;
            IF v_type NOT IN ('POINT', 'LINESTRING') THEN
                RAISE EXCEPTION
                    'Le repérage éditable est réservé aux Point et LineString.';
            END IF;

            SELECT conversion.* INTO v_debut
            FROM public.borne_offset_vers_xy(
                NEW.troncon_id, NEW.systeme_reperage_id,
                NEW.borne_debut_id,
                CASE NEW.position_debut_relative
                    WHEN 'AVANT_BORNE' THEN -NEW.distance_debut_m
                    WHEN 'SUR_BORNE' THEN 0.0
                    ELSE NEW.distance_debut_m END
            ) AS conversion;
            IF v_debut.statut <> 'OK' THEN
                RAISE EXCEPTION 'Repérage de début invalide : %', v_debut.statut;
            END IF;
            NEW.pr_debut := v_debut.pr;
            IF v_type = 'POINT' THEN
                NEW.borne_fin_id := NULL;
                NEW.distance_fin_m := NULL;
                NEW.position_fin_relative := NULL;
                NEW.pr_fin := NULL;
                v_geometry := v_debut.point_xy;
            ELSE
                IF NEW.borne_fin_id IS NULL OR NEW.distance_fin_m IS NULL
                   OR NEW.position_fin_relative IS NULL THEN
                    RAISE EXCEPTION
                        'Le recalage d''une ligne exige un repérage de fin complet.';
                END IF;
                SELECT conversion.* INTO v_fin
                FROM public.borne_offset_vers_xy(
                    NEW.troncon_id, NEW.systeme_reperage_id,
                    NEW.borne_fin_id,
                    CASE NEW.position_fin_relative
                        WHEN 'AVANT_BORNE' THEN -NEW.distance_fin_m
                        WHEN 'SUR_BORNE' THEN 0.0
                        ELSE NEW.distance_fin_m END
                ) AS conversion;
                IF v_fin.statut <> 'OK' THEN
                    RAISE EXCEPTION 'Repérage de fin invalide : %', v_fin.statut;
                END IF;
                IF abs(v_fin.abscisse_m - v_debut.abscisse_m) <= 1e-8 THEN
                    RAISE EXCEPTION
                        'Le début et la fin ne peuvent pas définir une ligne nulle.';
                END IF;
                NEW.pr_fin := v_fin.pr;
                v_fraction_debut := v_debut.abscisse_m
                    / ST_Length(v_troncon_geometry);
                v_fraction_fin := v_fin.abscisse_m
                    / ST_Length(v_troncon_geometry);
                v_geometry := ST_LineSubstring(
                    v_troncon_geometry,
                    least(v_fraction_debut, v_fraction_fin),
                    greatest(v_fraction_debut, v_fraction_fin)
                );
                IF v_fraction_debut > v_fraction_fin THEN
                    v_geometry := ST_Reverse(v_geometry);
                END IF;
            END IF;

            v_guard := current_setting('digues_app.reperage_guard', true);
            PERFORM set_config('digues_app.reperage_guard', 'REPERAGE', true);
            UPDATE public.desordres SET geometry = v_geometry
            WHERE id = NEW.desordre_id;
            PERFORM set_config('digues_app.reperage_guard', coalesce(v_guard, ''), true);
            RETURN NEW;
        END
        $function$
    """,
    "recalculer_desordre_apres_geometrie": """
        CREATE OR REPLACE FUNCTION public.recalculer_desordre_apres_geometrie()
        RETURNS trigger
        LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, public__DIGUES_APP_POSTGIS_SEARCH_PATH_SUFFIX__
        AS $function$
        BEGIN
            IF current_setting('digues_app.reperage_guard', true)
               IS DISTINCT FROM 'REPERAGE' THEN
                PERFORM public.synchroniser_reperage_desordre(NEW.id);
            END IF;
            RETURN NEW;
        END
        $function$
    """,
    "recalculer_desordre_apres_lien_troncon": """
        CREATE OR REPLACE FUNCTION public.recalculer_desordre_apres_lien_troncon()
        RETURNS trigger
        LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, public__DIGUES_APP_POSTGIS_SEARCH_PATH_SUFFIX__
        AS $function$
        BEGIN
            IF TG_OP IN ('DELETE', 'UPDATE') THEN
                PERFORM public.synchroniser_reperage_desordre(OLD.desordre_id);
            END IF;
            IF TG_OP IN ('INSERT', 'UPDATE') THEN
                IF TG_OP = 'INSERT' OR NEW.desordre_id <> OLD.desordre_id THEN
                    PERFORM public.synchroniser_reperage_desordre(NEW.desordre_id);
                END IF;
            END IF;
            RETURN coalesce(NEW, OLD);
        END
        $function$
    """,
    "inverser_troncon": """
        CREATE OR REPLACE FUNCTION public.inverser_troncon(p_troncon_id UUID)
        RETURNS INTEGER
        LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, public__DIGUES_APP_POSTGIS_SEARCH_PATH_SUFFIX__
        AS $function$
        DECLARE
            v_desordre_id UUID;
            v_nombre INTEGER := 0;
        BEGIN
            UPDATE public.troncons
            SET geometry_reperage = CASE WHEN geometry_reperage IS NOT NULL
                    THEN ST_Reverse(geometry_reperage) ELSE NULL END,
                geometry = CASE WHEN geometry_reperage IS NULL
                    THEN ST_Reverse(geometry) ELSE geometry END
            WHERE id = p_troncon_id
              AND GeometryType(coalesce(geometry_reperage, geometry)) = 'LINESTRING'
              AND NOT ST_IsEmpty(coalesce(geometry_reperage, geometry));
            IF NOT FOUND THEN
                RAISE EXCEPTION 'Tronçon absent ou géométrie non inversible.';
            END IF;
            FOR v_desordre_id IN
                SELECT l.desordre_id
                FROM public.desordres__troncons AS l
                WHERE l.troncon_id = p_troncon_id
                  AND 1 = (SELECT count(*)
                           FROM public.desordres__troncons AS tous
                           WHERE tous.desordre_id = l.desordre_id)
            LOOP
                PERFORM public.synchroniser_reperage_desordre(v_desordre_id);
                v_nombre := v_nombre + 1;
            END LOOP;
            RETURN v_nombre;
        END
        $function$
    """,
    "editer_desordre_point": """
        CREATE OR REPLACE FUNCTION public.editer_desordre_point()
        RETURNS trigger
        LANGUAGE plpgsql VOLATILE SET search_path = pg_catalog, public__DIGUES_APP_POSTGIS_SEARCH_PATH_SUFFIX__
        AS $function$
        DECLARE
            v_geometry geometry(Point, 3950);
            v_geometry_modifiee BOOLEAN;
            v_xy_modifie BOOLEAN;
            v_lonlat_modifie BOOLEAN;
            v_xy_fourni BOOLEAN;
            v_lonlat_fourni BOOLEAN;
            v_nombre_familles INTEGER;
        BEGIN
            IF TG_OP = 'DELETE' THEN
                DELETE FROM public.desordres WHERE id = OLD.id;
                RETURN OLD;
            END IF;
            IF TG_OP = 'INSERT' THEN
                v_xy_fourni := NEW.coord_x_3950 IS NOT NULL
                    OR NEW.coord_y_3950 IS NOT NULL;
                v_lonlat_fourni := NEW.longitude_4326 IS NOT NULL
                    OR NEW.latitude_4326 IS NOT NULL;
                IF v_xy_fourni AND (
                    NEW.coord_x_3950 IS NULL OR NEW.coord_y_3950 IS NULL
                ) THEN
                    RAISE EXCEPTION 'X et Y sont obligatoires ensemble.';
                END IF;
                IF v_lonlat_fourni AND (
                    NEW.longitude_4326 IS NULL OR NEW.latitude_4326 IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'Longitude et latitude sont obligatoires ensemble.';
                END IF;
                v_nombre_familles :=
                    CASE WHEN NEW.geometry IS NOT NULL THEN 1 ELSE 0 END
                    + CASE WHEN v_xy_fourni THEN 1 ELSE 0 END
                    + CASE WHEN v_lonlat_fourni THEN 1 ELSE 0 END;
                IF v_nombre_familles > 1 THEN
                    RAISE EXCEPTION
                        'Fournir soit la géométrie, soit X/Y, soit longitude/latitude.';
                ELSIF v_nombre_familles = 0 THEN
                    RAISE EXCEPTION
                        'Une géométrie, X/Y ou longitude/latitude est obligatoire.';
                ELSIF NEW.geometry IS NOT NULL THEN
                    v_geometry := NEW.geometry;
                ELSIF v_xy_fourni THEN
                    v_geometry := ST_SetSRID(
                        ST_Point(NEW.coord_x_3950, NEW.coord_y_3950), 3950
                    );
                ELSE
                    v_geometry := ST_Transform(
                        ST_SetSRID(
                            ST_Point(NEW.longitude_4326, NEW.latitude_4326), 4326
                        ), 3950
                    );
                END IF;
                INSERT INTO public.desordres (
                    id, type_desordre_id, designation, commentaire,
                    date_debut, date_fin, geometry, valid
                ) VALUES (
                    coalesce(NEW.id, gen_random_uuid()), NEW.type_desordre_id,
                    NEW.designation, NEW.commentaire, NEW.date_debut,
                    NEW.date_fin, v_geometry, NEW.valid
                ) RETURNING id INTO NEW.id;
            ELSE
                v_geometry_modifiee := NEW.geometry
                    IS DISTINCT FROM OLD.geometry;
                v_xy_modifie := NEW.coord_x_3950 IS DISTINCT FROM OLD.coord_x_3950
                    OR NEW.coord_y_3950 IS DISTINCT FROM OLD.coord_y_3950;
                v_lonlat_modifie := NEW.longitude_4326
                        IS DISTINCT FROM OLD.longitude_4326
                    OR NEW.latitude_4326 IS DISTINCT FROM OLD.latitude_4326;
                v_nombre_familles :=
                    CASE WHEN v_geometry_modifiee THEN 1 ELSE 0 END
                    + CASE WHEN v_xy_modifie THEN 1 ELSE 0 END
                    + CASE WHEN v_lonlat_modifie THEN 1 ELSE 0 END;
                IF v_nombre_familles > 1 THEN
                    RAISE EXCEPTION
                        'Modifier soit la géométrie, soit X/Y, soit longitude/latitude.';
                ELSIF v_xy_modifie THEN
                    IF NEW.coord_x_3950 IS NULL OR NEW.coord_y_3950 IS NULL THEN
                        RAISE EXCEPTION 'X et Y sont obligatoires ensemble.';
                    END IF;
                    v_geometry := ST_SetSRID(
                        ST_Point(NEW.coord_x_3950, NEW.coord_y_3950), 3950
                    );
                ELSIF v_lonlat_modifie THEN
                    IF NEW.longitude_4326 IS NULL OR NEW.latitude_4326 IS NULL THEN
                        RAISE EXCEPTION
                            'Longitude et latitude sont obligatoires ensemble.';
                    END IF;
                    v_geometry := ST_Transform(
                        ST_SetSRID(
                            ST_Point(NEW.longitude_4326, NEW.latitude_4326), 4326
                        ), 3950
                    );
                ELSE
                    v_geometry := NEW.geometry;
                END IF;
                UPDATE public.desordres SET
                    type_desordre_id = NEW.type_desordre_id,
                    designation = NEW.designation,
                    commentaire = NEW.commentaire,
                    date_debut = NEW.date_debut,
                    date_fin = NEW.date_fin,
                    geometry = v_geometry,
                    valid = NEW.valid
                WHERE id = OLD.id;
            END IF;
            NEW.geometry := v_geometry;
            NEW.coord_x_3950 := ST_X(v_geometry);
            NEW.coord_y_3950 := ST_Y(v_geometry);
            NEW.longitude_4326 := ST_X(ST_Transform(v_geometry, 4326));
            NEW.latitude_4326 := ST_Y(ST_Transform(v_geometry, 4326));
            RETURN NEW;
        END
        $function$
    """,
}

TRIGGER_DEFINITIONS = {
    "occurrences_localisation_desordre": """
        CREATE OR REPLACE TRIGGER occurrences_localisation_desordre
            BEFORE INSERT OR UPDATE ON public.localisations_desordres
            FOR EACH ROW EXECUTE FUNCTION public.maintenir_occurrences_localisation_desordre();
    """,
    "desordres_recalcul_reperage_trigger": """
        CREATE OR REPLACE TRIGGER recalculer_reperage_desordre
        AFTER INSERT OR UPDATE OF geometry ON public.desordres
        FOR EACH ROW EXECUTE FUNCTION public.recalculer_desordre_apres_geometrie()
    """,
    "liens_desordres_recalcul_reperage_trigger": """
        CREATE OR REPLACE TRIGGER recalculer_reperage_liens_desordres
        AFTER INSERT OR DELETE OR UPDATE OF desordre_id, troncon_id
        ON public.desordres__troncons
        FOR EACH ROW
        EXECUTE FUNCTION public.recalculer_desordre_apres_lien_troncon()
    """,
    "desordre_reperage_appliquer_trigger": """
        CREATE OR REPLACE TRIGGER appliquer_reperage_desordre
        BEFORE INSERT OR UPDATE OF
            troncon_id, systeme_reperage_id,
            borne_debut_id, distance_debut_m, position_debut_relative,
            borne_fin_id, distance_fin_m, position_fin_relative
        ON public.localisations_desordres
        FOR EACH ROW EXECUTE FUNCTION public.appliquer_reperage_desordre()
    """,
}

VIEW_DEFINITIONS = {
    "view_desordres_points_saisie": """
        CREATE OR REPLACE VIEW public.desordres_points_saisie AS
        SELECT d.id, d.type_desordre_id, d.designation, d.commentaire,
            d.date_debut, d.date_fin, d.geometry, d.valid,
            ST_X(d.geometry)::double precision AS coord_x_3950,
            ST_Y(d.geometry)::double precision AS coord_y_3950,
            ST_X(ST_Transform(d.geometry, 4326))::double precision
                AS longitude_4326,
            ST_Y(ST_Transform(d.geometry, 4326))::double precision
                AS latitude_4326
        FROM public.desordres AS d
        WHERE GeometryType(d.geometry) = 'POINT'
    """,
    "view_systemes_reperage_bornes": """
        CREATE OR REPLACE VIEW public.bornes_systemes_reperage_lecture AS
        SELECT l.id, l.systeme_reperage_id, l.borne_id, b.libelle,
            CASE WHEN GeometryType(coalesce(t.geometry_reperage, t.geometry)) = 'LINESTRING' AND NOT ST_IsEmpty(coalesce(t.geometry_reperage, t.geometry))
                AND ST_Length(coalesce(t.geometry_reperage, t.geometry)) > 0 AND GeometryType(b.geometry) = 'POINT'
                AND NOT ST_IsEmpty(b.geometry) AND ST_SRID(coalesce(t.geometry_reperage, t.geometry)) = ST_SRID(b.geometry)
            THEN CASE
                WHEN abs(ST_LineLocatePoint(coalesce(t.geometry_reperage, t.geometry), b.geometry)) <= 1e-8
                    THEN 'DEBUT_TRONCON'
                WHEN abs(1 - ST_LineLocatePoint(coalesce(t.geometry_reperage, t.geometry), b.geometry)) <= 1e-8
                    THEN 'FIN_TRONCON'
                ELSE 'BORNE_INTERMEDIAIRE'
            END ELSE NULL END AS role_spatial,
            CASE WHEN GeometryType(coalesce(t.geometry_reperage, t.geometry)) = 'LINESTRING' AND NOT ST_IsEmpty(coalesce(t.geometry_reperage, t.geometry))
                AND ST_Length(coalesce(t.geometry_reperage, t.geometry)) > 0 AND GeometryType(b.geometry) = 'POINT'
                AND NOT ST_IsEmpty(b.geometry) AND ST_SRID(coalesce(t.geometry_reperage, t.geometry)) = ST_SRID(b.geometry)
            THEN CASE
                WHEN abs(ST_LineLocatePoint(coalesce(t.geometry_reperage, t.geometry), b.geometry)) <= 1e-8
                    THEN 'Début du tronçon'
                WHEN abs(1 - ST_LineLocatePoint(coalesce(t.geometry_reperage, t.geometry), b.geometry)) <= 1e-8
                    THEN 'Fin du tronçon'
                ELSE coalesce(b.libelle, b.id::text)
            END ELSE coalesce(b.libelle,b.id::text) END AS libelle_affichage,
            l.valeur_pr, l.valid AND b.valid AND sr.valid AS valid
        FROM public.systemes_reperage__bornes AS l
        JOIN public.systemes_reperage AS sr ON sr.id = l.systeme_reperage_id
        JOIN public.troncons AS t ON t.id = sr.troncon_id
        JOIN public.bornes_reperage AS b ON b.id = l.borne_id
    """,
    "view_desordre_localisations_reperage": """
        CREATE OR REPLACE VIEW public.localisations_desordres_lecture AS
        SELECT l.id, l.desordre_id,
            d.designation AS desordre_designation,
            GeometryType(d.geometry) AS type_geometrie_desordre,
            1::integer AS nombre_troncons, true AS reperage_disponible,
            l.troncon_id, t.libelle AS troncon_libelle,
            l.systeme_reperage_id,
            sr.libelle AS systeme_reperage_libelle,
            l.borne_debut_id, bd.libelle AS borne_debut_libelle,
            l.distance_debut_m, l.position_debut_relative, l.pr_debut,
            l.borne_fin_id, bf.libelle AS borne_fin_libelle,
            l.distance_fin_m, l.position_fin_relative, l.pr_fin,
            concat_ws(' — ', t.libelle,
                concat_ws(' ', bd.libelle,
                    round(l.distance_debut_m::numeric, 2) || ' m',
                    CASE WHEN l.offset_debut_m < 0 THEN 'amont'
                         WHEN l.offset_debut_m > 0 THEN 'aval'
                         ELSE 'sur borne' END)) AS resume_localisation,
            l.valid
        FROM public.localisations_desordres AS l
        JOIN public.desordres AS d ON d.id = l.desordre_id
        JOIN public.troncons AS t ON t.id = l.troncon_id
        JOIN public.systemes_reperage AS sr ON sr.id = l.systeme_reperage_id
        JOIN public.bornes_reperage AS bd ON bd.id = l.borne_debut_id
        LEFT JOIN public.bornes_reperage AS bf ON bf.id = l.borne_fin_id
    """,
}

VIEW_TRIGGER_DEFINITIONS = {
    "editer_desordre_point": """
        CREATE OR REPLACE TRIGGER editer_desordre_point
        INSTEAD OF INSERT OR UPDATE OR DELETE
        ON public.desordres_points_saisie
        FOR EACH ROW EXECUTE FUNCTION public.editer_desordre_point()
    """,
}

FUNCTION_DDL = tuple(statement.strip() for statement in FUNCTION_DEFINITIONS.values())
TRIGGER_DDL = tuple(statement.strip() for statement in TRIGGER_DEFINITIONS.values())
VIEW_TRIGGER_DDL = tuple(
    statement.strip() for statement in VIEW_TRIGGER_DEFINITIONS.values()
)
