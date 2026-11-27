"""B (~1–3 min): real Docker/PostgreSQL cleanup after timeout/failure/signals.

Each scenario owns a new local cluster, containers and advisory lock. No
application database or volume is selected. The blocking case waits for an
observable PostgreSQL lock wait; its short deadline deliberately tests failure.
"""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from tempfile import mkdtemp
import time
import unittest

from tests.runtime_image_harness import (
    ROOT, OWNER_LABEL, ImagePhaseTimeout, ImageTestInterrupted, RuntimeImageEnvironment,
)

IMAGE = os.getenv('DIGUES_TEST_RUNTIME_IMAGE')
BLOCKED = r'''
import json,sys
print('DIGUES_IMAGE_PHASE startup',file=sys.stderr,flush=True)
import psycopg
p=json.load(sys.stdin)
with psycopg.connect(p['dsn'],autocommit=True) as connection:
    connection.execute('SELECT 1')
    print('DIGUES_IMAGE_PHASE blocked',file=sys.stderr,flush=True)
    connection.execute('SELECT pg_advisory_lock(%s)',(p['lock_key'],))
'''


def lock_ready(environment, name):
    deadline = time.monotonic()+5
    with environment.admin() as connection:
        while not connection.execute(
            "SELECT 1 FROM pg_stat_activity WHERE datname=%s AND application_name=%s "
            "AND wait_event_type='Lock' AND wait_event='advisory'",
            (environment.database, name)).fetchone():
            if time.monotonic() >= deadline:
                raise AssertionError('Controlled container never reached its PostgreSQL lock wait')
            time.sleep(.02)


def publish(path, value):
    temporary = path.with_suffix('.writing')
    temporary.write_text(json.dumps(value, indent=2)+'\n')
    temporary.replace(path)


def interruption_probe(folder):
    environment = RuntimeImageEnvironment(IMAGE, artifact_root=folder)
    try:
        with environment:
            with environment.connect() as blocker:
                blocker.execute('SELECT pg_advisory_lock(94632213)')

                def ready(name, phase):
                    if phase == 'blocked':
                        lock_ready(environment, name)
                        publish(folder/'ready.json', {'name': name, 'owner': environment.owner,
                            'data': str(environment.cluster.data),
                            'socket': str(environment.cluster.socket_dir)})

                result = environment.docker(script=BLOCKED, payload=json.dumps({'lock_key': 94632213}),
                    phases=('startup', 'blocked'), on_phase=ready)
                if result.returncode:
                    raise AssertionError(result.stderr)
        return 0  # Unexpected in this probe: the parent must interrupt the owned process.
    except ImageTestInterrupted as error:
        publish(folder/'interrupted.json', {'signal': error.signum, 'closed': environment.closed,
            'evidence': str(environment.evidence)})
        return 128+error.signum


