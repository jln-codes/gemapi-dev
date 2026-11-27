"""Declared source-field aliases for historically undefined classifications.

C3 must attest the source reference, C1/C2 its Indéfini identity, and the actual
SQL FK its catalogue. Matching text names or nullable columns never suffice.
"""
import re
from . import load_contracts

# Reviewed aliases of existing writer fields, not a camelCase conversion rule.
# Existing AH writer explicitly keeps these fields outside the business
# projection. A nullable destination alone does not authorize a new binding.
SOURCE_ONLY = {('OuvrageAssocieAmenagementHydraulique', 'sourceId')}

ALIASES = {
 'coteId':('cote_id',), 'typeCoteId':('type_cote_id',), 'positionId':('position_id',),
 'typePositionId':('type_position_id',), 'sourceId':('source_id',),
 'orientationPhoto':('orientation_photo_id',),
 'typeRiveId':('type_rive_id',), 'riveId':('rive_id',), 'typeTronconId':('type_troncon_id',),
 'typeDesordreId':('type_desordre_id',), 'urgenceId':('urgence_id',),
 'materiauId':('materiau_id',), 'materiauHautId':('materiau_haut_id',), 'materiauBasId':('materiau_bas_id',),
 'materiauPrincipalA':('materiau_principal_a_id',), 'materiauPrincipalB':('materiau_principal_b_id',),
 'materiauCorpsId':('materiau_corps_id',), 'materiauTeteId':('materiau_tete_id',),
 'materiauAncrageAmontId':('materiau_ancrage_amont_id',), 'materiauAncrageAvalId':('materiau_ancrage_aval_id',),
 'natureId':('nature_id',), 'natureHautId':('nature_haut_id',), 'natureBasId':('nature_bas_id',),
 'natureCorpsId':('nature_corps_id',), 'natureTeteId':('nature_tete_id',),
 'natureAncrageAmontId':('nature_ancrage_amont_id',), 'natureAncrageAvalId':('nature_ancrage_aval_id',),
 'fonctionId':('fonction_id','fonction_deversoir_id'), 'fonctionHautId':('fonction_haut_id',), 'fonctionBasId':('fonction_bas_id',),
 'positionHautId':('position_haut_id',), 'positionBasId':('position_bas_id',),
 'revetementId':('revetement_id',), 'revetementHautId':('revetement_haut_id',), 'revetementBasId':('revetement_bas_id',),
 'orientationOuvrageId':('orientation_ouvrage_id','orientation_id'), 'usageId':('usage_id',),
 'typeLargeurFrancBord':('type_largeur_franc_bord_id','largeur_franc_bord_id'),
 'typeOuvrageTelecomEnergieId':('type_id',), 'typeOuvrageVoirieId':('type_id',),
 'typeReseauHydroCielOuvertId':('type_id',), 'typeConduiteFermeeId':('type_id',),
 'ecoulementId':('ecoulement_id',), 'implantationId':('implantation_id',), 'utilisationConduiteId':('utilisation_conduite_id',),
 'typePrestationId':('type_prestation_id','type_id'), 'typeRapportEtudeId':('type_rapport_id','type_id'),
 'originesProfil':('origine_profil_id',), 'origineProfilLongId':('origine_profil_long_id','origine_id'), 'positionProfilLongSurDigueId':('position_profil_long_digue_id','position_digue_id'),
 'typeSystemesReleveId':('systeme_releve_id',), 'typeProfilId':('type_profil_id','type_profil_travers_id'),
 'frequenceEvenementHydrauliqueId':('frequence_id','frequence_evenement_id'),
 'typeOrientationVentId':('orientation_vent_id','orientation_id'), 'referenceHauteurId':('reference_hauteur_id',),
 'typeId':('type_id','type_zone_id'), 'typeOccupationId':('type_occupation_id',), 'regimeId':('regime_id',),
 'fonctionnementId':('fonctionnement_id',), 'moyenManipBatardeauxId':('moyen_manipulation_batardeaux_id','moyen_manip_batardeaux_id'),
 'natureBatardeauxId':('nature_batardeaux_id',), 'typeSeuilId':('type_seuil_id',), 'typeGlissiereId':('type_glissiere_id',),
}


def identity_bindings(classes, families, definitions, contracts=None):
    from digues_app.target.schema import SCHEMA_DDL_TEMPLATE
    contracts = contracts or load_contracts()
    declarations = contracts.c2['historical_undefined']['catalogs']
    result = []
    foreign_keys = {}
    for match in re.finditer(r'ALTER TABLE(?: ONLY)?\s+(public\.[a-z_0-9]+)\s+([^;]+);', SCHEMA_DDL_TEMPLATE[0]):
        for fk in re.finditer(r'FOREIGN KEY\s*\(\s*([a-z_0-9]+)\s*\)\s*REFERENCES\s+(referentiel\.[a-z_0-9]+)', match[2]):
            foreign_keys.setdefault((match[1], fk[1]), set()).add(fk[2])
        for fk in re.finditer(r'FOREIGN KEY\s*\(([^)]+)\)\s*REFERENCES\s+(referentiel\.[a-z_0-9]+)', match[2]):
            for column in fk[1].split(','):
                foreign_keys.setdefault((match[1], column.strip()), set()).add(fk[2])
    for cls, tables in families.items():
        for field in classes.get(cls, {}).get('effective_fields', ()):
            if (cls, field['name']) in SOURCE_ONLY:continue
            ref = field.get('type'); declaration = declarations.get(ref)
            if (field['kind'] != 'REFERENCE' or field.get('many') or declaration is None
                    or len(declaration['candidates']) != 1):
                continue
            for table in tables:
                table = 'public.' + table
                definition = definitions[table]
                for column in ALIASES.get(field['name'], ()):
                    matches=[target for target in declaration['identity_targets'] if (target in foreign_keys.get((table, column), set())
                           or re.search(r'\b' + re.escape(column) + r'\s+text\b[^\n]*REFERENCES\s+' + re.escape(target) + r'\b', definition)
                           or re.search(r'FOREIGN KEY\s*\(\s*' + re.escape(column) + r'\s*\)\s*REFERENCES\s+' + re.escape(target) + r'\b', definition))]
                    if len(matches)==1:
                        result.append((cls,field['name'],table,column,ref,matches[0]))
    return tuple(sorted(result))
