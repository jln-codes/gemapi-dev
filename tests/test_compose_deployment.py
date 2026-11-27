"""Contrat réseau et persistance du déploiement Docker Compose."""

from pathlib import Path
import json
import os
import re
import shutil
import subprocess
from tempfile import TemporaryDirectory
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = (ROOT / "docker-compose.yml").read_text()
ENV_EXAMPLE = (ROOT / ".env.example").read_text()


def yaml_block(source: str, name: str, indent: int) -> str:
    """Isole un bloc de la configuration versionnée pour les contrôles statiques."""
    lines = source.splitlines()
    marker = " " * indent + name + ":"
    start = next(i for i, line in enumerate(lines) if line == marker) + 1
    end = start
    while end < len(lines):
        line = lines[end]
        if line.strip() and not line.lstrip().startswith("#"):
            current_indent = len(line) - len(line.lstrip())
            if current_indent <= indent:
                break
        end += 1
    return "\n".join(lines[start:end])


class ComposeDeploymentTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.services = yaml_block(COMPOSE, "services", 0)
        cls.db = yaml_block(cls.services, "db", 2)
        cls.init = yaml_block(cls.services, "init", 2)
        cls.app = yaml_block(cls.services, "app", 2)
        cls.caddy = yaml_block(cls.services, "caddy", 2)
        cls.worker = yaml_block(cls.services, "worker", 2)

    def test_only_caddy_publishes_application_ports(self):
        self.assertEqual(
            set(re.findall(r"^  ([a-z-]+):$", self.services, re.MULTILINE)),
            {"db", "init", "app", "worker", "import-worker", "basemap", "caddy"},
        )
        self.assertNotRegex(self.db, r"(?m)^    ports:")
        self.assertNotRegex(self.app, r"(?m)^    ports:")
        self.assertNotRegex(self.worker, r"(?m)^    ports:")
        ports = yaml_block(self.caddy, "ports", 4)
        self.assertEqual(
            set(re.findall(r'^      - "([^"]+)"$', ports, re.MULTILINE)),
            {"80:80/tcp", "443:443/tcp", "443:443/udp"},
        )

    def test_caddy_uses_private_app_and_persistent_volumes(self):
        self.assertIn("image: caddy:2-alpine", self.caddy)
        for service in (self.db, self.app, self.caddy):
            self.assertNotRegex(service, r"(?m)^    networks:")
        mounts = yaml_block(self.caddy, "volumes", 4)
        for mount in (
            "./docker/caddy/Caddyfile:/etc/caddy/Caddyfile:ro",
            "caddy-data:/data",
            "caddy-config:/config",
        ):
            self.assertIn(mount, mounts)
        volumes = yaml_block(COMPOSE, "volumes", 0)
        for name in ("digues-db", "digues-media", "caddy-data", "caddy-config", "runtime-config",
                     "provisioning-state", "business-profiles", "basemap-cache"):
            self.assertRegex(volumes, rf"(?m)^  {name}:$")

        caddyfile = (ROOT / "docker/caddy/Caddyfile").read_text()
        self.assertIn("reverse_proxy app:8000", caddyfile)
        self.assertIn("{$APP_DOMAIN:digues-app.fr}", caddyfile)
        self.assertNotIn("www.digues-app.fr", caddyfile)
        self.assertIn("APP_DOMAIN=digues-app.fr", (ROOT / ".env.example").read_text())

    def test_database_initialization_and_health_dependency_remain(self):
        self.assertIn('user: "10001:10001"', COMPOSE.split('services:', 1)[0])
        self.assertIn('chmod -R a+rX /app', (ROOT / 'Dockerfile').read_text())
        self.assertNotIn('ENV PYTHONPATH=', (ROOT / 'Dockerfile').read_text())
        self.assertNotIn('--app-dir', self.app)
        self.assertIn("healthcheck:", self.db)
        self.assertRegex(self.init, r"(?s)db:\n\s+condition: service_healthy")
        self.assertRegex(self.app, r"(?s)db:\n\s+condition: service_healthy")
        self.assertRegex(self.app, r"(?s)init:\n\s+condition: service_completed_successfully")
        self.assertIn('FORWARDED_ALLOW_IPS: "*"', self.app)
        self.assertIn('digues_app.deployment.bootstrap', self.init)
        self.assertNotIn('--with-demo', self.init)
        self.assertIn('digues-media:/var/lib/digues/media', self.init)
        self.assertIn('BUSINESS_CONNECTION_PROFILES_FILE: /var/lib/digues/profiles/profiles.json', self.init)
        self.assertIn('digues_app.provisioning.service', self.worker)
        self.assertIn('LEGACY_SINGLE_DATABASE_ENABLED: "false"', self.app)
        self.assertIn('runtime-config:/var/lib/digues/runtime-config:ro', self.app)
        self.assertIn('business-profiles:/var/lib/digues/profiles:ro', self.app)
        self.assertNotIn('provisioning-state:', self.app)
        self.assertNotIn('POSTGRES_PASSWORD', self.app)
        self.assertNotIn('BOOTSTRAP_POSTGRES_PASSWORD', self.app)
        self.assertNotIn('PROVISIONING_CONFIG_FILE', self.app)
        self.assertNotIn('DATABASE_READONLY_URL', self.app)
        self.assertNotIn('SIRS_POSTGRE_', self.app)
        self.assertNotIn('SIRS_WEB_SHOW_UUID', self.app)
        self.assertIn('DIGUES_WEB_SHOW_UUID:', self.app)
        self.assertIn('scram-sha-256', self.db)

    def test_approved_sirs_origin_is_configured_for_web_and_import_worker(self):
        approved_origin = "https://couchdb.sirs-digues.info"
        setting = f"SIRS_COUCHDB_ALLOWED_ORIGINS={approved_origin}"
        self.assertIn(setting, ENV_EXAMPLE)
        import_worker = yaml_block(self.services, "import-worker", 2)
        for service in (self.app, import_worker):
            self.assertIn(
                "SIRS_COUCHDB_ALLOWED_ORIGINS: "
                "${SIRS_COUCHDB_ALLOWED_ORIGINS:-}",
                service,
            )

    def test_runtime_image_contains_public_help_documents_only(self):
        dockerfile = (ROOT / 'Dockerfile').read_text()
        runtime = dockerfile.split('FROM python:3.12-slim-trixie AS runtime', 1)[1]
        self.assertIn('COPY README.md ./README.md', runtime)
        # Aucun Markdown public n'existe aujourd'hui à la racine de docs/.
        # Docker échouerait sur ce glob vide ; README reste l'aide servie.
        self.assertFalse(list((ROOT / 'docs').glob('*.md')))
        self.assertNotIn('COPY docs/*.md ./docs/', runtime)
        self.assertNotIn('COPY private', dockerfile)
        self.assertNotIn('COPY digues_app', runtime)
        self.assertNotIn('COPY webapp/backend', runtime)
        self.assertIn('ENV DIGUES_HELP_ROOT=/app', runtime)
        self.assertIn('private/', (ROOT / '.dockerignore').read_text())
        self.assertNotIn('COPY docs ./docs', runtime)

    def test_admin_domain_proxies_same_backend_with_separate_secure_origin(self):
        caddyfile = (ROOT / "docker/caddy/Caddyfile").read_text()
        admin = caddyfile.split("{$ADMIN_DOMAIN:admin.digues-app.fr}", 1)[1]
        self.assertIn("reverse_proxy app:8000", admin)
        self.assertIn('Strict-Transport-Security "max-age=31536000"', admin)
        self.assertIn('Cache-Control "no-store"', admin)
        self.assertNotIn("respond", admin)
        self.assertIn("ADMIN_DOMAIN:", self.caddy)
        self.assertIn("ADMIN_ORIGIN:", self.app)
        self.assertIn('ADMIN_ALLOW_INSECURE_LOCALHOST: "false"', self.app)
        self.assertIn('ADMIN_ORIGIN: https://${ADMIN_DOMAIN:', self.app)

    def test_local_rc_uses_product_domain_for_business_origin(self):
        rc_compose = (ROOT / "docker-compose.rc.yml").read_text()
        rc_caddy = (ROOT / "docker/caddy/Caddyfile.rc").read_text()
        self.assertIn("AUTH_ORIGIN: https://digues-app.localhost:8443", rc_compose)
        self.assertIn("ADMIN_ORIGIN: https://admin.localhost:8443", rc_compose)
        self.assertIn("https://digues-app.localhost {", rc_caddy)
        self.assertIn("https://admin.localhost {", rc_caddy)
        self.assertNotIn("business.localhost", rc_compose + rc_caddy)