@unittest.skipUnless(IMAGE, 'explicit local production image required')
class RuntimeImageLifecycleTest(unittest.TestCase):
    def assert_closed(self, environment):
        self.assertTrue(environment.closed)
        self.assertFalse(environment.cluster.root.exists())
        self.assertFalse(environment.cluster.socket_dir.exists())
        self.assertEqual(environment.events[-1]['event'], 'closed')
        self.assertEqual(environment.events[-1]['errors'], [])
        for name in environment.containers:
            self.assertIsNone(environment.inspect_container(name))

    def test_timeout_removes_container_and_its_blocked_backend_before_drop(self):
        environment = RuntimeImageEnvironment(IMAGE)
        observed = []
        with environment:
            with environment.connect() as blocker:
                blocker.execute('SELECT pg_advisory_lock(94632213)')

                def ready(name, phase):
                    if phase == 'blocked':
                        lock_ready(environment, name)
                        observed.append(name)

                with self.assertRaisesRegex(ImagePhaseTimeout, 'blocked'):
                    environment.docker(script=BLOCKED, payload=json.dumps({'lock_key': 94632213}),
                        phases=('startup', 'blocked'), phase_timeout={'blocked': 3}, on_phase=ready)
                self.assertEqual(len(observed), 1)
                self.assertIsNone(environment.inspect_container(observed[0]))
                with environment.admin() as connection:
                    self.assertEqual(connection.execute(
                        'SELECT count(*) FROM pg_stat_activity WHERE application_name=%s',
                        (observed[0],)).fetchone(), (0,))
                self.assertTrue(any(e['event']=='sessions_released' and e['terminated']>0
                                    for e in environment.events))
        self.assert_closed(environment)
        events = [event['event'] for event in environment.events]
        self.assertLess(events.index('container_absent'), events.index('sessions_released'))
        self.assertLess(events.index('sessions_released'), events.index('database_dropped'))
        self.assertLess(events.index('database_dropped'), events.index('cluster_stopped'))

    def test_nonzero_container_and_failed_assertion_still_release_resources(self):
        environment = RuntimeImageEnvironment(IMAGE)
        with self.assertRaisesRegex(AssertionError, 'controlled assertion after container failure'):
            with environment:
                result = environment.docker(script="import os; os._exit(23)")
                self.assertEqual(result.returncode, 23)
                self.assertIsNone(environment.inspect_container(environment.containers[-1]))
                raise AssertionError('controlled assertion after container failure')
        self.assert_closed(environment)

    def test_foreign_container_owner_is_refused_without_removal(self):
        # The counter-fixture is also ours, but deliberately has another owner.
        # The image harness must leave it intact; its creator alone removes it.
        environment = RuntimeImageEnvironment(IMAGE)
        with environment:
            foreign_name = 'digues-image-foreign-'+environment.owner
            foreign_owner = 'counter-fixture-'+environment.owner
            created = environment.command('container', 'create', '--name', foreign_name,
                '--label', OWNER_LABEL+'='+foreign_owner, '--pull', 'never', '--network', 'none',
                '--entrypoint', 'python', environment.image_id, '-c', 'pass')
            self.assertEqual(created.returncode, 0, created.stderr)
            try:
                with self.assertRaisesRegex(RuntimeError, 'foreign ownership'):
                    environment.remove_container(foreign_name)
                inspected = environment.command('container', 'inspect', foreign_name)
                self.assertEqual(inspected.returncode, 0)
                info = json.loads(inspected.stdout)[0]
                self.assertEqual(info['Config']['Labels'][OWNER_LABEL], foreign_owner)
                self.assertFalse(info['State']['Running'])
            finally:
                removed = environment.command('container', 'rm', created.stdout.strip())
                self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assert_closed(environment)

    def test_sigint_and_sigterm_close_the_real_child_fixture(self):
        root = Path(os.getenv('DIGUES_IMAGE_TEST_ARTIFACT_DIR', ROOT/'audits/runtime-image'))
        root.mkdir(parents=True, exist_ok=True)
        for signum in (signal.SIGINT, signal.SIGTERM):
            with self.subTest(signal=signum):
                folder = Path(mkdtemp(prefix='interrupt-', dir=root))
                with (folder/'probe.log').open('w') as log:
                    process = subprocess.Popen([sys.executable, '-B', '-m',
                        'tests.test_runtime_image_harness', '--interruption-probe', str(folder)],
                        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
                    try:
                        deadline = time.monotonic()+120
                        while not (folder/'ready.json').exists():
                            self.assertIsNone(process.poll(), (folder/'probe.log').read_text())
                            if time.monotonic() >= deadline:
                                self.fail('Interrupted fixture never became observably ready')
                            time.sleep(.05)
                        ready = json.loads((folder/'ready.json').read_text())
                        process.send_signal(signum)
                        self.assertEqual(process.wait(timeout=45), 128+signum,
                                         (folder/'probe.log').read_text())
                    finally:
                        if process.poll() is None:
                            process.terminate()
                            process.wait(timeout=45)
                result = json.loads((folder/'interrupted.json').read_text())
                self.assertEqual(result['signal'], signum)
                self.assertTrue(result['closed'])
                self.assertFalse(Path(ready['data']).parent.exists())
                self.assertFalse(Path(ready['socket']).exists())
                check = subprocess.run(['docker', 'container', 'ls', '-aq', '--filter',
                    'label=digues.test.runtime-image-owner='+ready['owner']],
                    capture_output=True, text=True, timeout=30, check=True)
                self.assertFalse(check.stdout.strip())


if __name__ == '__main__':
    if len(sys.argv) == 3 and sys.argv[1] == '--interruption-probe':
        raise SystemExit(interruption_probe(Path(sys.argv[2])))
    unittest.main()
