from __future__ import annotations

import argparse
import json
import sys
import uuid

from app.database import SessionLocal
from app.modules.generation.debug import avatar_pipeline_debug
from app.modules.generation.models import GenerationJob
from app.modules.projects.service import MOCK_ORG_ID


def main() -> int:
    parser = argparse.ArgumentParser(description="Inspect avatar generation artifacts for a project slide.")
    parser.add_argument("--project-id", required=True)
    parser.add_argument("--job-id", required=False)
    parser.add_argument("--slide-number", type=int, default=None)
    args = parser.parse_args()

    project_id = uuid.UUID(args.project_id)
    job_id = uuid.UUID(args.job_id) if args.job_id else None

    with SessionLocal() as db:
        if job_id is None:
            job = (
                db.query(GenerationJob)
                .filter(
                    GenerationJob.project_id == project_id,
                    GenerationJob.organization_id == MOCK_ORG_ID,
                )
                .order_by(GenerationJob.created_at.desc())
                .first()
            )
            if job is None:
                raise SystemExit("No generation job found for project")
            job_id = job.id
        payload = avatar_pipeline_debug(project_id, job_id, db, slide_number=args.slide_number)
        json.dump(payload, sys.stdout, indent=2, default=str)
        sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
