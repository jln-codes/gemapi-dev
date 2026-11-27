"""A/B, ~1 min: simulated metadata/SSL and real bounded probe scheduling.

No provider or application DSN. Negative cases cover redirects before follow,
HTTP-200 HTML, bounds, trickling bodies, expiring certificates and secret leaks.
Business cache/session invariance is checked against the actual module objects.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from io import BytesIO
import json
import os
import ssl
from threading import Event
import time
import unittest
from unittest.mock import MagicMock, patch

import requests
from digues_webapp import diagnostic_external as ext, admin_diagnostics as diag
from digues_webapp import weather, vigicrues, hydrometry, service_observations

WCS = b'''<wcs:Capabilities xmlns:wcs="http://www.opengis.net/wcs/2.0" xmlns:ows="http://www.opengis.net/ows/2.0" version="2.0.1"><ows:ServiceIdentification><ows:ServiceType>WCS</ows:ServiceType></ows:ServiceIdentification>'''
HUB = json.dumps({'count': 4, 'data': [{'code_site': 'synthetic'}]}).encode()
VIG = json.dumps({'GrdSimul': 'H', 'ListEntVigiCru': [{'CdEntVigiCru': 'synthetic', 'TypEntVigiCru': 7}]}).encode()
WFS = b'<wfs:FeatureCollection xmlns:wfs="http://www.opengis.net/wfs/2.0" numberMatched="0" numberReturned="0"/>'


class Response:
    def __init__(self, body, *, status=200, headers=None):
        self.raw = BytesIO(body); self.status_code = status; self.headers = headers or {}; self.history = []; self.url = ''; self.closed = False
    def __enter__(self): return self
    def __exit__(self, *_): self.closed = True


class ActiveMetadataTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'AROME_API_KEY': 'private-sentinel-arome', 'ARPEGE_API_KEY': 'private-sentinel-arpege'}))
        self.session = MagicMock()
        self.enterContext(patch.object(ext.requests, 'Session', return_value=self.session))
        self.session.__enter__.return_value = self.session

    def response(self, body, **kw):
        response = Response(body, **kw)
        def get(url, **options):
            response.url = url; self.options = options; return response
        self.session.get.side_effect = get
        return response

    def test_all_provider_contracts_reuse_urls_without_business_state_or_large_catalogues(self):
        session = weather._weather_session
        snapshots = [deepcopy(v) for v in [weather._weather_cache, hydrometry._discovery_cache, hydrometry._structural_cache,
                    hydrometry._observation_cache, hydrometry._forecast_cache, service_observations._observations]]
        with patch.object(weather, '_make_clients', side_effect=AssertionError('No business session')):
            for name, body, status in [('arome', WCS + b'CATALOGUE-MUST-NOT-BE-READ'*100000, 200), ('arpege', WCS, 200),
                                      ('hubeau', HUB, 206), ('topage', WFS, 200), ('vigicrues', VIG, 200)]:
                with self.subTest(name=name):
                    r = self.response(body, status=status); value = ext.probe(name)
                    self.assertEqual(value['status'], 'ok', value)
                    self.assertLessEqual(value['response_bytes'], ext.MAX_BYTES)
                    self.assertTrue(r.closed)
                    self.assertFalse(self.session.trust_env)
                    self.assertFalse(self.options['allow_redirects'])
                    self.assertTrue(self.options['stream'])
                    self.assertEqual(self.options['timeout'], (ext.IO_TIMEOUT, ext.IO_TIMEOUT))
                    self.assertNotIn('private-sentinel', json.dumps(value))
                    if name == 'arome': self.assertLess(r.raw.tell(), len(body))
        self.assertIs(weather._weather_session, session)
        self.assertEqual(snapshots, [weather._weather_cache, hydrometry._discovery_cache, hydrometry._structural_cache,
                    hydrometry._observation_cache, hydrometry._forecast_cache, service_observations._observations])

    def test_missing_model_key_does_not_probe_other_model_or_connect(self):
        for name, envkey in [('arome', 'AROME_API_KEY'), ('arpege', 'ARPEGE_API_KEY')]:
            with patch.dict(os.environ, {envkey: ''}):
                result = ext.probe(name)
                self.assertEqual(result['status'], 'not_configured'); self.assertFalse(result['network_attempted'])
        self.session.get.assert_not_called()

    def test_http_success_is_not_functional_success(self):
        for name, body in [('arome', b'<html>ok</html>'), ('arpege', WCS.replace(b'>WCS<', b'>WRONG<')),
                           ('hubeau', b'{"data":[],"count":0}'), ('vigicrues', b'{}'), ('topage', b'<error numberMatched="0" numberReturned="0"/>'),
                           ('topage', b'<!DOCTYPE a [<!ENTITY x "secret">]><a/>'), ('topage', b'\xff'),
                           ('vigicrues', b'{"GrdSimul":"Q","ListEntVigiCru":[]}')]:
            with self.subTest(name=name, body=body):
                self.response(body); result = ext.probe(name)
                self.assertEqual(result['status'], 'error'); self.assertEqual(result['code'], 'format')

    def test_redirects_refused_before_follow_and_errors_do_not_echo_secrets(self):
        for status in [301, 302, 307, 308, 401, 403, 429, 503]:
            self.session.get.reset_mock()
            self.response(b'private-sentinel-body', status=status, headers={'Location': 'https://evil.invalid/?secret=private-sentinel'})
            with self.assertNoLogs('digues_webapp', level='WARNING'):
                value = ext.probe('arome')
            self.assertEqual(value['status'], 'error'); self.assertNotIn('private-sentinel', json.dumps(value))
            self.assertEqual(value['code'], 'redirect' if status < 400 else 'http_status')
            self.assertEqual(value['http_status'], status)
            self.session.get.assert_called_once()
        for error in [requests.Timeout('private-sentinel'), requests.ConnectionError('private-sentinel'), requests.exceptions.SSLError('private-sentinel')]:
            self.session.get.side_effect = error
            value = ext.probe('arome'); self.assertEqual(value['status'], 'error'); self.assertNotIn('private-sentinel', json.dumps(value))

    def test_response_size_encoding_and_absolute_deadline_are_bounded(self):
        for body, headers, code in [(b' '* (ext.MAX_BYTES+1), {}, 'too_large'), (b'{}', {'Content-Length': str(ext.MAX_BYTES+1)}, 'too_large'),
                                    (b'{}', {'Content-Encoding': 'gzip'}, 'encoding')]:
            r = self.response(body, headers=headers); value = ext.probe('hubeau')
            self.assertEqual(value['code'], code); self.assertLessEqual(r.raw.tell(), ext.MAX_BYTES+1)
        r = self.response(b' ' * 100000)
        clock = iter([0, .1, .2, .3, .4, .5, .6, .7])
        with patch.object(ext.time, 'monotonic', side_effect=lambda: next(clock)):
            with self.assertRaises(ext.ProbeFailure) as caught:
                ext._read(hydrometry.HUBEAU_SITES_URL, {}, {}, .35)
        self.assertEqual(caught.exception.code, 'timeout'); self.assertLess(r.raw.tell(), 100000)

    def test_wcs_entity_or_truncation_is_not_accepted_and_cross_chunk_metadata_is(self):
        validator = ext.WcsMetadata(); data = bytearray()
        for chunk in [WCS[:8], WCS[8:70], WCS[70:]]:
            data.extend(chunk); completed = validator.feed(chunk, data)
        self.assertTrue(completed)
        for body in [WCS[:50], b'<!DOCTYPE x [<!ENTITY x "secret">]>' + WCS, WCS.replace(b'WCS', b'bad')]:
            self.response(body); self.assertEqual(ext.probe('arome')['status'], 'error')


class HttpsTest(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {'AUTH_ORIGIN': 'https://business.test', 'AUTH_ALLOW_INSECURE_LOCALHOST': 'false',
                'ADMIN_ORIGIN': 'https://admin.test', 'ADMIN_ALLOW_INSECURE_LOCALHOST': 'false'}))
        self.context = MagicMock(); self.context.check_hostname = True; self.context.verify_mode = ssl.CERT_REQUIRED
        self.connect = self.enterContext(patch.object(ext.socket, 'create_connection'))
        self.create_context = self.enterContext(patch.object(ext.ssl, 'create_default_context', return_value=self.context))
        self.peer = self.context.wrap_socket.return_value.__enter__.return_value
        self.peer.getpeercert.return_value = {'notAfter': (datetime.now(timezone.utc)+timedelta(days=20)).strftime('%b %d %H:%M:%S %Y GMT')}

    def test_both_configured_origins_use_verified_tls_and_report_expiration_only(self):
        for name, host in [('https_business', 'business.test'), ('https_admin', 'admin.test')]:
            value = ext.probe(name); self.assertEqual(value['status'], 'ok'); self.assertGreater(value['certificate_remaining_days'], 18)
            self.assertIn('certificate_expires_at', value); self.connect.assert_called_with((host, 443), timeout=ext.IO_TIMEOUT)
            self.assertEqual(self.context.wrap_socket.call_args.kwargs['server_hostname'], host)
        self.assertEqual(self.create_context.call_count, 2)
        self.assertTrue(self.context.check_hostname); self.assertEqual(self.context.verify_mode, ssl.CERT_REQUIRED)
        self.assertNotIn('key', json.dumps(value))

    def test_expired_and_untrusted_certificate_never_report_ok(self):
        self.peer.getpeercert.return_value = {'notAfter': 'Jan 01 00:00:00 2020 GMT'}
        self.assertEqual(ext.probe('https_business')['code'], 'certificate_expired')
        error = ssl.SSLCertVerificationError('private-sentinel'); error.verify_code = 10
        self.context.wrap_socket.side_effect = error
        value = ext.probe('https_business'); self.assertEqual(value['code'], 'certificate_expired'); self.assertNotIn('private-sentinel', json.dumps(value))
        error.verify_code = 62; self.assertEqual(ext.probe('https_admin')['code'], 'certificate_invalid')

    def test_missing_http_and_invalid_origins_do_not_connect(self):
        for origin, insecure, status in [('', 'false', 'not_configured'), ('http://localhost', 'true', 'not_configured'),
                                          ('https://secret@bad.test', 'false', 'error')]:
            with patch.dict(os.environ, {'AUTH_ORIGIN': origin, 'AUTH_ALLOW_INSECURE_LOCALHOST': insecure}):
                value = ext.probe('https_business'); self.assertEqual(value['status'], status); self.assertFalse(value['network_attempted'])
        self.connect.assert_not_called()


class CachedActiveChecksTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runner = diag.ProbeRunner(workers=7, ttl=300, cache_limit=7)
        self.addCleanup(self.runner.executor.shutdown)
        self.enterContext(patch.object(diag, 'external_runner', self.runner))
        self.probe = self.enterContext(patch.object(ext, 'probe', side_effect=lambda name: {
            'status': 'ok', 'check': 'synthetic', 'active_probe': True, 'latency_ms': 1, 'network_attempted': True}))

    async def test_repeated_and_concurrent_refreshes_share_cached_results_and_timestamps(self):
        first = await diag.active_external_checks(time.monotonic()+5)
        for cached in await asyncio.gather(*(diag.active_external_checks(time.monotonic()+5) for _ in range(3))):
            self.assertEqual(cached, first)
        self.assertEqual(self.probe.call_count, 7)
        for key,(stamp,value) in list(self.runner.cache.items()): self.runner.cache[key] = (stamp-301,value)
        await diag.active_external_checks(time.monotonic()+5)
        self.assertEqual(self.probe.call_count, 14)

    async def test_blocked_probe_keeps_its_slot_and_never_becomes_false_upstream_failure(self):
        release, entered = Event(), Event()
        def blocked(name):
            entered.set(); release.wait(5); return {'status': 'ok', 'check': 'synthetic', 'active_probe': True}
        self.probe.side_effect = blocked
        try:
            # Establish all seven occupied slots before the short HTTP deadline;
            # thread startup itself is not the behavior under test.
            for name in ext.PROBES:
                self.runner.submit(name, lambda name=name: ext.probe(name))
            self.assertTrue(await asyncio.to_thread(entered.wait, 2))
            values = await diag.active_external_checks(time.monotonic()+.02)
            self.assertEqual(len(self.runner.pending), 7)
            self.assertTrue(all(v['status']=='unknown' and v['checked_at'] is None for v in values.values()))
            self.assertLessEqual(self.probe.call_count, 7)
            await diag.active_external_checks(time.monotonic()+.01)
            self.assertLessEqual(self.probe.call_count, 7)
        finally: release.set()
