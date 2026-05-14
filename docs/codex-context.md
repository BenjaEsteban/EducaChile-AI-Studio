# Educa cHILE AI Studio — Codex Context

## Project overview

Educa cHILE AI Studio is a SaaS web platform that generates educational videos from PowerPoint presentations.

The user uploads a PPT/PPTX, edits the slides in a canvas-like editor, configures voice/avatar settings, and exports a final MP4 video using AI services.

## Current stack

- Frontend: Next.js / React
- Backend: FastAPI
- Database: PostgreSQL
- Storage: MinIO / S3-compatible storage
- Async jobs: Redis / Celery workers
- Video composition: FFmpeg
- Default avatar provider: WaveSpeed AI Talking Photos
- Optional advanced TTS provider: ElevenLabs

## Current user flow

1. User uploads a PPT/PPTX.
2. Backend parses the presentation.
3. Slides are displayed in a canvas-like editor.
4. User can edit slide text directly inside the editor.
5. The editor must preserve original PPT design, fonts, colors, positions and layout.
6. User can configure avatar position.
7. Video generation happens from inside the editor, not from a separate configuration page.
8. Final video must use the edited canvas state, not only the original PPT image.

## Important implementation decisions

- Do not render the PPT as a flat image and place duplicate text on top.
- PPT text must become editable real elements.
- Preserve font family, font size, color, position and alignment from the original PPT.
- API keys must be stored backend-side only.
- Raw API keys must never be returned to the frontend.
- Raw API keys must not be stored in localStorage or sessionStorage.
- Saved keys should be shown only masked.
- For the MVP, only these video settings are required:
  - WaveSpeed API Key
  - ElevenLabs API Key and Voice ID are optional advanced settings

## Video generation pipeline

When the user clicks Generate Video inside the editor, the async pipeline should:

1. Load edited slides and dialogue/notes.
2. Generate avatar/talking-photo clip per slide with WaveSpeed using the narration text.
3. Generate audio per slide only if an advanced voice mode is enabled.
4. Render the edited slide canvas.
5. Use FFmpeg to compose:
   - slide render as background
   - avatar clip as overlay
   - avatar clip audio as the audio track by default
6. Generate one MP4 per slide.
7. Concatenate all slide videos into final.mp4.
8. Show progress and final preview/download in the editor.

## Current known bugs

1. The generated video only shows the PPT slides. It does not include avatar or audio.
2. After generating one video, the user cannot generate another video.

## Expected fixes

- Ensure FFmpeg uses slide render + avatar clip + audio as inputs.
- Use explicit FFmpeg stream mapping.
- Do not silently generate partial videos if audio or avatar is missing.
- Validate that audio and avatar files exist before composing.
- Allow regeneration by creating a new GenerationJob each time unless another job is already running.
- If a job is completed or failed, allow Generate Again / Try Again.
- The final video URL should always point to the latest generated MP4.

## Development rules

- Inspect the existing repository before making changes.
- Adapt changes incrementally.
- Do not rewrite the whole app.
- Do not remove existing working editor functionality.
- Keep backend logic modular.
- Do not put provider-specific logic directly inside route handlers.
- Use services/adapters for Wavespeed, optional ElevenLabs and FFmpeg composition.
- Heavy processing must run in workers, not inside HTTP requests.
