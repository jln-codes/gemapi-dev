// Legacy consumer scenarios use synthetic BC events, not a real journal.
async function mockResourceJournal(route) {
  const url = new URL(route.request().url());
  if (!url.pathname.startsWith('/api/resource-events')) return false;
  if (url.pathname.endsWith('/stream')) {
    await route.fulfill({ contentType: 'text/event-stream', body: ': mock journal\n\n' });
  } else {
    await route.fulfill({ json: url.pathname.endsWith('/head') ? { cursor: '0' }
      : { events: [], nextCursor: url.searchParams.get('after') || '0', hasMore: false } });
  }
  return true;
}
module.exports = { mockResourceJournal };
