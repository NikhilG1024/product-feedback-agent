"""Run only the durable summary queue in a separate process."""

import asyncio
import signal

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
                except ServiceError:
                    # A capacity or provider configuration failure must not
                    # terminate the process; durable state remains for retry.
                    did_work = False
                if not did_work:
                    worker.stopped.wait(1)
    asyncio.run(run())


if __name__ == "__main__":
    main()
