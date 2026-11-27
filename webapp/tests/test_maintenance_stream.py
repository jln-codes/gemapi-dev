"""A: streaming faults and disconnect without a network success claim."""
import asyncio
import unittest
from unittest.mock import patch
from digues_webapp.database import BusinessMaintenanceError, WebDatabaseError
from digues_webapp.resource_journal import stream_events

class MaintenanceStreamTest(unittest.TestCase):
    def test_expected_refusals_close_after_headers_without_cursor(self):
        class Request:
            async def is_disconnected(self):return False
        async def collect(error):
            # Inject at the await boundary here. The HTTP suite exercises the
            # actual thread hop; this unit test owns no executor lifecycle.
            async def refused(*args, **kwargs):raise error
            with patch('digues_webapp.resource_journal.asyncio.to_thread',side_effect=refused):
                return [part async for part in stream_events(Request(),9,poll_interval=0)]
        parts=asyncio.run(collect(BusinessMaintenanceError()))
        self.assertEqual(parts[0],': connected\n\n')
        self.assertIn('event: maintenance',parts[1]);self.assertNotIn('id:',parts[1])
        self.assertNotIn('traceback',''.join(parts).lower())
        self.assertEqual(asyncio.run(collect(WebDatabaseError('private DSN'))),[': connected\n\n'])

    def test_disconnected_client_never_opens_a_business_read(self):
        class Request:
            async def is_disconnected(self):return True
        async def collect():
            with patch('digues_webapp.resource_journal.read_page',side_effect=AssertionError('disconnected')):
                return [part async for part in stream_events(Request(),9)]
        self.assertEqual(asyncio.run(collect()),[': connected\n\n'])
