"""Lambda entry point.

Mangum's lifespan="on" builds a fresh LifespanCycle and runs the ASGI app's full
startup AND shutdown around every single invocation (verified directly in
mangum.adapter.Mangum.__call__ / mangum.protocols.lifespan.LifespanCycle) -- there is
no persistent "server start" moment outside of invocations in Lambda's model, so
Mangum re-triggers it every time. For this app that would mean reloading the CLIP
encoder, recreating the Supabase clients, and rerunning the search warm-up on every
request, warm or not, which defeats container reuse entirely.

So lifespan is driven manually here, once, at module import (i.e. during Lambda's
Init Duration on cold start): backend.app.lifespan's startup runs to its `yield` and
app.state is left populated for the container's lifetime; warm invocations reuse this
same imported module without re-running it. Mangum is then told lifespan="off" so it
never touches the ASGI lifespan protocol itself.
"""

import asyncio

from backend.app import app, lifespan
from mangum import Mangum

asyncio.run(lifespan(app).__aenter__())

handler = Mangum(app, lifespan="off")
