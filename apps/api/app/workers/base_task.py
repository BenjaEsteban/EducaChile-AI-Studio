import logging
import uuid
from typing import Any

from celery import Task

from app.modules.jobs.repository import JobRepository
from app.workers.db import worker_db_session

logger = logging.getLogger(__name__)


class JobTask(Task):
    """Task base que gestiona el ciclo de vida del Job en DB.

    Las subclases implementan `run_job(job_id, **kwargs) -> dict`
    y esta clase se encarga de:
      - marcar el job como running al inicio
      - actualizar progress durante la ejecución
      - marcar completed con el resultado
      - marcar failed con el error_message si lanza excepción
    """

    abstract = True

    def __call__(self, job_id: str, **kwargs: Any) -> dict:
        _job_id = uuid.UUID(job_id)
        with worker_db_session() as db:
            repo = JobRepository(db)
            job = repo.get_by_id(_job_id)
            if not job:
                logger.error("Job %s no encontrado en DB", job_id)
                return {"error": "job_not_found"}
            repo.mark_running(job, celery_task_id=self.request.id or "")

        try:
            result = self.run_job(job_id=_job_id, **kwargs)
        except Exception as exc:
            with worker_db_session() as db:
                repo = JobRepository(db)
                job = repo.get_by_id(_job_id)
                if job:
                    repo.mark_failed(job, error_message=str(exc))
            logger.exception("Job %s falló: %s", job_id, exc)
            raise

        with worker_db_session() as db:
            repo = JobRepository(db)
            job = repo.get_by_id(_job_id)
            if job:
                repo.mark_completed(job, result=result)

        return result

    def run_job(self, job_id: uuid.UUID, **kwargs: Any) -> dict:
        """Implementar en cada task. Debe retornar un dict con el resultado."""
        raise NotImplementedError

    def set_progress(
        self,
        job_id: uuid.UUID,
        progress: float,
        current_step: str | None = None,
    ) -> None:
        """Llamar desde run_job() para reportar progreso (0-100)."""
        with worker_db_session() as db:
            repo = JobRepository(db)
            job = repo.get_by_id(job_id)
            if job:
                repo.update_progress(job, progress, current_step=current_step)
        meta = {"progress": progress}
        if current_step is not None:
            meta["current_step"] = current_step
        self.update_state(state="PROGRESS", meta=meta)
