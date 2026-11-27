"""Isolated active metadata/TLS checks. No business cache, session or observations.

Provider URLs, credentials and response contracts come from the existing clients.
Their data-acquisition methods intentionally aren't called: retries, full catalogues
and raster/dataset budgets belong to business use, not administrative diagnostics.
"""
from datetime import datetime, timezone
import codecs
import json
import re
import socket
import ssl
import time
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

import requests
from urllib3.exceptions import TimeoutError as TransportTimeout

CACHE_SECONDS = 300
MAX_BYTES = 128 * 1024
IO_TIMEOUT = 1
PROBE_SECONDS = 3
PROBES = ('arome', 'arpege', 'hubeau', 'topage', 'vigicrues', 'https_business', 'https_admin')
REASONS = {
    'timeout': 'Délai court de la sonde dépassé.',
    'transport': 'Connexion fournisseur indisponible.',
    'tls': 'Vérification TLS refusée.',
    'certificate_expired': 'Certificat HTTPS expiré.',
    'certificate_invalid': 'Certificat HTTPS non valide pour cette origine ou chaîne non reconnue.',
    'redirect': 'Redirection refusée ; aucune autre destination contactée.',
    'http_status': 'Réponse HTTP inattendue du fournisseur.',
    'format': 'Format fonctionnel attendu absent ou invalide.',
    'too_large': 'Métadonnées dépassant la limite de lecture.',
    'encoding': 'Encodage de transport inattendu.',
    'configuration': 'Configuration de la sonde invalide.',
}


class ProbeFailure(Exception):
    def __init__(self, code, http_status=None):
        self.code = code
        self.http_status = http_status


def _xml_text(payload):
    try:
        text = payload.decode('utf-8-sig')
    except UnicodeError:
        raise ProbeFailure('format') from None
    if '\x00' in text or re.search(r'<!\s*(?:DOCTYPE|ENTITY)', text, re.I):
        raise ProbeFailure('format')
    return text


class WcsMetadata:
    """Validate the complete ServiceIdentification, not the unbounded catalogue.

    MF ignores the OWS sections parameter. Stop reading after this metadata node
    instead of downloading megabytes of CoverageIds. Never request GetCoverage.
    """
    def __init__(self):
        self.parser = ET.XMLPullParser(events=('start', 'end'))
        self.decoder = codecs.getincrementaldecoder('utf-8-sig')()
        self.started = False

    def feed(self, chunk, payload):
        # Reject DTD/entity declarations across chunk boundaries before feeding.
        if b'\x00' in payload or re.search(br'<!\s*(?:DOCTYPE|ENTITY)', payload, re.I):
            raise ProbeFailure('format')
        try:
            self.parser.feed(self.decoder.decode(chunk))
            for event, element in self.parser.read_events():
                if not self.started:
                    if event != 'start' or element.tag != '{http://www.opengis.net/wcs/2.0}Capabilities' or element.get('version') != '2.0.1':
                        raise ProbeFailure('format')
                    self.started = True
                if event == 'end' and element.tag in {'{http://www.opengis.net/ows/2.0}ServiceIdentification', '{http://www.opengis.net/ows/1.1}ServiceIdentification'}:
                    types = [e.text for e in element if e.tag in {'{http://www.opengis.net/ows/2.0}ServiceType', '{http://www.opengis.net/ows/1.1}ServiceType'}]
                    if types != ['WCS']:
                        raise ProbeFailure('format')
                    return True
        except (ET.ParseError, UnicodeError):
            raise ProbeFailure('format') from None
        return False


def _read(url, params, headers, deadline, *, prefix=None):
    # Fixed provider URL or configured origin only, never an HTTP query parameter.
    parsed = urlsplit(url)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query or parsed.port not in (None, 443):
        raise ProbeFailure('configuration')
    with requests.Session() as session:
        session.trust_env = False
        with session.get(url, params=params, headers={'Accept-Encoding': 'identity', **headers},
                         timeout=(IO_TIMEOUT, IO_TIMEOUT), allow_redirects=False, stream=True) as response:
            if 300 <= response.status_code < 400 or response.history:
                raise ProbeFailure('redirect', response.status_code)
            if response.url.split('?', 1)[0] != url:
                raise ProbeFailure('redirect')
            if response.status_code not in (200, 206):
                raise ProbeFailure('http_status', response.status_code)
            if response.headers.get('Content-Encoding', 'identity').lower() not in ('', 'identity'):
                raise ProbeFailure('encoding')
            # For prefix validation Content-Length describes the deliberately
            # unread remainder. All other responses must fit completely.
            length = response.headers.get('Content-Length')
            if not prefix and length and (not length.isdigit() or int(length) > MAX_BYTES):
                raise ProbeFailure('too_large')
            payload = bytearray()
            while True:
                if time.monotonic() >= deadline:
                    raise ProbeFailure('timeout')
                # read1 performs one buffered read: a trickling peer cannot hold
                # read(n) indefinitely by continually resetting a socket timeout.
                chunk = response.raw.read1(min(4096, MAX_BYTES + 1 - len(payload)))
                if not chunk:
                    break
                payload.extend(chunk)
                if time.monotonic() >= deadline:
                    raise ProbeFailure('timeout')
                if len(payload) > MAX_BYTES:
                    raise ProbeFailure('too_large')
                if prefix and prefix.feed(chunk, payload):
                    return bytes(payload)
            if prefix:
                raise ProbeFailure('format')
            return bytes(payload)


