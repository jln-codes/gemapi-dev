"""Native login permissions and hydrographic preparation on disposable PostGIS."""
import time
from uuid import uuid4

import psycopg2


PROFILES = {'roles_reader': 'milieux_consultation',
            'roles_editor': 'milieux_prestations',
            'roles_manager': 'milieux_patrimoine'}
PATRIMONY = {'ah', 'ah_stockage', 'ah_ouvrage', 'ah_sections', 'cours_eau',
             'digues', 'digues_reperes', 'sites_terrestres', 'troncons', 'mobilier', 'cheminements', 'vegetation',
             'ouvrages_particuliers', 'equipements_mesure', 'ouvrages_hydrauliques', 'desordres', 'observations', 'photos',
             'equipements_mesure__desordres', 'ouvrages_hydrauliques__desordres', 'ouvrages_particuliers__desordres',
             'ouvrages_hydrauliques__objets_lies'}


def check_hydrography(connect, login, ids):
    with connect(login) as db, db.cursor() as c:
        c.execute("INSERT INTO gemapi.digues(nom) VALUES ('Digue fictive') RETURNING id");digue=c.fetchone()[0]
        c.execute("INSERT INTO gemapi.digues_reperes(digue_id,riviere_id,rive,debut_fraction,fin_fraction) VALUES (%s,%s,'G',0.1,0.9) RETURNING id,modifie_par",(digue,ids['river']))
        repere, author=c.fetchone();assert author==login
        c.execute('INSERT INTO gemapi.ah_sections(ah_id,cours_eau_id,debut_fraction,fin_fraction) VALUES (%s,%s,0.2,0.8) RETURNING id,valide_par,modifie_par',(ids['ah'],ids['river']))
        section,validator,author=c.fetchone();assert validator==author==login
        c.execute('UPDATE gemapi.ah_sections SET fin_fraction=0.7,revision_validation=revision_validation+1 WHERE id=%s',(section,))
        c.execute('UPDATE gemapi.digues_reperes SET fin_fraction=0.8,revision_validation=revision_validation+1 WHERE id=%s',(repere,))
        c.execute('SET CONSTRAINTS ALL IMMEDIATE')
    with connect(login) as db, db.cursor() as c:
        try:
            c.execute("UPDATE gemapi.ah_sections SET geom_reference=ST_GeomFromText('LINESTRING(0 0,1 1)',3950) WHERE id=%s",(section,))
        except psycopg2.Error as error:
            assert error.pgcode=='23514';db.rollback()
        else:raise AssertionError('Une référence figée a été modifiée librement')
    with connect(login) as db, db.cursor() as c:
        c.execute('DELETE FROM gemapi.ah_sections WHERE id=%s',(section,))
        c.execute('DELETE FROM gemapi.digues_reperes WHERE id=%s',(repere,))
        c.execute('DELETE FROM gemapi.digues WHERE id=%s',(digue,))


