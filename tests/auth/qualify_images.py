"""Socket puis TCP/TLS sur hôtes Docker jetables. Voir docs/GEMAPI-AUTH.md."""
import argparse
import ast
import base64
import hashlib
from contextlib import ExitStack
from http.cookiejar import CookieJar
import json
import os
from pathlib import Path
import re
import secrets
import ssl
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPCookieProcessor, HTTPSHandler, Request, build_opener
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "model"))
from install import install, permissions, transaction

SCHEMA = "Territoire test"
GROUPS = ("milieux_consultation", "milieux_prestations", "milieux_patrimoine")


def docker(*args, data=None, env=None):
    result = subprocess.run(["docker", *map(str, args)], input=data, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True, env=env)
    if result.returncode:
        # Les paramètres de la fixture ne doivent pas être reproduits lors d'un échec.
        raise RuntimeError(f"Docker {args[0]} a échoué ({result.returncode})")
    return result.stdout.strip()


def remove(kind, name):
    args = ["docker", "rm", "-fv", name] if kind == "container" else ["docker", kind, "rm", name]
    subprocess.run(args, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def certificates(folder, hostname, name="sql", kind="DNS"):
    cert = folder / f"{name}.crt"
    key = folder / f"{name}.key"
    subprocess.run(["openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
                    "-subj", f"/CN={hostname}", "-addext", f"subjectAltName={kind}:{hostname}",
                    "-keyout", key, "-out", cert], check=True, stderr=subprocess.DEVNULL)
    return cert


class Browser:
    def __init__(self, origin, ca=None):
        self.origin = origin
        handlers = [HTTPCookieProcessor(CookieJar())]
        if ca:
            handlers.append(HTTPSHandler(context=ssl.create_default_context(cafile=ca)))
        self.client = build_opener(*handlers)
        self.context = None

    def request(self, path, body=None, status=200, method=None):
        headers = {"Origin": self.origin, "X-Gemapi-CSRF": "1", "Content-Type": "application/json"}
        if self.context:
            headers["X-Gemapi-Context"] = self.context
        req = Request(self.origin + path, None if body is None else json.dumps(body).encode(), headers, method=method)
        try:
            response = self.client.open(req, timeout=35)
        except HTTPError as exc:
            response = exc
        with response:
            assert response.status == status, (path, status, response.status)
            return json.load(response)

    def login(self, user, password, status=200):
        result = self.request("/api/auth/login", {"login":user, "password":password}, status)
        if status == 200:
            self.context = result["context"]
            assert result["login"] == user and password not in str(result)
        return result

    def logout(self):
        self.request("/api/auth/logout", {})


def wait_for(test, timeout=60):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if test():
            return
        time.sleep(.3)
    raise TimeoutError("Échéance de recette dépassée")


class Fixture:
    def __init__(self, stack, folder, image, renderer, hostname="sql", web_tls=False):
        self.hostname = hostname
        prefix = "gemapi-06b-" + uuid4().hex[:10]
        self.net = prefix + "-sql"
        self.front = prefix + "-http"
        self.socket = prefix + "-socket"
        self.db, self.app, self.proxy = (prefix + suffix for suffix in ("-db","-app","-proxy"))
        self.stack, self.folder, self.image = stack, folder, image
        self.password = secrets.token_urlsafe(30)
        self.demo_password = secrets.token_urlsafe(30)
        self.admin_password = secrets.token_urlsafe(30)
        self.cert = certificates(folder, hostname)
        tiles = prefix + "-tiles"
        for network in (self.net, self.front, tiles):
            options = [] if network == self.front else ["--internal"]
            docker("network", "create", *options, network)
            stack.callback(remove, "network", network)
        docker("volume", "create", self.socket)
        stack.callback(remove, "volume", self.socket)
        self.start_db()
        renderer_name = prefix + "-renderer"
        catalog = ROOT / "webapp/backend/digues_webapp"
        self.start(renderer_name, "--network", tiles, "--network-alias", "basemap",
                   "-e", "BASEMAP_CATALOG_FILE=/fixture/basemaps.toml",
                   "-e", "CARTOGRAPHIC_CACHE_PRODUCTS_FILE=/fixture/cartographic_caches.json",
                   "-v", f"{catalog}/basemaps.toml:/fixture/basemaps.toml:ro",
                   "-v", f"{catalog}/cartographic_caches.json:/fixture/cartographic_caches.json:ro", renderer)
        caddyfile = ROOT / "docker/caddy/Caddyfile"
        port = 8443 if web_tls else 8080
        mounts = []
        self.web_cert = None
        if web_tls:
            self.web_cert = certificates(folder, "127.0.0.1", name="web", kind="IP")
            config = caddyfile.read_text().replace(":8080 {", "https://127.0.0.1:8443 {\n\ttls /tls/web.crt /tls/web.key")
            caddyfile = folder / "Caddyfile"
            caddyfile.write_text("{\n default_sni 127.0.0.1\n}\n" + config)
            mounts = ["-v", f"{self.web_cert}:/tls/web.crt:ro", "-v", f"{folder}/web.key:/tls/web.key:ro"]
        self.start(self.proxy, "--network", self.front, "-p", f"127.0.0.1::{port}",
                   "-v", f"{caddyfile}:/etc/caddy/Caddyfile:ro", *mounts, "caddy:2-alpine")
        docker("network", "connect", tiles, self.proxy)
        for label, container in (("PostgreSQL", self.db), ("Renderer", renderer_name), ("Proxy", self.proxy)):
            print(label, docker("inspect", "--format", "{{.Image}}", container), flush=True)
        scheme = "https" if web_tls else "http"
        self.origin = f"{scheme}://127.0.0.1:" + docker("port", self.proxy, f"{port}/tcp").split(":")[-1]

    def browser(self):
        return Browser(self.origin, self.web_cert)

    def clean_logs(self):
        for container in (self.app, self.proxy):
            result = subprocess.run(["docker", "logs", container], capture_output=True, check=True)
            for secret in (self.password, self.demo_password, self.admin_password):
                assert secret.encode() not in result.stdout + result.stderr

    def chromium(self, *specs):
        env = dict(os.environ, GEMAPI_E2E_URL=self.origin, GEMAPI_TEST_PASSWORD=self.password,
                   GEMAPI_TEST_DEMO_PASSWORD=self.demo_password, GEMAPI_TEST_PREFILL="1" if self.prefill else "0")
        if self.web_cert:
            pem = subprocess.check_output(["openssl", "x509", "-in", self.web_cert, "-pubkey", "-noout"])
            der = subprocess.check_output(["openssl", "pkey", "-pubin", "-outform", "DER"], input=pem)
            env["GEMAPI_TEST_TLS_PIN"] = base64.b64encode(hashlib.sha256(der).digest()).decode()
            env["NODE_EXTRA_CA_CERTS"] = str(self.web_cert)
        subprocess.run(["npx", "--no-install", "playwright", "test", "--workers=1",
                        "--output=" + str(self.folder / "browser"), *specs], cwd=ROOT, env=env, check=True)


    def start(self, name, *args, env=None):
        self.stack.callback(remove, "container", name)
        docker("run","--pull=never","-d","--name",name,*args,env=env)

    def query(self, sql):
        return docker("exec","-i","-u","postgres",self.db,"psql","-XqAt","-v","ON_ERROR_STOP=1",
                      "-d","postgres",data=sql)

    def start_db(self):
        (self.folder / "hba").write_text(
            "local all postgres peer\nlocal all trust_probe trust\nlocal all all scram-sha-256\n"
            "hostssl all trust_probe all trust\nhostssl all all all scram-sha-256\n")
        bootstrap = """
            set -eu
            install -o postgres -m 600 /fixture/sql.key /tmp/server.key
            install -m 644 /fixture/sql.crt /tmp/server.crt
            install -m 644 /fixture/hba /tmp/hba
            exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tmp/server.crt \
                -c ssl_key_file=/tmp/server.key -c hba_file=/tmp/hba
        """
        self.start(self.db, "--network", self.net, "--network-alias", self.hostname, "--network-alias", "alias-sql", "-e", "POSTGRES_PASSWORD",
                   "-v", f"{self.socket}:/var/run/postgresql", "-v", f"{self.folder}:/fixture:ro",
                   "--entrypoint","sh","postgis/postgis:16-3.4","-c",bootstrap,
                   env=dict(os.environ,POSTGRES_PASSWORD=self.admin_password))
        def ready():
            return subprocess.run(["docker","exec",self.db,"pg_isready","-q","-h","127.0.0.1"],
                                  capture_output=True).returncode == 0
        wait_for(ready,100)
        self.query("CREATE EXTENSION IF NOT EXISTS postgis; CREATE ROLE milieux_consultation; "
                   "CREATE ROLE milieux_prestations IN ROLE milieux_consultation; "
                   "CREATE ROLE milieux_patrimoine IN ROLE milieux_prestations;")
        self.query(transaction(install(SCHEMA)+permissions(SCHEMA,GROUPS)))
        for user, group in zip(("lecteur","editeur","gestion"), GROUPS):
            self.query(f"CREATE ROLE {user} LOGIN PASSWORD '{self.password}' IN ROLE {group}")
        for user, attrs in (("public_demo","IN ROLE milieux_prestations"), ("trust_probe","IN ROLE milieux_consultation"),
                            ("externe",""), ("privilege","CREATEROLE IN ROLE milieux_patrimoine")):
            secret = self.demo_password if user == "public_demo" else self.password
            self.query(f"CREATE ROLE {user} LOGIN PASSWORD '{secret}' {attrs}")

    def start_app(self, tcp, **overrides):
        self.prefill = tcp
        host = self.hostname if tcp else "/sock"
        cfg = dict(AUTH_ORIGIN=self.origin, DATABASE_SCHEMA=SCHEMA, AUTH_DEPLOYMENT="demo" if tcp else "production",
                   AUTH_DEMO_PREFILL="1" if tcp else "0", AUTH_DEMO_LOGIN="public_demo",
                   AUTH_DEMO_PASSWORD=self.demo_password, AUTH_GLOBAL_ATTEMPTS_PER_MINUTE="200",
                   AUTH_ATTEMPTS_PER_MINUTE="150",
                   DATABASE_URL=f"host={host} dbname=postgres port=5432 sslrootcert=/tmp/sql.crt", **overrides)
        args = ["--network",self.front,"--network-alias","app","-v",f"{self.cert}:/tmp/sql.crt:ro"]
        if not tcp:
            args.extend(["-v", f"{self.socket}:/sock:ro"])
        for key in cfg:
            args.extend(["-e",key])
        # Connect the SQL network before startup so the one-time demo preflight can use it.
        self.stack.callback(remove, "container", self.app)
        docker("create","--pull=never","--name",self.app,*args,self.image,env=dict(os.environ, **cfg))
        if tcp:
            docker("network","connect",self.net,self.app)
        docker("start",self.app)
        self.wait_http()
        tilejson = self.browser().request("/basemap/ign-plan.json")
        assert tilejson["tilejson"] and tilejson["tiles"]
        command = json.loads(docker("inspect", "--format", "{{json .Config.Cmd}}", self.app))
        assert command[command.index("--workers") + 1] == "1"
        assert command[command.index("--lifespan") + 1] == "on"
        print((f"TCP/TLS {host}" if tcp else "socket"),docker("inspect","--format","{{.Image}}",self.app),flush=True)

    def wait_http(self):
        def ready():
            try:
                return self.browser().request("/api/health") == {"status":"ok"}
            except (URLError, AssertionError, OSError):
                return False
        wait_for(ready)

    def count(self):
        return int(self.query("SELECT count(*) FROM pg_stat_activity WHERE application_name='gemapi-web'"))

    def profiles(self,tcp):
        users = ["lecteur","editeur","gestion"]
        clients = [self.browser() for _ in users]
        demo = clients[0].request("/api/auth/demo", {})
        assert demo == (dict(login="public_demo", password=self.demo_password) if tcp else {})
        for client, user in zip(clients, users):
            client.login(user,self.password)
            assert client.request("/api/auth/me")["login"] == user
        observed = self.query("""
            SELECT usename || ':' || state || ':' || s.ssl::text
            FROM pg_stat_activity a JOIN pg_stat_ssl s USING(pid)
            WHERE application_name='gemapi-web' ORDER BY usename
        """)
        assert observed.splitlines() == [f"{name}:idle:{str(tcp).lower()}" for name in sorted(users)], observed
        for user, password, status in [("lecteur","invalid",401),("externe",self.password,403),
                                       ("privilege",self.password,403),("trust_probe",self.password,503)]:
            self.browser().login(user,password,status)
        for client in clients:
            client.logout()
        wait_for(lambda: self.count()==0)
        print("Trois identités, état idle, transport, refus et restitution des connexions vérifiés.",flush=True)

    def transactions(self):
        source = (ROOT / "tests/auth/check_transactions.py").read_text()
        docker("exec", "-i", "-u", "10001", "-e", "GEMAPI_TEST_PASSWORD", self.app, "python", "-", data=source,
               env=dict(os.environ, GEMAPI_TEST_PASSWORD=self.password))
        wait_for(lambda: self.count()==0)
        print("Attribution, rollback, ACL réelles et accès simultanés par connexion vérifiés.", flush=True)

    def live_changes(self):
        client = self.browser()
        client.login("lecteur",self.password)
        module = ast.parse((ROOT / "webapp/backend/digues_webapp/protected_routes.py").read_text())
        routes = next(ast.literal_eval(node.value) for node in module.body if isinstance(node, ast.Assign)
                      and node.targets[0].id == "PROTECTED_ROUTES")
        assert len(routes) == 144
        for method, template in routes:
            path = re.sub(r"\{[^}]+\}", "50bfe115-8e16-4899-bbf4-5917c03e18f9", template)
            client.request(path, method=method, status=403)
        siblings = [self.browser() for _ in range(2)]
        for other in siblings:
            other.login("lecteur", self.password)
        self.browser().login("lecteur", self.password, 503)
        fourth = self.browser()
        fourth.login("editeur", self.password)
        self.browser().login("gestion", self.password, 503)
        assert self.count() == 4
        fourth.logout()
        for other in siblings:
            other.logout()
        replacement = secrets.token_urlsafe(30)
        self.query(f"ALTER ROLE lecteur PASSWORD '{replacement}'")
        assert client.request("/api/auth/me")["login"] == "lecteur"
        self.browser().login("lecteur", self.password, 401)
        self.query(f"ALTER ROLE lecteur PASSWORD '{self.password}'")
        self.query("REVOKE milieux_consultation FROM lecteur")
        client.request("/api/auth/me",status=403)
        self.query("GRANT milieux_consultation TO lecteur")
        client.request("/api/auth/me",status=401)
        self.query(f'GRANT SELECT ON "{SCHEMA}".ah TO lecteur; REVOKE SELECT ON "{SCHEMA}".ah FROM milieux_consultation')
        client.login("lecteur", self.password)
        self.query(f'REVOKE SELECT ON "{SCHEMA}".ah FROM lecteur')
        client.request("/api/auth/me", status=403)
        self.query(f'GRANT SELECT ON "{SCHEMA}".ah TO milieux_consultation')
        for change, undo in (("NOLOGIN","LOGIN"),("VALID UNTIL '2000-01-01'","VALID UNTIL 'infinity'")):
            client.login("lecteur",self.password)
            self.query("ALTER ROLE lecteur " + change)
            client.request("/api/auth/me",status=403)
            self.query("ALTER ROLE lecteur " + undo)
        client.login("lecteur",self.password)
        self.query("SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE application_name='gemapi-web'")
        client.request("/api/auth/me",status=503)
        client.request("/api/auth/me",status=401)
        client.login("lecteur",self.password)
        docker("restart",self.app)
        self.wait_http()
        client.request("/api/auth/me",status=401)
        wait_for(lambda: self.count()==0)
        print("Révocations, terminaison DBA et redémarrage vérifiés.",flush=True)

    def network_loss(self):
        client = self.browser()
        client.login("lecteur",self.password)
        start = time.monotonic()
        docker("network","disconnect",self.net,self.app)
        try:
            client.request("/api/auth/me", status=503)
            client.request("/api/auth/me", status=401)
        finally:
            docker("network","connect",self.net,self.app)
        client.login("lecteur",self.password)
        client.logout()
        wait_for(lambda: self.count()==0,timeout=90)
        print(f"Coupure TCP, session invalidée, reconnexion manuelle et restitution en {time.monotonic()-start:.1f} s.", flush=True)


def run():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image",default="gemapi-init06b-auth:qualification")
    parser.add_argument("--renderer",default="gemapi-init04-renderer:local")
    args = parser.parse_args()
    # Épingler les octets une seule fois, même si un tag local change pendant la recette.
    image = docker("image", "inspect", "--format", "{{.Id}}", args.image)
    renderer = docker("image", "inspect", "--format", "{{.Id}}", args.renderer)
    with tempfile.TemporaryDirectory(prefix="gemapi-auth-") as tmp, ExitStack() as stack:
        fixture = Fixture(stack, Path(tmp), image, renderer)
        fixture.start_app(False, AUTH_IDLE_SECONDS="3")
        fixture.profiles(False)
        client = fixture.browser()
        client.login("lecteur", fixture.password)
        wait_for(lambda: fixture.count()==0,timeout=12)
        client.request("/api/auth/me",status=401)
        fixture.chromium("gemapi-login-production.spec.js")
        fixture.clean_logs()
        remove("container",fixture.app)
        fixture.start_app(True, AUTH_IDLE_SECONDS="25", AUTH_MAX_SESSIONS="4")
        fixture.profiles(True)
        fixture.transactions()
        print("Client", docker("exec", fixture.app, "python", "-c",
              "import psycopg; print(psycopg.__version__, psycopg.pq.version())"), flush=True)
        fixture.live_changes()
        fixture.network_loss()
        fixture.chromium("gemapi-auth.spec.js", "gemapi-shell.spec.js")
        fixture.clean_logs()

    with tempfile.TemporaryDirectory(prefix="gemapi-auth-b-") as tmp, ExitStack() as stack:
        second = Fixture(stack, Path(tmp), image, renderer, hostname="sql-b", web_tls=True)
        second.start_app(True, AUTH_IDLE_SECONDS="25")
        second.profiles(True)
        second.transactions()
        second.network_loss()
        second.chromium("gemapi-auth.spec.js")
        second.clean_logs()


if __name__ == "__main__":
    run()