def _provider(name, deadline):
    from . import weather, vigicrues, hydrometry
    if name in ('arome', 'arpege'):
        key = weather.load_arome_api_key() if name == 'arome' else weather._load_api_key(weather.ARPEGE_API_KEY_ENV)
        if not key:
            return {'status': 'not_configured', 'check': 'Clé de ce modèle absente du processus serveur.', 'network_attempted': False}
        endpoint = weather.AROME_WCS_ENDPOINT if name == 'arome' else weather.WCS_ENDPOINT
        data = _read(endpoint.rstrip('/') + '/GetCapabilities',
                     {'service': 'WCS', 'version': '2.0.1', 'language': 'fre'}, {'apikey': key}, deadline, prefix=WcsMetadata())
        scope = 'Métadonnées WCS ServiceIdentification validées ; catalogue, échéances et rasters non parcourus.'
    elif name == 'hubeau':
        data = _read(hydrometry.HUBEAU_SITES_URL, {'size': '1', 'format': 'json'}, {'Accept': 'application/json'}, deadline)
        p = json.loads(data)
        rows = p.get('data') if isinstance(p, dict) else None
        if (not isinstance(rows, list) or len(rows) != 1 or not isinstance(p.get('count'), int)
                or isinstance(p['count'], bool) or p['count'] < 1 or not isinstance(rows[0], dict)
                or not isinstance(rows[0].get('code_site'), str) or not rows[0]['code_site']):
            raise ProbeFailure('format')
        scope = 'Référentiel Hub’Eau : une seule ligne valide, sans observations ni pagination.'
    elif name == 'vigicrues':
        data = _read(vigicrues.VIGICRUES_FORECASTS_URL, {'GrdSimul': 'H', 'FormatDate': 'iso'}, {'Accept': 'application/json'}, deadline)
        p = json.loads(data)
        if (not isinstance(p, dict) or p.get('GrdSimul') != 'H' or not isinstance(p.get('ListEntVigiCru'), list)
                or not all(isinstance(row, dict) and isinstance(row.get('CdEntVigiCru'), str)
                           and row.get('TypEntVigiCru') in (7, '7') for row in p['ListEntVigiCru'])):
            raise ProbeFailure('format')
        vigicrues.parse_forecast_catalog(p)
        scope = 'Catalogue de prévisions Vigicrues H validé ; aucune série de station téléchargée.'
    elif name == 'topage':
        data = _read(hydrometry.TOPAGE_WFS_URL, {'service': 'WFS', 'version': '2.0.0', 'request': 'GetFeature',
                     'resultType': 'hits', 'typeNames': hydrometry.TOPAGE_TYPE_NAME,
                     'bbox': '48.8500,2.3500,48.8501,2.3501,urn:ogc:def:crs:EPSG::4326'}, {'Accept': 'application/xml'}, deadline)
        p = ET.fromstring(_xml_text(data))
        if (p.tag != '{http://www.opengis.net/wfs/2.0}FeatureCollection' or
                not p.get('numberMatched', '').isdigit() or p.get('numberReturned') != '0'):
            raise ProbeFailure('format')
        scope = 'WFS TOPAGE : décompte hits sur une emprise technique minuscule, aucune géométrie téléchargée.'
    else:
        raise ProbeFailure('configuration')
    return {'status': 'ok', 'check': scope, 'response_bytes': len(data), 'network_attempted': True}


def _https(name, deadline):
    import os
    value = os.getenv("GEMAPI_PUBLIC_ORIGIN")
    if not value:
        return {'status': 'not_configured', 'check': 'Origine HTTPS non configurée.', 'network_attempted': False}
    origin = urlsplit(value)
    if (origin.scheme != "https" or not origin.hostname or origin.username or origin.password
            or origin.path not in {"", "/"} or origin.query or origin.fragment):
        raise ProbeFailure('configuration')
    context = ssl.create_default_context()
    with socket.create_connection((origin.hostname, origin.port or 443), timeout=IO_TIMEOUT) as connection:
        with context.wrap_socket(connection, server_hostname=origin.hostname) as secure:
            cert = secure.getpeercert()
            expires = datetime.fromtimestamp(ssl.cert_time_to_seconds(cert['notAfter']), timezone.utc)
            now = datetime.now(timezone.utc)
            if expires <= now:
                raise ProbeFailure('certificate_expired')
            if time.monotonic() >= deadline:
                raise ProbeFailure('timeout')
    return {'status': 'ok', 'check': 'Handshake TLS, nom, chaîne de confiance et validité du certificat vérifiés ; aucun accès aux clés Caddy.',
            'certificate_expires_at': expires.isoformat(), 'certificate_remaining_days': int((expires - now).total_seconds() // 86400),
            'network_attempted': True}


def probe(name):
    start = time.monotonic()
    try:
        value = _https(name, start + PROBE_SECONDS) if name.startswith('https_') else _provider(name, start + PROBE_SECONDS)
    except Exception as error:
        if isinstance(error, ProbeFailure):
            code = error.code
        elif isinstance(error, ssl.SSLCertVerificationError):
            code = 'certificate_expired' if getattr(error, 'verify_code', None) == 10 else 'certificate_invalid'
        elif isinstance(error, requests.exceptions.SSLError):
            code = 'tls'
        elif isinstance(error, (requests.Timeout, TimeoutError, TransportTimeout)):
            code = 'timeout'
        elif isinstance(error, (ValueError, ET.ParseError, KeyError, TypeError)):
            code = 'format'
        else:
            code = 'transport'
        # Never return or log exception text, body, request URL, headers or secrets.
        value = {'status': 'error', 'check': REASONS[code], 'code': code, 'network_attempted': code != 'configuration'}
        status = getattr(error, 'http_status', None)
        if isinstance(status, int) and 100 <= status <= 599:
            value['http_status'] = status
    return {**value, 'active_probe': True, 'checked_at': datetime.now(timezone.utc).isoformat(),
            'latency_ms': round((time.monotonic() - start) * 1000) if value['network_attempted'] else None,
            'result_cache_seconds': CACHE_SECONDS}
