"""ENTITY-TYPE-02 level B (~3 min), real browser/API/sessions/PostGIS.

Extends the existing C5 fixture rather than replacing its guarantees. Isolated
UUIDs, EWKB and associated rows checked by C5; new scenarios cover both hosts,
conditional fields, API diagnostics, draft retention and OCC conflicts.
"""
import os
import unittest
from webapp.tests import test_troncons_c5_browser_postgres as c5


@unittest.skipUnless(os.getenv('DIGUES_MULTIBASE_TEST_ADMIN_DSN'), 'Official disposable runner required')
class TronconRequalificationBrowserTest(c5.CanonicalTronconBrowserTest):
    browser_environment = {'DIGUES_ENTITY_TYPE_02': '1'}
    # The complete C5 journey plus both subtype hosts and a real extra conflict.
    browser_timeout = 600
