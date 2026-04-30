import logging
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)


def render_slide_previews(
    pptx_bytes: bytes,
    presentation_id: uuid.UUID,
    original_filename: str,
    storage,
) -> dict[int, str]:
    """Render PPT/PPTX slides to PNG previews and store them in object storage.

    Rendering is best-effort: text parsing remains useful in test/CI environments where
    LibreOffice or Poppler may not be installed.
    """
    if not shutil.which("soffice") or not shutil.which("pdftoppm"):
        logger.warning(
            "Slide preview rendering skipped: LibreOffice soffice or pdftoppm is unavailable."
        )
        return {}

    suffix = Path(original_filename).suffix.lower()
    if suffix not in {".ppt", ".pptx"}:
        suffix = ".pptx"

    try:
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            input_path = tmpdir / f"presentation{suffix}"
            input_path.write_bytes(pptx_bytes)

            subprocess.run(
                [
                    "soffice",
                    "--headless",
                    "--nologo",
                    "--nofirststartwizard",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(tmpdir),
                    str(input_path),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )

            pdf_path = tmpdir / "presentation.pdf"
            if not pdf_path.exists():
                pdfs = list(tmpdir.glob("*.pdf"))
                if not pdfs:
                    raise RuntimeError("LibreOffice did not produce a PDF preview source")
                pdf_path = pdfs[0]

            output_prefix = tmpdir / "slide"
            subprocess.run(
                [
                    "pdftoppm",
                    "-png",
                    "-r",
                    "144",
                    str(pdf_path),
                    str(output_prefix),
                ],
                check=True,
                capture_output=True,
                text=True,
                timeout=120,
            )

            preview_keys: dict[int, str] = {}
            image_paths = sorted(tmpdir.glob("slide-*.png"), key=_rendered_slide_number)
            version = uuid.uuid4().hex
            for index, image_path in enumerate(image_paths, 1):
                key = f"presentations/{presentation_id}/previews/{version}/slide-{index}.png"
                storage.upload_file(
                    key=key,
                    data=image_path.read_bytes(),
                    content_type="image/png",
                )
                preview_keys[index] = key
                logger.info(
                    "Rendered slide preview %s for presentation %s to %s",
                    index,
                    presentation_id,
                    key,
                )

            logger.info(
                "Rendered %s slide previews for presentation %s",
                len(preview_keys),
                presentation_id,
            )
            return preview_keys
    except Exception as exc:
        logger.warning("Slide preview rendering failed: %s", exc)
        return {}


def _rendered_slide_number(path: Path) -> int:
    try:
        return int(path.stem.rsplit("-", 1)[-1])
    except ValueError:
        return 0