class ComposeConfigTests(unittest.TestCase):
    def test_public_demo_can_be_disabled_without_distributing_its_configuration(self):
        # Real Compose interpolation: empty must disable, not select the default.
        if shutil.which('docker') is None or subprocess.run(
                ['docker', 'compose', 'version'], capture_output=True).returncode:
            self.skipTest('Docker Compose absent')
        for configured, expected in [(None, '/var/lib/digues/runtime-config/public-demo.json'), ('', '')]:
            environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                           'POSTGRES_PASSWORD': 'synthetic-no-connection',
                           'APP_DOMAIN': 'business.test', 'ADMIN_DOMAIN': 'admin.test'}
            if configured is not None:
                environment['PUBLIC_DEMO_CONFIG_FILE'] = configured
            result = subprocess.run(['docker', 'compose', '--env-file', '/dev/null',
                                     'config', '--format', 'json'], cwd=ROOT,
                                    env=environment, capture_output=True, text=True, check=True)
            services = json.loads(result.stdout)['services']
            self.assertEqual(services['app']['environment']['PUBLIC_DEMO_CONFIG_FILE'], expected)
            for name, service in services.items():
                if name != 'app':
                    self.assertNotIn('PUBLIC_DEMO_CONFIG_FILE', service.get('environment', {}))

    def test_server_provider_injection_is_limited_to_app_at_runtime(self):
        """A (~1s): real Compose, synthetic operator/candidate files, no daemon.

        Protect provider and bootstrap isolation before a server delivery.
        Unrelated SSH/admin canaries in the operator file must never enter a
        service. The provider fragment must preserve the existing instance.
        """
        if shutil.which('docker') is None or subprocess.run(
                ['docker', 'compose', 'version'], capture_output=True).returncode:
            self.skipTest('Docker Compose absent')
        providers = ('MISTRAL_API_KEY', 'ARPEGE_API_KEY', 'AROME_API_KEY',
                     'LEGIFRANCE_CLIENT_ID', 'LEGIFRANCE_CLIENT_SECRET')
        with TemporaryDirectory(dir=ROOT/'audits') as directory:
            folder = Path(directory)
            operator = folder/'.env'
            candidate = folder/'.env.providers.candidate'
            operator.write_text(
                'POSTGRES_PASSWORD=synthetic-bootstrap-password\n'
                'APP_DOMAIN=business.test\nADMIN_DOMAIN=admin.test\n'
                'SSH_PASSWORD=synthetic-forbidden-ssh\n'
                'ADMIN_PASSWORD=synthetic-forbidden-admin\n'
                'DIGUES_IMAGE=digues-app:existing\n'
                'STORAGE_HOST_SNAPSHOT_DIR=/var/lib/digues-supervision\n'
                'BASEMAP_ADMIN_TOKEN=synthetic-internal-token\n'
                'ARPEGE_API_KEY=synthetic-preserved-key\n')
            # Deliberately omit ARPEGE: an unapproved replacement must not
            # erase a pre-existing server credential.
            approved = {key: 'synthetic-approved-'+key for key in providers
                        if key != 'ARPEGE_API_KEY'}
            candidate.write_text('LEGIFRANCE_ENV=sandbox\n' + ''.join(
                f'{key}={value}\n' for key, value in approved.items()))
            for path in (operator, candidate):
                path.chmod(0o600)
            result = subprocess.run(
                ['docker', 'compose', '--env-file', str(operator), '--env-file', str(candidate),
                 '-f', str(ROOT/'docker-compose.yml'), '-f', str(ROOT/'docker-compose.storage.yml'),
                 'config', '--format', 'json'], cwd=folder,
                env={'PATH': os.environ.get('PATH', '/usr/bin:/bin')},
                capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            services = json.loads(result.stdout)['services']
            for name, service in services.items():
                environment = service.get('environment', {})
                self.assertNotIn('SSH_PASSWORD', environment)
                self.assertNotIn('ADMIN_PASSWORD', environment)
                self.assertNotIn('env_file', service)
                self.assertTrue(set(providers).isdisjoint(service.get('build', {}).get('args', {})))
                if name == 'app':
                    self.assertEqual(environment['ARPEGE_API_KEY'], 'synthetic-preserved-key')
                    for key, value in approved.items():
                        self.assertEqual(environment[key], value)
                    self.assertEqual(environment['LEGIFRANCE_ENV'], 'sandbox')
                    self.assertEqual(service['image'], 'digues-app:existing')
                    self.assertTrue(any(mount['target'] == '/var/lib/digues/host-storage'
                        and mount['source'] == '/var/lib/digues-supervision'
                        and mount.get('read_only') is True
                        for mount in service.get('volumes', [])))
                else:
                    self.assertTrue(set(providers).isdisjoint(environment))
                if name not in ('db', 'init'):
                    self.assertNotIn('POSTGRES_PASSWORD', environment)
                    self.assertNotIn('BOOTSTRAP_POSTGRES_PASSWORD', environment)
                if name in ('app', 'basemap'):
                    self.assertNotIn('CARTOGRAPHIC_TERRITORIAL_ENABLED', environment)
                    self.assertEqual(environment['BASEMAP_ADMIN_TOKEN'], 'synthetic-internal-token')
                else:
                    self.assertNotIn('BASEMAP_ADMIN_TOKEN', environment)

    def test_deployment_needs_no_global_couchdb_credentials_or_development_file(self):
        """A (~1s): actual Compose resolution, synthetic files, no daemon.

        A sibling development file and exported canaries must be ignored by
        every service. The same template must resolve when that file is absent.
        """
        if shutil.which('docker') is None or subprocess.run(
                ['docker', 'compose', 'version'], capture_output=True).returncode:
            self.skipTest('Docker Compose absent')
        credentials = {'SIRS_PROFILE': 'local',
                       'SIRS_LOCAL_COUCHDB_URL': 'http://127.0.0.1:5984',
                       'SIRS_LOCAL_DATABASE': 'dev-only',
                       'SIRS_LOCAL_USERNAME': 'dev-only-user',
                       'SIRS_LOCAL_PASSWORD': 'dev-only-password',
                       'SIRS_LOCAL_READONLY_USERNAME': 'dev-reader',
                       'SIRS_LOCAL_READONLY_PASSWORD': 'dev-read-password',
                       'SIRS_READONLY_USERNAME': 'dev-shared-reader',
                       'SIRS_READONLY_PASSWORD': 'dev-shared-password',
                       'SIRS_SECURE_PASSWORD': 'dev-only-remote-password'}
        def development_setting(name):
            return name=='SIRS_PROFILE' or name.startswith(('SIRS_LOCAL_', 'SIRS_SECURE_', 'SIRS_READONLY_'))
        declared = set(re.findall(r'^([A-Z][A-Z0-9_]*)=', ENV_EXAMPLE, re.MULTILINE))
        self.assertFalse(any(development_setting(name) for name in declared))
        self.assertIn('SIRS_COUCHDB_ALLOWED_ORIGINS', declared)
        self.assertIn('SIRS_COUCHDB_ALLOWED_CIDRS', declared)
        self.assertIn('.env.*', (ROOT/'.dockerignore').read_text().splitlines())
        with TemporaryDirectory(dir=ROOT/'audits') as directory:
            folder = Path(directory)
            config = folder/'.env'
            config.write_text(ENV_EXAMPLE.replace('POSTGRES_PASSWORD=\n',
                'POSTGRES_PASSWORD=synthetic-bootstrap-password\n'))
            development = folder/'.env.couchdb.dev'
            for present in (True, False):
                if present:
                    development.write_text(''.join(f'{k}={v}\n' for k,v in credentials.items()))
                    development.chmod(0o600)
                else:
                    development.unlink()
                for files in (('docker-compose.yml',), ('docker-compose.yml', 'docker-compose.rc.yml')):
                    command = ['docker', 'compose', '--env-file', str(config), '-p', 'config03-isolation']
                    for name in files:
                        command.extend(['-f', str(ROOT/name)])
                    result = subprocess.run(command+['config', '--format', 'json'], cwd=folder,
                        env={'PATH': os.environ.get('PATH', '/usr/bin:/bin'), **credentials},
                        capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    services = json.loads(result.stdout)['services']
                    self.assertIn('import-worker', services)
                    for service in services.values():
                        self.assertFalse(any(development_setting(name) for name in service.get('environment', {})))
                        for mount in service.get('volumes', []):
                            self.assertNotEqual(Path(mount['source']).name, development.name)
                    for name in ('app', 'import-worker'):
                        self.assertEqual(services[name]['environment']['SIRS_COUCHDB_ALLOWED_ORIGINS'],
                                         'https://couchdb.sirs-digues.info')

    def test_local_providers_are_explicit_and_couchdb_secrets_stay_out_of_services(self):
        """A: real Compose parser, synthetic env only, no daemon/network (~1s).

        Empty opt-in is the negative case: even configured provider keys must
        stay absent. This checks resolved values rather than YAML spellings.
        """
        if shutil.which("docker") is None:
            self.skipTest("Docker absent de cet environnement")
        version = subprocess.run(["docker", "compose", "version"], capture_output=True)
        if version.returncode:
            self.skipTest("plugin Docker Compose absent")
        providers = ('MISTRAL_API_KEY', 'ARPEGE_API_KEY', 'AROME_API_KEY',
                     'LEGIFRANCE_CLIENT_ID', 'LEGIFRANCE_CLIENT_SECRET')
        for enabled in ('', '1'):
            environment = {'PATH': os.environ.get('PATH', '/usr/bin:/bin'),
                           'POSTGRES_PASSWORD': 'synthetic-not-a-real-password',
                           'APP_DOMAIN': 'business.test', 'ADMIN_DOMAIN': 'admin.test',
                           'DIGUES_LOCAL_INTEGRATIONS': enabled,
                           'SIRS_LOCAL_PASSWORD': 'synthetic-source-secret',
                           'DATABASE_URL': 'postgresql://forbidden.invalid/unused',
                           **{key: 'synthetic-provider-key' for key in providers}}
            result = subprocess.run(
                ['docker', 'compose', '--env-file', '/dev/null', '-f', 'docker-compose.yml',
                 '-f', 'docker-compose.rc.yml', 'config', '--format', 'json'],
                cwd=ROOT, env=environment, capture_output=True, text=True,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            services = json.loads(result.stdout)['services']
            self.assertIn('import-worker', services)
            for key in providers:
                self.assertEqual(services['app']['environment'][key],
                                 'synthetic-provider-key' if enabled else '')
            for service in services.values():
                self.assertNotIn('SIRS_LOCAL_PASSWORD', service.get('environment', {}))
                self.assertNotIn('DATABASE_URL', service.get('environment', {}))
            self.assertNotIn('POSTGRES_PASSWORD', services['app']['environment'])

    def test_compose_config_when_plugin_is_available(self):
        if shutil.which("docker") is None:
            self.skipTest("Docker absent de cet environnement")
        version = subprocess.run(
            ["docker", "compose", "version"], cwd=ROOT, capture_output=True, text=True
        )
        if version.returncode:
            self.skipTest("plugin Docker Compose absent de cet environnement")
        result = subprocess.run(
            ["docker", "compose", "--env-file", "/dev/null", "config", "--quiet"],
            cwd=ROOT,
            env={"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
                 "POSTGRES_PASSWORD": "synthetic-not-used-for-any-connection",
                 "APP_DOMAIN": "business.test", "ADMIN_DOMAIN": "admin.test"},
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
