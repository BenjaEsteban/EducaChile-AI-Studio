import uuid
from contextlib import contextmanager
from io import BytesIO
from unittest.mock import MagicMock, patch

from pptx import Presentation as PptxPresentation
from sqlalchemy.orm import configure_mappers

from app.modules.jobs.models import Job, JobStatus, JobType
from app.modules.organizations.models import Organization
from app.modules.projects.models import Presentation, PresentationStatus, Project, Slide
from app.modules.projects.service import MOCK_ORG_ID, MOCK_USER_ID
from app.modules.users.models import User
from app.workers.celery_app import celery_app
from app.workers.tasks import ParsePresentationTask, enqueue_parse_presentation, ping
from tests.conftest import _TestingSession
from tests.fakes import InMemoryStorageProvider

# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_job(job_id: uuid.UUID | None = None) -> Job:
    return Job(
        id=job_id or uuid.uuid4(),
        organization_id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        presentation_id=uuid.uuid4(),
        job_type=JobType.parse_presentation,
        status=JobStatus.queued,
        progress=0.0,
        current_step=None,
        celery_task_id=None,
        error_message=None,
        result=None,
        started_at=None,
        finished_at=None,
    )


def _make_repo(job: Job) -> MagicMock:
    repo = MagicMock()
    repo.get_by_id.return_value = job
    repo.mark_running.return_value = job
    repo.mark_completed.return_value = job
    repo.mark_failed.return_value = job
    repo.update_progress.return_value = job
    return repo


@contextmanager
def _fake_session():
    yield MagicMock()


@contextmanager
def _testing_worker_session():
    db = _TestingSession()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def _make_pptx_bytes() -> bytes:
    deck = PptxPresentation()
    slide = deck.slides.add_slide(deck.slide_layouts[1])
    slide.shapes.title.text = "Clase 1"
    slide.placeholders[1].text = "Objetivo de aprendizaje\nContenido visible"

    second = deck.slides.add_slide(deck.slide_layouts[1])
    second.shapes.title.text = "Clase 2"
    second.placeholders[1].text = "Actividad final"

    buffer = BytesIO()
    deck.save(buffer)
    return buffer.getvalue()


def _create_parse_fixture(storage: InMemoryStorageProvider, pptx_bytes: bytes | None = None):
    storage_key = f"{MOCK_ORG_ID}/{uuid.uuid4()}/deck.pptx"
    storage.upload_file(
        storage_key,
        pptx_bytes if pptx_bytes is not None else _make_pptx_bytes(),
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    )

    db = _TestingSession()
    try:
        user = User(
            id=MOCK_USER_ID,
            email=f"{uuid.uuid4()}@test.local",
            hashed_password="test",
            full_name="Test User",
        )
        org = Organization(id=MOCK_ORG_ID, name="Test Org", slug=f"test-{uuid.uuid4()}")
        project = Project(
            organization_id=MOCK_ORG_ID,
            owner_id=MOCK_USER_ID,
            name="Project",
        )
        db.add_all([user, org, project])
        db.flush()

        presentation = Presentation(
            project_id=project.id,
            organization_id=MOCK_ORG_ID,
            title="deck.pptx",
            original_filename="deck.pptx",
            storage_key=storage_key,
            status=PresentationStatus.uploaded,
        )
        db.add(presentation)
        db.flush()

        job = Job(
            organization_id=MOCK_ORG_ID,
            project_id=project.id,
            presentation_id=presentation.id,
            job_type=JobType.parse_presentation,
            status=JobStatus.queued,
        )
        db.add(job)
        db.commit()
        return job.id, presentation.id
    finally:
        db.close()


# ── ping ──────────────────────────────────────────────────────────────────────

def test_worker_imports_register_all_sqlalchemy_mappers():
    configure_mappers()


def test_ping_returns_message():
    celery_app.conf.task_always_eager = True
    result = ping.apply(kwargs={"message": "hello"})
    assert result.result["message"] == "hello"


def test_ping_default_message():
    celery_app.conf.task_always_eager = True
    result = ping.apply()
    assert result.result["message"] == "pong"


# ── JobTask base — ciclo de vida ──────────────────────────────────────────────

def test_job_task_marks_running_then_completed():
    from app.workers.base_task import JobTask

    class EchoTask(JobTask):
        name = "test.echo"
        def run_job(self, job_id, payload="ok", **kwargs):
            return {"echo": payload}

    job = _make_job()
    repo = _make_repo(job)

    with patch("app.workers.base_task.worker_db_session", _fake_session), \
         patch("app.workers.base_task.JobRepository", return_value=repo):
        task = celery_app.register_task(EchoTask())
        celery_app.conf.task_always_eager = True
        result = task.apply(kwargs={"job_id": str(job.id), "payload": "test"})

    assert result.result == {"echo": "test"}
    repo.mark_running.assert_called_once()
    repo.mark_completed.assert_called_once()
    repo.mark_failed.assert_not_called()


def test_job_task_marks_failed_on_exception():
    from app.workers.base_task import JobTask

    class FailTask(JobTask):
        name = "test.fail"
        def run_job(self, job_id, **kwargs):
            raise ValueError("error simulado")

    job = _make_job()
    repo = _make_repo(job)

    with patch("app.workers.base_task.worker_db_session", _fake_session), \
         patch("app.workers.base_task.JobRepository", return_value=repo):
        task = celery_app.register_task(FailTask())
        celery_app.conf.task_always_eager = True
        result = task.apply(kwargs={"job_id": str(job.id)})

    # En modo eager, las excepciones se capturan en el result
    assert result.failed()
    repo.mark_running.assert_called_once()
    repo.mark_failed.assert_called_once()
    _, kwargs = repo.mark_failed.call_args
    assert "error simulado" in kwargs["error_message"]


