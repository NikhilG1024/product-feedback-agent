"""Run the configured durable worker as a separate process."""
import signal
import asyncio
from app.main import configured_app


def main():
    app = configured_app()
    worker = app.state.worker
    def stop(signum, frame): worker.stop()
    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)
    async def run():
        async with app.router.lifespan_context(app):
            while not worker.stopped.is_set():
                if not worker.tick(): worker.stopped.wait(1)
    asyncio.run(run())


if __name__ == '__main__': main()
