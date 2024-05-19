from asyncio import CancelledError, create_task, sleep, Task, TimeoutError, wait_for
from logging import getLogger
from os import getpid, kill
from signal import SIGTERM
from time import time

from callbacks import Callbacks
from settings import settings, max_wait_cancel


logger = getLogger('jmqtt.healthcheck')


class Healthcheck:
    _lastRcv: int = time()  # time of the last rcv msg
    _task: Task = None  # Healthcheck task initialised by daemonUp method

    @classmethod
    async def onReceive(cls):
        cls._lastRcv = time()

    @classmethod
    async def __hcLoop(cls) -> bool:
        now = time()
        # Kill daemon if we cannot send for a total of X seconds
        #  and/or a total of Y retries "Jeedom is no longer available"
        if (
            now - Callbacks._lastSnd > settings.snd_timeout
            and Callbacks._retrySnd > settings.retry_max
        ):
            logger.error(
                "Nothing could be sent for %ds (max %ds) AND after %d attempts (max %d), "
                "Jeedom/Apache is probably dead.",
                now - Callbacks._lastSnd,
                settings.snd_timeout,
                Callbacks._retrySnd,
                settings.retry_max,
            )
            kill(getpid(), SIGTERM)
            return False
        if now - cls._lastRcv > settings.hb_timeout:
            logger.error(
                "Nothing has been received for %ds, Jeedom does not want me any longer.",
                now - cls._lastRcv,
            )
            kill(getpid(), SIGTERM)
            return False
        elif now - cls._lastRcv > settings.hb_timeout - settings.check_interval - 1:
            logger.warning(
                "Nothing received for %ds, Deamon will stop if >%ds.",
                now - cls._lastRcv,
                settings.hb_timeout,
            )

        if now - Callbacks._lastSnd > settings.hb_delay:
            # Avoid sending heartbeats continuously
            if now - Callbacks._lastHb > settings.hb_retry:
                logger.debug(
                    "Heartbeat TO Jeedom (last msg from/to Jeedom %ds/%ds ago)",
                    time() - cls._lastRcv,
                    time() - Callbacks._lastSnd,
                )
                await Callbacks.daemonHB()
        return True

    @classmethod
    async def __healthcheck(cls):
        logger.debug('Healthcheck task started')
        while await cls.__hcLoop():
            # logger.debug('Healthcheck-ed')
            await sleep(settings.check_interval)
        logger.debug('Healthcheck task ended unexpectidely')

    @classmethod
    async def start(cls):
        # Start heart beat task
        cls._task = create_task(cls.__healthcheck())
        logger.debug('Healthcheck task created')

    @classmethod
    async def stop(cls):
        if cls._task is None:
            return
        try:
            # Cancel tasks and join it for `max_wait_cancel` seconds
            cls._task.cancel()
            await wait_for(cls._task, timeout=max_wait_cancel)
        except CancelledError:
            logger.debug('Healthcheck task canceled')
        except TimeoutError:
            logger.debug('Healthcheck task timeouted')