def test_job_task_job_not_found_returns_error():
    from app.workers.base_task import JobTask

    class AnyTask(JobTask):
        name = "test.any"
        def run_job(self, job_id, **kwargs):
            return {}

    repo = MagicMock()
    repo.get_by_id.return_value = None

    with patch("app.workers.base_task.worker_db_session", _fake_session), \
         patch("app.workers.base_task.JobRepository", return_value=repo):
        task = celery_app.register_task(AnyTask())
        celery_app.conf.task_always_eager = True
        result = task.apply(kwargs={"job_id": str(uuid.uuid4())})

    assert result.result == {"error": "job_not_found"}
    repo.mark_running.assert_not_called()


def test_job_task_set_progress_updates_db():
    from app.workers.base_task import JobTask

    class ProgressTask(JobTask):
        name = "test.progress"
        def run_job(self, job_id, **kwargs):
            self.set_progress(job_id, 50.0)
            self.set_progress(job_id, 100.0)
            return {"done": True}

    job = _make_job()
    repo = _make_repo(job)

    with patch("app.workers.base_task.worker_db_session", _fake_session), \
         patch("app.workers.base_task.JobRepository", return_value=repo):
        task = celery_app.register_task(ProgressTask())
        celery_app.conf.task_always_eager = True
        task.apply(kwargs={"job_id": str(job.id)})

    assert repo.update_progress.call_count == 2
    calls = repo.update_progress.call_args_list
    assert calls[0].args[1] == 50.0
    assert calls[1].args[1] == 100.0


# ── ParsePresentationTask ─────────────────────────────────────────────────────

def test_parse_presentation_completes_successfully():
    storage = InMemoryStorageProvider()
    job_id, presentation_id = _create_parse_fixture(storage)

    with patch("app.workers.base_task.worker_db_session", _testing_worker_session), \
         patch("app.workers.tasks.worker_db_session", _testing_worker_session), \
         patch("app.workers.tasks.get_storage", return_value=storage):
        celery_app.conf.task_always_eager = True
        result = ParsePresentationTask().apply(kwargs={
            "job_id": str(job_id),
            "presentation_id": str(presentation_id),
        })

    assert not result.failed()
    body = result.result
    assert body["parsed"] is True
    assert body["presentation_id"] == str(presentation_id)
    assert body["slide_count"] == 2

    db = _TestingSession()
    try:
        presentation = db.get(Presentation, presentation_id)
        slides = (
            db.query(Slide)
            .filter(Slide.presentation_id == presentation_id)
            .order_by(Slide.position)
            .all()
        )
        job = db.get(Job, job_id)
        assert presentation.status == PresentationStatus.parsed
        assert presentation.slide_count == 2
        assert job.status == JobStatus.completed
        assert job.progress == 100.0
        assert job.current_step == "Completed"
        assert len(slides) == 2
        assert slides[0].position == 1
        assert slides[0].title == "Clase 1"
        assert "Objetivo de aprendizaje" in slides[0].metadata_["visible_text"]
        assert slides[0].notes is None
        assert slides[0].metadata_["dialogue"] == ""
    finally:
        db.close()


def test_parse_presentation_failure_marks_presentation_failed():
    storage = InMemoryStorageProvider()
    job_id, presentation_id = _create_parse_fixture(storage, pptx_bytes=b"not a pptx")

    with patch("app.workers.base_task.worker_db_session", _testing_worker_session), \
         patch("app.workers.tasks.worker_db_session", _testing_worker_session), \
         patch("app.workers.tasks.get_storage", return_value=storage):
        celery_app.conf.task_always_eager = True
        result = ParsePresentationTask().apply(kwargs={
            "job_id": str(job_id),
            "presentation_id": str(presentation_id),
        })

    assert result.failed()
    db = _TestingSession()
    try:
        presentation = db.get(Presentation, presentation_id)
        job = db.get(Job, job_id)
        assert presentation.status == PresentationStatus.failed
        assert job.status == JobStatus.failed
        assert job.error_message
        assert job.current_step == "Failed"
    finally:
        db.close()


# ── enqueue_parse_presentation ────────────────────────────────────────────────

def test_enqueue_returns_celery_task_id():
    mock_result = MagicMock()
    mock_result.id = "fake-celery-id"

    with patch("app.workers.tasks.parse_presentation.apply_async", return_value=mock_result):
        task_id = enqueue_parse_presentation(uuid.uuid4(), uuid.uuid4())

    assert task_id == "fake-celery-id"


def test_enqueue_uses_presentations_queue():
    mock_result = MagicMock()
    mock_result.id = "x"

    with patch(
        "app.workers.tasks.parse_presentation.apply_async",
        return_value=mock_result,
    ) as mock_apply:
        enqueue_parse_presentation(uuid.uuid4(), uuid.uuid4())

    assert mock_apply.call_args[1]["queue"] == "presentations"


def test_enqueue_passes_job_and_presentation_ids():
    job_id = uuid.uuid4()
    presentation_id = uuid.uuid4()
    mock_result = MagicMock()
    mock_result.id = "x"

    with patch(
        "app.workers.tasks.parse_presentation.apply_async",
        return_value=mock_result,
    ) as mock_apply:
        enqueue_parse_presentation(job_id, presentation_id)

    kwargs = mock_apply.call_args[1]["kwargs"]
    assert kwargs["job_id"] == str(job_id)
    assert kwargs["presentation_id"] == str(presentation_id)
