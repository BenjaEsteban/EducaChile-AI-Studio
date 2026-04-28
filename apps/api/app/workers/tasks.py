import logging
import time
import uuid

from app.workers.base_task import JobTask
from app.workers.celery_app import celery_app

logger = logging.getLogger(__name__)


# ── ping ──────────────────────────────────────────────────────────────────────

@celery_app.task(name="app.workers.tasks.ping", bind=True)
def ping(self, message: str = "pong") -> dict:
    """Task de prueba — verifica que el worker está vivo y puede procesar tasks."""
    logger.info("ping recibido: %s", message)
    return {"message": message, "worker": self.request.hostname}


# ── parse_presentation ────────────────────────────────────────────────────────

class ParsePresentationTask(JobTask):
    """Parsea una presentación PPT/PPTX y extrae sus slides.

    Por ahora simula el procesamiento con sleep y actualiza el job a completed.
    La implementación real usará python-pptx para extraer slides, notas e imágenes.
    """

    name = "app.workers.tasks.parse_presentation"

    def run_job(self, job_id: uuid.UUID, presentation_id: str, **kwargs) -> dict:
        logger.info("Iniciando parse de presentación %s", presentation_id)

        # Simular etapas de procesamiento con progreso
        steps = [
            (10.0, "Descargando archivo desde storage..."),
            (30.0, "Abriendo presentación..."),
            (50.0, "Extrayendo slides..."),
            (70.0, "Procesando notas..."),
            (90.0, "Generando thumbnails..."),
            (100.0, "Finalizado"),
        ]

        for progress, step_name in steps:
            logger.info("[%s] %s", presentation_id, step_name)
            self.set_progress(job_id, progress)
            time.sleep(0.5)  # TODO: reemplazar con lógica real de python-pptx

        return {
            "presentation_id": presentation_id,
            "slide_count": 0,       # TODO: contar slides reales
            "parsed": True,
        }


# Registrar la task en Celery
parse_presentation = celery_app.register_task(ParsePresentationTask())


# ── Función helper para encolar jobs ─────────────────────────────────────────

def enqueue_parse_presentation(job_id: uuid.UUID, presentation_id: uuid.UUID) -> str:
    """Encola un job de parse_presentation y retorna el celery_task_id."""
    result = parse_presentation.apply_async(
        kwargs={
            "job_id": str(job_id),
            "presentation_id": str(presentation_id),
        },
        queue="presentations",
    )
    return result.id
