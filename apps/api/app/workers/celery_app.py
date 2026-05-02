import logging

from celery import Celery
from celery.signals import task_failure, task_postrun, task_prerun, worker_ready

import app.models  # noqa: F401
from app.config import settings

logger = logging.getLogger(__name__)

celery_app = Celery(
    "educachile",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    # Serialización
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # Timezone
    timezone="America/Santiago",
    enable_utc=True,
    # Resultados
    result_expires=3600,          # resultados en Redis expiran en 1h
    result_backend_transport_options={"visibility_timeout": 3600},
    # Reintentos y timeouts
    task_acks_late=True,          # ack solo después de completar (evita pérdida si el worker muere)
    task_reject_on_worker_lost=True,
    task_soft_time_limit=300,     # 5 min → SoftTimeLimitExceeded (puede limpiar)
    task_time_limit=360,          # 6 min → SIGKILL
    # Concurrencia
    worker_prefetch_multiplier=1, # un task a la vez por worker (fair dispatch)
    # Queues
    task_default_queue="default",
    task_queues={
        "default": {"exchange": "default", "routing_key": "default"},
        "presentations": {"exchange": "presentations", "routing_key": "presentations"},
        "generation": {"exchange": "generation", "routing_key": "generation"},
    },
    task_routes={
        "app.workers.tasks.parse_presentation": {"queue": "presentations"},
        "app.workers.tasks.generate_video": {"queue": "generation"},
        "app.workers.tasks.ping": {"queue": "default"},
    },
)


# ── Señales ───────────────────────────────────────────────────────────────────

@worker_ready.connect
def on_worker_ready(sender, **kwargs):
    logger.info("Celery worker listo. Broker: %s", settings.REDIS_URL)


@task_prerun.connect
def on_task_prerun(task_id, task, args, kwargs, **extra):
    logger.info("Task iniciada: %s [%s]", task.name, task_id)


@task_postrun.connect
def on_task_postrun(task_id, task, args, kwargs, retval, state, **extra):
    logger.info("Task finalizada: %s [%s] → %s", task.name, task_id, state)


@task_failure.connect
def on_task_failure(task_id, exception, traceback, sender, **kwargs):
    logger.error("Task fallida: %s [%s] → %s", sender.name, task_id, exception)