def main():
    from demo_container import ROOT, command, sql

    container = 'sig-roles-' + uuid4().hex[:10]
    command('docker', 'run', '--rm', '-d', '--name', container,
            '-p', '127.0.0.1::5432', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust', 'postgis/postgis:16-3.4')
    try:
        for _ in range(60):
            if command('docker', 'exec', container, 'pg_isready', '-h', '127.0.0.1', '-U', 'postgres', check=False).returncode == 0:
                break
            time.sleep(.5)
        port = command('docker', 'port', container, '5432').stdout.strip().rsplit(':', 1)[1]
        for extension in ('public', 'extensions'):
            database = 'roles_' + extension
            sql(container, 'CREATE DATABASE '+database)
            def connect(user='postgres'):
                return psycopg2.connect(host='127.0.0.1', port=port, dbname=database, user=user)
            with connect() as db, db.cursor() as c:
                c.execute(f'CREATE SCHEMA IF NOT EXISTS {extension}; CREATE EXTENSION postgis WITH SCHEMA {extension}')
                c.execute((ROOT/'model/schema_v0.6.1.sql').read_text())
                c.execute((ROOT/'model/roles_milieux.sql').read_text())
                c.execute((ROOT/'model/roles_milieux.sql').read_text())
                for login, group in PROFILES.items():
                    c.execute('SELECT 1 FROM pg_roles WHERE rolname=%s', (login,))
                    if not c.fetchone():
                        c.execute(f'CREATE ROLE {login} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS')
                    c.execute(f'GRANT {group} TO {login}')
                    c.execute(f'ALTER ROLE {login} IN DATABASE {database} SET search_path=pg_catalog,{extension},gemapi')
                c.execute("SELECT relname,relkind FROM pg_class WHERE relnamespace='gemapi'::regnamespace AND relkind IN ('r','v')")
                tables = c.fetchall()
                ids = {name: str(uuid4()) for name in ('type','provider','technician','ah','river','site','mobilier')}
                c.execute('SET search_path=pg_catalog,'+extension+',gemapi')
                c.execute("INSERT INTO gemapi.types_prestations(id,code,libelle,categorie_id) VALUES (%s,'TEST','Test','cf2572fb-9365-5ac5-bdf4-0d66581d9aaa')", (ids['type'],))
                c.execute("INSERT INTO gemapi.prestataires(id,nom) VALUES (%s,'Test')", (ids['provider'],))
                c.execute("INSERT INTO gemapi.techniciens(id,nom) VALUES (%s,'Test')", (ids['technician'],))
                c.execute("INSERT INTO gemapi.ah(id,nom) VALUES (%s,'AH fictif')", (ids['ah'],))
                c.execute("INSERT INTO gemapi.ah_stockage(ah_id,nom,geom) VALUES (%s,'Stockage fictif',ST_GeomFromText('POLYGON((2 -2,8 -2,8 2,2 2,2 -2))',3950))", (ids['ah'],))
                c.execute("INSERT INTO gemapi.cours_eau(id,nom,source,direction,geom) VALUES (%s,'Support fictif','TEST',true,ST_Multi(ST_GeomFromText('LINESTRING(0 0,10 0)',3950)))", (ids['river'],))
                c.execute("INSERT INTO gemapi.sites_terrestres(id,nom,geom) VALUES (%s,'Site fictif',ST_GeomFromText('MULTIPOLYGON(((0 0,2 0,2 2,0 0)))',3950))", (ids['site'],))
                c.execute("INSERT INTO gemapi.mobilier(id,site_terrestre_id,designation,type_id,geometry) VALUES (%s,%s,'Panneau fictif','PAN',ST_GeomFromText('MULTIPOINT((1 1))',3950))", (ids['mobilier'],ids['site']))
                c.execute("SELECT id FROM gemapi.statuts_prestations WHERE code='PLANIFIE'")
                status = c.fetchone()[0]
            for login in PROFILES:
                with connect(login) as db, db.cursor() as c:
                    c.execute('SELECT current_user,session_user');assert c.fetchone() == (login,login)
                    for table, kind in tables:
                        c.execute(f'SELECT count(*) FROM gemapi.{table}')
                        for privilege in ('INSERT','UPDATE','DELETE'):
                            c.execute('SELECT has_table_privilege(%s,%s)', ('gemapi.'+table,privilege))
                            expected = kind=='r' and ((table=='prestations' and login!='roles_reader') or (table in PATRIMONY and login=='roles_manager'))
                            assert c.fetchone()[0] == expected, (extension,login,table,privilege)
                        c.execute("SELECT has_table_privilege(%s,'TRUNCATE')", ('gemapi.'+table,));assert not c.fetchone()[0]
                    c.execute("SELECT has_schema_privilege('gemapi','CREATE')");assert not c.fetchone()[0]
                    c.execute('SELECT rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls FROM pg_roles WHERE rolname=current_user')
                    assert c.fetchone()==(False,False,False,False,False)
                    c.execute('SELECT count(*) FROM gemapi.ah_sections_candidates(%s)',(ids['ah'],));assert c.fetchone()[0]==1
            # Native sessions verify session_user stamping, not SET ROLE impersonation.
            for login in ('roles_editor','roles_manager'):
                with connect(login) as db, db.cursor() as c:
                    c.execute("INSERT INTO gemapi.prestations(type_prestation_id,prestataire_id,technicien_id,statut_id,date_debut,date_fin,mobilier_id) VALUES (%s,%s,%s,%s,'2026-01-01','2026-01-01',%s) RETURNING id,modifie_par", (ids['type'],ids['provider'],ids['technician'],status,ids['mobilier']))
                    uid, author = c.fetchone();assert author==login
                    c.execute('UPDATE gemapi.prestations SET description=%s WHERE id=%s',('Test',uid))
                    c.execute('DELETE FROM gemapi.prestations WHERE id=%s',(uid,))
            check_hydrography(connect, 'roles_manager', ids)
            print(extension+': lecture de toutes les vues, matrice des trois profils, refus des droits supplémentaires, préparation/modification/suppression des références et traçabilité LOGIN : OK',flush=True)
    finally:
        command('docker','stop',container,check=False)


if __name__=='__main__':main()
