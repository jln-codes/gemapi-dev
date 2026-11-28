"""HYDRO-AH-04 / HYDRO-QUAL-02: digue/AH references, rights and transactions.

Run: /usr/bin/python3 plugin/tests/test_ah_sections.py
No external DSN is accepted. Only invented geometries are used.
"""
import contextlib
import time
import unittest
from uuid import uuid4

import psycopg2

from test_postgres_integration import ROOT, command


class AhSections(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.container = 'sig-ah-sections-' + uuid4().hex[:10]
        command('docker', 'run', '--rm', '-d', '--name', cls.container,
                '-p', '127.0.0.1::5432', '-e', 'POSTGRES_HOST_AUTH_METHOD=trust',
                '-e', 'POSTGRES_DB=seed', 'postgis/postgis:16-3.4')
        cls.addClassCleanup(command, 'docker', 'stop', cls.container, check=False)
        for _ in range(60):
            if command('docker', 'exec', cls.container, 'pg_isready', '-h', '127.0.0.1',
                       '-U', 'postgres', check=False).returncode == 0:
                break
            time.sleep(.5)
        else:
            raise RuntimeError('PostGIS local indisponible')
        cls.port = command('docker', 'port', cls.container, '5432').stdout.strip().rsplit(':', 1)[-1]
        with cls.connect() as db, db.cursor() as cur:
            cur.execute('CREATE SCHEMA extensions; CREATE EXTENSION postgis WITH SCHEMA extensions;')
            cur.execute((ROOT / 'model/schema_v0.6.1.sql').read_text())
            cur.execute('CREATE ROLE ah_reader; CREATE ROLE ah_writer; CREATE ROLE ah_operator LOGIN;'
                        'GRANT USAGE ON SCHEMA gemapi,extensions TO ah_reader,ah_writer,ah_operator;'
                        'GRANT SELECT ON gemapi.ah_sections,gemapi.ah_sections_carte,gemapi.cours_eau,'
                        'gemapi.ah_stockage TO ah_reader;'
                        'GRANT SELECT,INSERT,UPDATE,DELETE ON gemapi.ah_sections TO ah_writer,ah_operator;'
                        'GRANT SELECT ON gemapi.cours_eau TO ah_writer,ah_operator;'
                        'GRANT SELECT ON gemapi.ah TO ah_writer,ah_operator;'
                        'GRANT UPDATE(direction) ON gemapi.cours_eau TO ah_operator;'
                        'GRANT SELECT,UPDATE(geom) ON gemapi.ah_stockage TO ah_writer,ah_operator;')

    @classmethod
    def connect(cls, user='postgres'):
        return psycopg2.connect(host='127.0.0.1', port=cls.port, dbname='postgres', user=user)

    def setUp(self):
        self.db = self.connect()
        self.ah = str(uuid4())
        self.other_ah = str(uuid4())
        self.courses = []
        self.q('SET search_path=pg_catalog,extensions,public')
        self.q('INSERT INTO gemapi.ah(id,nom) VALUES (%s,\'A\'),(%s,\'B\')', (self.ah, self.other_ah))
        self.river = self.course('MULTILINESTRING((0 0,1000 0))')
        for parent in (self.ah,self.other_ah):
            self.q("INSERT INTO gemapi.ah_stockage(ah_id,nom,geom) VALUES (%s,'Fictif',"
                   "ST_GeomFromText('POLYGON((100 -10,300 -10,300 150,100 150,100 -10))',3950))",(parent,))

    def tearDown(self):
        self.db.rollback()
        parents=self.q('DELETE FROM gemapi.digues_reperes WHERE riviere_id=ANY(%s::uuid[]) RETURNING digue_id', (self.courses,))
        if parents:
            self.q('DELETE FROM gemapi.digues WHERE id=ANY(%s::uuid[])',([str(row[0]) for row in parents],))
        self.q('DELETE FROM gemapi.ah_sections WHERE ah_id IN (%s,%s)', (self.ah, self.other_ah))
        self.q('DELETE FROM gemapi.ah_stockage WHERE ah_id IN (%s,%s)', (self.ah, self.other_ah))
        self.q('DELETE FROM gemapi.cours_eau WHERE id=ANY(%s::uuid[])', (self.courses,))
        self.q('DELETE FROM gemapi.ah WHERE id IN (%s,%s)', (self.ah, self.other_ah))
        self.db.commit()
        self.db.close()

    def q(self, statement, args=None):
        with self.db.cursor() as cur:
            cur.execute(statement, args)
            return cur.fetchall() if cur.description else None

    def course(self, wkt, direction=True):
        uid = str(uuid4())
        self.courses.append(uid)
        self.q('INSERT INTO gemapi.cours_eau(id,nom,source,direction,geom) '
               'VALUES (%s,\'Rivière fictive\',\'TEST\',%s,ST_GeomFromText(%s,3950))', (uid, direction, wkt))
        return uid

    def section(self, start=.1, end=.3, parent=None, river=None):
        return self.q('INSERT INTO gemapi.ah_sections(ah_id,cours_eau_id,debut_fraction,fin_fraction) '
                      'VALUES (%s,%s,%s,%s) RETURNING id::text',
                      (parent or self.ah, river or self.river, start, end))[0][0]

    def state(self, uid):
        return self.q('SELECT etat_reference FROM gemapi.ah_sections_carte WHERE id=%s', (uid,))[0][0]

    def repere(self, river=None):
        digue = self.q("INSERT INTO gemapi.digues(nom) VALUES ('TEST qualification') RETURNING id")[0][0]
        return self.q("INSERT INTO gemapi.digues_reperes(digue_id,riviere_id,rive,debut_fraction,fin_fraction) "
                      "VALUES (%s,%s,'G',.1,.3) RETURNING id::text", (digue,river or self.river))[0][0]

    def digue_snapshot(self, uid):
        return self.q('SELECT riviere_id,debut_fraction,fin_fraction,ST_AsEWKB(geom_reference),'
                      'geom_riviere_signature FROM gemapi.digues_reperes WHERE id=%s',(uid,))[0]

    def digue_state(self, uid):
        return self.q('SELECT etat_referentiel FROM gemapi.digues_carte WHERE id=%s',(uid,))[0][0]

    def test_common_control_requires_both_qualification_fields(self):
        for direction,valid,expected in [(True,True,True),(True,False,False),(True,None,False),
                                        (False,True,False),(None,True,False)]:
            with self.subTest(direction=direction,valid=valid):
                self.assertEqual(self.q('SELECT gemapi.cours_eau_reference_admissible(geom,%s,%s) '
                                        'FROM gemapi.cours_eau WHERE id=%s',
                                        (direction,valid,self.river))[0][0],expected)
        self.assertFalse(self.q("SELECT gemapi.cours_eau_reference_admissible("
                                "ST_GeomFromText('MULTILINESTRING((0 0,0 0))',3950),TRUE,TRUE)")[0][0])
        self.assertIsNone(self.q("SELECT to_regprocedure('gemapi.ah_cours_eau_admissible(geometry,boolean)')")[0][0])

    def test_digue_and_ah_use_same_qualification(self):
        for wkt,direction in [('MULTILINESTRING((0 0,10 0))',None),
                               ('MULTILINESTRING((0 0,10 0))',False),
                               ('MULTILINESTRING((0 0,10 0),(10 0,20 0))',True),
                               ('MULTILINESTRING((0 0,10 10,0 10,10 0))',True),
                               ('MULTILINESTRING((0 0,10 0,10 10,0 0))',True)]:
            river=self.course(wkt,direction)
            for create in (self.repere,self.section):
                with self.subTest(wkt=wkt,direction=direction,create=create.__name__),self.rejected('23514'):
                    create(river=river)
        self.assertEqual(self.digue_state(self.repere()),'CONFORME')
        self.assertEqual(self.state(self.section()),'CONFORME')

    def test_unqualified_digue_is_retained_and_signalled(self):
        uid=self.repere()
        original=self.digue_snapshot(uid)
        for direction in (None,False):
            self.q('UPDATE gemapi.cours_eau SET direction=%s WHERE id=%s',(direction,self.river))
            self.assertEqual(self.digue_state(uid),'COURS_EAU_NON_QUALIFIE')
            with self.rejected('23514'):
                self.q("UPDATE gemapi.digues_reperes SET rive='D' WHERE id=%s",(uid,))
            self.q("UPDATE gemapi.digues_reperes SET debut_fraction=debut_fraction WHERE id=%s",(uid,))
            self.assertEqual(self.digue_snapshot(uid),original)
            with self.rejected('23514'):
                self.q('UPDATE gemapi.digues_reperes SET debut_fraction=.15,revision_validation=revision_validation+1 WHERE id=%s',(uid,))
        other=self.course('MULTILINESTRING((0 5,1000 5))',None)
        with self.rejected('23514'):
            self.q('UPDATE gemapi.digues_reperes SET riviere_id=%s,revision_validation=revision_validation+1 WHERE id=%s',(other,uid))
        self.q('UPDATE gemapi.cours_eau SET direction=TRUE WHERE id=%s',(self.river,))
        self.q('UPDATE gemapi.digues_reperes SET debut_fraction=.15,revision_validation=revision_validation+1 WHERE id=%s',(uid,))
        self.assertEqual(self.digue_state(uid),'CONFORME')

    def test_digue_hydrographic_change_does_not_recalculate_snapshot(self):
        uid=self.repere()
        original=self.digue_snapshot(uid)
        self.q("UPDATE gemapi.cours_eau SET geom=geom,nom='Renommé' WHERE id=%s",(self.river,))
        self.assertEqual(self.digue_snapshot(uid),original)
        self.assertEqual(self.digue_state(uid),'CONFORME')
        self.q('UPDATE gemapi.cours_eau SET geom=ST_Translate(geom,0,1) WHERE id=%s',(self.river,))
        self.assertEqual(self.digue_snapshot(uid),original)
        self.assertEqual(self.digue_state(uid),'REFERENTIEL_MODIFIE')
        with self.rejected('23514'):
            self.q('UPDATE gemapi.digues_reperes SET fin_fraction=.4,revision_validation=revision_validation+1 WHERE id=%s',(uid,))

    def frozen(self, uid):
        return self.q('SELECT id,ah_id,cours_eau_id,debut_fraction,fin_fraction,'
                      'ST_AsEWKB(geom_reference),geom_cours_eau_signature,'
                      'revision_validation,valide_par,date_validation FROM gemapi.ah_sections WHERE id=%s', (uid,))[0]

    @contextlib.contextmanager
    def rejected(self, code):
        self.q('SAVEPOINT rejected')
        try:
            with self.assertRaises(psycopg2.Error) as caught:
                yield
            self.assertEqual(caught.exception.pgcode, code)
        finally:
            self.q('ROLLBACK TO SAVEPOINT rejected')
            self.q('RELEASE SAVEPOINT rejected')

    def test_server_snapshot_and_trace(self):
        uid = self.section()
        self.assertEqual(self.state(uid), 'CONFORME')
        values = self.q('SELECT ST_AsText(geom_reference),ST_SRID(geom_reference),'
                        'geom_cours_eau_signature=md5(encode(ST_AsEWKB(r.geom),\'hex\')),'
                        'valide_par=session_user,s.modifie_par=session_user,'
                        'date_validation IS NOT NULL,s.date_modification IS NOT NULL,revision_validation '
                        'FROM gemapi.ah_sections s JOIN gemapi.cours_eau r ON r.id=s.cours_eau_id WHERE s.id=%s', (uid,))[0]
        self.assertEqual(values, ('LINESTRING(100 0,300 0)', 3950, True, True, True, True, True, 1))
        with self.rejected('23514'):
            self.q('UPDATE gemapi.ah_sections SET id=gen_random_uuid() WHERE id=%s', (uid,))
        with self.rejected('23514'):
            self.q('UPDATE gemapi.ah_sections SET geom_cours_eau_signature=\'forgée\' WHERE id=%s', (uid,))
        with self.rejected('23514'):
            self.q('UPDATE gemapi.ah_sections SET geom_reference=ST_Translate(geom_reference,1,0) WHERE id=%s', (uid,))
        with self.rejected('23514'):
            self.q('UPDATE gemapi.ah_sections SET valide_par=\'autre\' WHERE id=%s', (uid,))

    def test_fractions(self):
        for start, end in [(0,0),(.8,.2),(-.1,.2),(.2,1.1),(.2,float('nan')),
                           (float('nan'),.3),(float('-inf'),.3),(.2,float('inf')),(None,.3),(.1,None)]:
            with self.subTest(start=start, end=end), self.rejected('23514'):
                self.section(start,end)
        self.q("UPDATE gemapi.ah_stockage SET geom=ST_GeomFromText('POLYGON((0 -10,1000 -10,1000 10,0 10,0 -10))',3950) WHERE ah_id=%s",(self.ah,))
        uid = self.section(0,1)
        self.assertEqual(self.q('SELECT ST_Length(geom_reference) FROM gemapi.ah_sections WHERE id=%s', (uid,))[0][0],1000)

    def test_qualification_and_geometry(self):
        for wkt, direction in [('MULTILINESTRING((0 0,1 0))',None),
                               ('MULTILINESTRING((0 0,1 0))',False),
                               ('MULTILINESTRING((0 0,1 0),(2 0,3 0))',True),
                               ('MULTILINESTRING((0 0,2 2,0 2,2 0))',True),
                               ('MULTILINESTRING((0 0,2 0,2 2,0 0))',True)]:
            river=self.course(wkt,direction)
            with self.subTest(wkt=wkt,direction=direction),self.rejected('23514'):
                self.section(river=river)
        # Degenerate, invalid geometries are already blocked by the course contract.
        with self.rejected('23514'):
            self.course('MULTILINESTRING((0 0,0 0))')

    def test_independent_multiple_and_shared_sections(self):
        first=self.section()
        outside=self.section(.5,.6)
        self.assertFalse(self.q('SELECT ST_CoveredBy(s.geom_reference,a.geom) FROM gemapi.ah_sections s '
                                'JOIN gemapi.ah_stockage a ON a.ah_id=s.ah_id WHERE s.id=%s',(outside,))[0][0])
        self.assertEqual(self.state(outside),'CONFORME')
        self.section(parent=self.other_ah)
        river=self.course('MULTILINESTRING((0 100,1000 100))')
        self.section(river=river)
        self.assertEqual(self.q('SELECT count(*) FROM gemapi.ah_sections WHERE ah_id=%s',(self.ah,))[0][0],3)
        with self.rejected('23505'):
            self.section()
        self.section(.2,.4)
        with self.rejected('23503'):
            self.q('DELETE FROM gemapi.ah WHERE id=%s',(self.ah,))
        with self.rejected('23503'):
            self.q('DELETE FROM gemapi.cours_eau WHERE id=%s',(self.river,))
        self.assertEqual(self.state(first),'CONFORME')
        self.assertEqual(self.q('SELECT count(*) FROM gemapi.ah_stockage WHERE ah_id=%s',(self.ah,))[0][0],1)

    def test_required_foreign_keys(self):
        for parent, river, code in [(str(uuid4()),self.river,'23503'),
                                     (None,self.river,'23503'),(self.ah,str(uuid4()),'23503'),
                                     (self.ah,None,'23503')]:
            with self.subTest(parent=parent,river=river),self.rejected(code):
                self.q('INSERT INTO gemapi.ah_sections(ah_id,cours_eau_id,debut_fraction,fin_fraction) '
                       'VALUES (%s,%s,.1,.2)',(parent,river))

    def test_attribute_and_identical_geometry_preserve_snapshot(self):
        uid=self.section()
        original=self.frozen(uid)
        self.q('UPDATE gemapi.cours_eau SET nom=\'Renommée\',geom=geom WHERE id=%s',(self.river,))
        self.q('UPDATE gemapi.ah_sections SET commentaire=\'Revue documentaire\' WHERE id=%s',(uid,))
        self.assertEqual(self.frozen(uid),original)
        self.assertEqual(self.state(uid),'CONFORME')
        self.q('UPDATE gemapi.cours_eau SET direction=NULL WHERE id=%s',(self.river,))
        self.assertEqual(self.state(uid),'COURS_EAU_NON_QUALIFIE')
        self.assertEqual(self.frozen(uid),original)
        self.q('UPDATE gemapi.cours_eau SET direction=FALSE WHERE id=%s',(self.river,))
        self.assertEqual(self.state(uid),'COURS_EAU_NON_QUALIFIE')

    def test_stale_snapshot_and_explicit_revalidation(self):
        uid=self.section()
        original=self.frozen(uid)
        # Densification preserves topology but changes the vertex reference signature.
        self.q('UPDATE gemapi.cours_eau SET geom=ST_Segmentize(geom,100) WHERE id=%s',(self.river,))
        self.assertEqual(self.state(uid),'REFERENTIEL_MODIFIE')
        self.assertEqual(self.frozen(uid),original)
        self.q('UPDATE gemapi.ah_sections SET commentaire=\'À revoir\' WHERE id=%s',(uid,))
        self.assertEqual(self.frozen(uid),original)
        with self.rejected('23514'):
            self.q('UPDATE gemapi.ah_sections SET revision_validation=revision_validation+1 WHERE id=%s',(uid,))
        self.q('UPDATE gemapi.cours_eau SET direction=TRUE WHERE id=%s',(self.river,))
        self.assertEqual(self.state(uid),'REFERENTIEL_MODIFIE')
        with self.rejected('23514'):
            self.q('UPDATE gemapi.ah_sections SET debut_fraction=.15 WHERE id=%s',(uid,))
        for value in [None,0,3]:
            with self.rejected('23514'):
                self.q('UPDATE gemapi.ah_sections SET revision_validation=%s WHERE id=%s',(value,uid))
        self.q('UPDATE gemapi.ah_sections SET revision_validation=revision_validation+1 WHERE id=%s',(uid,))
        current=self.frozen(uid)
        self.assertEqual(current[0],original[0])
        self.assertNotEqual(current[6],original[6])
        self.assertEqual(current[7],2)
        self.assertGreater(current[9],original[9])
        self.assertEqual(self.state(uid),'CONFORME')
        self.q('UPDATE gemapi.ah_sections SET debut_fraction=.15,revision_validation=revision_validation+1 WHERE id=%s',(uid,))
        self.assertEqual(self.frozen(uid)[3],.15)

    def test_candidates_disjoint_two_rivers_shared_and_point_contact(self):
        # One connected Polygon intersects each river in two disjoint intervals.
        polygon='POLYGON((100 -10,200 -10,200 40,400 40,400 -10,500 -10,500 60,100 60,100 -10))'
        river2=self.course('MULTILINESTRING((0 20,1000 20))')
        self.q('UPDATE gemapi.ah_stockage SET geom=ST_GeomFromText(%s,3950) WHERE ah_id=%s',(polygon,self.ah))
        rows=self.q('SELECT cours_eau_id::text,debut_fraction,fin_fraction,ST_Length(geom_reference) '
                    'FROM gemapi.ah_sections_candidates(%s)',(self.ah,))
        self.assertEqual(sorted((r[1],r[2],r[3]) for r in rows),[(.1,.2,100),(.1,.2,100),(.4,.5,100),(.4,.5,100)])
        self.assertEqual({r[0] for r in rows},{self.river,river2})
        self.assertEqual(self.q('SELECT count(*) FROM gemapi.ah_sections WHERE ah_id=%s',(self.ah,))[0][0],0)
        self.q('UPDATE gemapi.ah_stockage SET geom=ST_GeomFromText(%s,3950) WHERE ah_id=%s',(polygon,self.other_ah))
        self.assertEqual(len(self.q('SELECT * FROM gemapi.ah_sections_candidates(%s)',(self.other_ah,))),4)
        self.q('UPDATE gemapi.ah_stockage SET geom=ST_GeomFromText(\'POLYGON((100 0,90 -10,110 -10,100 0))\',3950) WHERE ah_id=%s',(self.ah,))
        self.assertEqual(self.q('SELECT * FROM gemapi.ah_sections_candidates(%s)',(self.ah,)),[])

    def test_candidates_require_qualification_and_no_storage_is_allowed(self):
        self.q('DELETE FROM gemapi.ah_stockage WHERE ah_id=%s',(self.ah,))
        self.assertEqual(self.q('SELECT * FROM gemapi.ah_sections_candidates(%s)',(self.ah,)),[])
        self.section()
        self.q('INSERT INTO gemapi.ah_stockage(ah_id,nom,geom) VALUES (%s,\'S\','
               'ST_GeomFromText(\'POLYGON((100 -1,200 -1,200 1,100 1,100 -1))\',3950))',(self.ah,))
        self.q('UPDATE gemapi.cours_eau SET direction=NULL WHERE id=%s',(self.river,))
        self.assertEqual(self.q('SELECT * FROM gemapi.ah_sections_candidates(%s)',(self.ah,)),[])
        self.q('UPDATE gemapi.cours_eau SET direction=TRUE WHERE id=%s',(self.river,))
        self.assertEqual(len(self.q('SELECT * FROM gemapi.ah_sections_candidates(%s)',(self.ah,))),1)

    def test_rollback_and_atomic_refusal(self):
        self.db.commit()
        self.section()
        self.db.rollback()
        self.assertEqual(self.q('SELECT count(*) FROM gemapi.ah_sections WHERE ah_id=%s',(self.ah,))[0][0],0)
        with self.rejected('23514'):
            self.q('INSERT INTO gemapi.ah_sections(ah_id,cours_eau_id,debut_fraction,fin_fraction) '
                   'VALUES (%s,%s,.1,.2),(%s,%s,.9,.1)',(self.ah,self.river,self.ah,self.river))
        self.assertEqual(self.q('SELECT count(*) FROM gemapi.ah_sections WHERE ah_id=%s',(self.ah,))[0][0],0)
        try:
            self.section()
            self.section(parent=str(uuid4()))
        except psycopg2.Error as exc:
            self.assertEqual(exc.pgcode,'23503')
            self.db.rollback()
        else:
            self.fail('FK invalide acceptée')
        self.assertEqual(self.q('SELECT count(*) FROM gemapi.ah_sections WHERE ah_id=%s',(self.ah,))[0][0],0)
        uid=self.section()
        self.db.commit()
        with self.connect() as other,other.cursor() as cur:
            cur.execute('SELECT revision_validation FROM gemapi.ah_sections WHERE id=%s',(uid,))
            self.assertEqual(cur.fetchone(),(1,))

    def test_read_rights_and_no_privilege_escalation(self):
        uid=self.section()
        self.q('SET LOCAL ROLE ah_reader')
        self.assertEqual(self.state(uid),'CONFORME')
        self.assertEqual(len(self.q('SELECT * FROM gemapi.ah_sections_candidates(%s)',(self.ah,))),1)
        with self.rejected('42501'):
            self.section(.5,.6)
        self.q('RESET ROLE')
        self.q('REVOKE SELECT ON gemapi.cours_eau FROM ah_reader')
        self.q('SET LOCAL ROLE ah_reader')
        with self.rejected('42501'):
            self.state(uid)
        self.q('RESET ROLE')
        self.assertEqual(self.q("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                                "WHERE n.nspname='gemapi' AND p.proname LIKE 'ah_%' AND p.prosecdef")[0][0],0)

    def test_writer_requires_course_lock_right_and_validation_is_server_authored(self):
        self.q('SET LOCAL ROLE ah_writer')
        with self.rejected('42501'):
            self.section()
        self.q('RESET ROLE')
        self.q('GRANT UPDATE(direction) ON gemapi.cours_eau TO ah_writer')
        self.q('REVOKE ALL ON gemapi.ah_stockage FROM ah_writer')
        self.q('SET LOCAL ROLE ah_writer')
        uid=self.section()
        self.q('RESET ROLE')
        self.assertEqual(self.state(uid),'CONFORME')
        self.db.commit()
        with self.connect('ah_operator') as db, db.cursor() as cur:
            cur.execute('UPDATE gemapi.ah_sections SET revision_validation=revision_validation+1,'
                        'valide_par=\'client\',date_validation=\'2000-01-01\' WHERE id=%s '
                        'RETURNING valide_par,modifie_par,date_validation>\'2020-01-01\'',(uid,))
            self.assertEqual(cur.fetchone(),('ah_operator','ah_operator',True))

    def test_concurrent_course_change_is_serialized(self):
        self.db.commit()
        uid=self.section()
        frozen=self.frozen(uid)
        with self.connect() as other,other.cursor() as cur:
            cur.execute("SET search_path=pg_catalog,extensions,public; SET lock_timeout='200ms'")
            with self.assertRaises(psycopg2.Error) as caught:
                cur.execute('UPDATE gemapi.cours_eau SET geom=ST_Translate(geom,0,1) WHERE id=%s',(self.river,))
            self.assertEqual(caught.exception.pgcode,'55P03')
            other.rollback()
            self.db.commit() # Release the shared course lock.
            cur.execute('SET search_path=pg_catalog,extensions,public')
            cur.execute('UPDATE gemapi.cours_eau SET geom=ST_Translate(geom,0,1) WHERE id=%s',(self.river,))
        self.assertEqual(self.state(uid),'REFERENTIEL_MODIFIE')
        self.assertEqual(self.frozen(uid),frozen)

    def test_existing_course_change_prevents_outdated_validation(self):
        self.db.commit()
        with self.connect() as other,other.cursor() as cur:
            cur.execute('SET search_path=pg_catalog,extensions,public')
            cur.execute('UPDATE gemapi.cours_eau SET geom=ST_Translate(geom,0,1) WHERE id=%s',(self.river,))
            self.q("SET LOCAL lock_timeout='200ms'")
            with self.rejected('55P03'):
                self.section()
            other.commit()
        with self.rejected('23514'):
            self.section()
        self.assertEqual(self.q('SELECT count(*) FROM gemapi.ah_sections WHERE ah_id=%s',(self.ah,))[0][0],0)

    def test_storage_change_or_removal_has_no_effect(self):
        uid=self.section()
        original=self.frozen(uid)
        self.q("UPDATE gemapi.ah_stockage SET nom='Autre',geom=geom WHERE ah_id=%s",(self.ah,))
        self.assertEqual(self.state(uid),'CONFORME')
        self.q('UPDATE gemapi.ah_stockage SET geom=ST_Translate(geom,100,0) WHERE ah_id=%s',(self.ah,))
        self.assertEqual(self.state(uid),'CONFORME')
        self.assertEqual(self.frozen(uid),original)
        self.q('UPDATE gemapi.ah_sections SET revision_validation=2 WHERE id=%s',(uid,))
        self.q('UPDATE gemapi.ah_sections SET debut_fraction=.2,fin_fraction=.4,revision_validation=3 WHERE id=%s',(uid,))
        self.assertEqual(self.state(uid),'CONFORME')
        self.q('DELETE FROM gemapi.ah_stockage WHERE ah_id=%s',(self.ah,))
        self.assertEqual(self.state(uid),'CONFORME')
        self.section(.6,.9)

    def test_explicit_revision_can_change_course_support(self):
        uid=self.section()
        river=self.course('MULTILINESTRING((0 300,1000 300))')
        with self.rejected('23514'):
            self.q('UPDATE gemapi.ah_sections SET cours_eau_id=%s WHERE id=%s',(river,uid))
        self.q('UPDATE gemapi.ah_sections SET cours_eau_id=%s,revision_validation=2 WHERE id=%s',(river,uid))
        self.assertEqual(self.state(uid),'CONFORME')
        self.assertEqual(self.q('SELECT ST_AsText(geom_reference) FROM gemapi.ah_sections WHERE id=%s',(uid,))[0][0],
                         'LINESTRING(100 300,300 300)')
        original=self.frozen(uid)
        unqualified=self.course('MULTILINESTRING((0 400,1000 400))',None)
        with self.rejected('23514'):
            self.q('UPDATE gemapi.ah_sections SET cours_eau_id=%s,revision_validation=3 WHERE id=%s',(unqualified,uid))
        self.assertEqual(self.frozen(uid),original)

    def test_storage_update_is_independent_of_validation(self):
        self.db.commit()
        self.section()
        with self.connect() as other,other.cursor() as cur:
            cur.execute("SET search_path=pg_catalog,extensions,public; SET lock_timeout='200ms'")
            cur.execute('UPDATE gemapi.ah_stockage SET geom=ST_Translate(geom,1,0) WHERE ah_id=%s',(self.ah,))
            other.rollback()

    def test_validation_is_independent_of_storage_update(self):
        self.db.commit()
        with self.connect() as other,other.cursor() as cur:
            cur.execute('SET search_path=pg_catalog,extensions,public')
            cur.execute('UPDATE gemapi.ah_stockage SET geom=ST_Translate(geom,100,0) WHERE ah_id=%s',(self.ah,))
            self.q("SET LOCAL lock_timeout='200ms'")
            uid=self.section(.7,.9)
            other.commit()
        self.section()
        uid=self.section(.2,.4)
        self.assertEqual(self.state(uid),'CONFORME')


if __name__ == '__main__':
    unittest.main(verbosity=2)
