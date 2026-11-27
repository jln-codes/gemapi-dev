"""Application-owned private renderer transport; no user cookies or identity state."""
from contextlib import asynccontextmanager, AsyncExitStack
from http.cookiejar import CookieJar, DefaultCookiePolicy
import os
import httpx


def register_transport(app):
    previous = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application):
        async with AsyncExitStack() as stack:
            client = None
            if len(os.getenv('LIDAR_INTERNAL_TOKEN', '')) >= 32:
                client = await stack.enter_async_context(httpx.AsyncClient(
                    timeout=httpx.Timeout(50, connect=5), trust_env=False, follow_redirects=False,
                    cookies=CookieJar(policy=DefaultCookiePolicy(allowed_domains=()))))
            application.state.lidar_http_client = client
            try:
                async with previous(application) as state:
                    yield state
            finally:
                del application.state.lidar_http_client
    app.router.lifespan_context = lifespan
