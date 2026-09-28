"""Run only the durable summary queue in a separate process."""

import asyncio
import signal
import logging

from pymongo.errors import PyMongoError

from app.main import configured_app
from app.errors import ServiceError


def main():
    app = configured_app()
    worker = app.state.summary_worker
    signal.signal(signal.SIGINT, lambda *_: worker.stop())
    signal.signal(signal.SIGTERM, lambda *_: worker.stop())

    async def run():
        async with app.router.lifespan_context(app):
            while not worker.stopped.is_set():
                try:
                    did_work = worker.tick()
                except PyMongoError as exc:
                    # Never log exception payloads: database URLs may be sensitive.
                    logging.getLogger(__name__).warning(
                        "Summary worker database interruption (%s); retrying in 5 seconds",
                        type(exc).__name__)
                    worker.stopped.wait(5)
                    continue
                except ServiceError:
                    # A capacity or provider configuration failure must not
                    # terminate the process; durable state remains for retry.
                    did_work = False
                if not did_work:
                    worker.stopped.wait(1)
    asyncio.run(run())


if __name__ == "__main__":
    main()
