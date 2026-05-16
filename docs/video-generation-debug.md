# Video Generation Debug Diagnostics

These diagnostics are development-only. They are disabled when `APP_ENV=production`.

## ElevenLabs test

This is optional advanced voice-mode diagnostics. The default generation flow no longer requires ElevenLabs.

Run:

```bash
curl -X POST http://localhost:8000/api/v1/projects/{project_id}/debug/elevenlabs-test
```

The endpoint loads the saved ElevenLabs API key and voice ID from backend settings, calls the real ElevenLabs text-to-speech API, writes `/tmp/educa_test_audio.mp3`, verifies it with `ffprobe`, uploads a debug copy to storage, and returns a signed download URL.

It logs only safe values: voice ID, output path, size, and duration. It never logs the API key.

The endpoint uses the same `ElevenLabsTTSProvider` adapter used by the optional advanced voice path. A successful response confirms the adapter can produce a real MP3, not a local silent placeholder.

## WaveSpeed audio-driven lip-sync test

Run:

```bash
curl -X POST "http://localhost:8000/api/v1/projects/{project_id}/debug/wavespeed-test"
```

The endpoint loads the configured avatar image from the project settings, splits long narration into bounded Spanish chunks, generates controlled audio for each chunk, then calls `POST /api/v3/wavespeed-ai/infinitetalk` with:

```json
{
  "image": "<avatar_image_url>",
  "audio": "<narration_audio_url>",
  "resolution": "480p",
  "seed": -1
}
```

It polls `GET /api/v3/predictions/{request_id}/result`, strips any provider audio from the returned MP4, writes `/tmp/educa_test_avatar.mp4`, verifies it with `ffprobe`, uploads a debug copy to storage, and returns a browser download URL.

The output should contain a video stream only. The older `ai-talking-photos` mode remains available only as a fallback/dev diagnostic.

Use these settings for local Docker:

```env
MINIO_INTERNAL_ENDPOINT=http://minio:9000
MINIO_PUBLIC_ENDPOINT=http://localhost:9000
WAVESPEED_API_KEY=...
WAVESPEED_BASE_URL=https://api.wavespeed.ai/api/v3
TTS_PROVIDER=elevenlabs
AVATAR_GENERATION_MODE=fast_lipsync
AVATAR_LIPSYNC_PROVIDER=wavespeed_sync_lipsync_3
AVATAR_SYNC_MODE=loop
AVATAR_LIPSYNC_MODEL_PATH=wavespeed-ai/sync-lipsync-3
AVATAR_LIPSYNC_RESOLUTION=480p
TTS_LANGUAGE=es
TTS_SPEED=0.85
ENABLE_SUBTITLES=false
FFMPEG_TIMEOUT_SECONDS=900
WAVESPEED_HTTP_TIMEOUT_SECONDS=300
WAVESPEED_PREDICTION_TIMEOUT_SECONDS=1800
WAVESPEED_POLL_INTERVAL_SECONDS=8
REQUIRE_EXTERNAL_PROVIDER_URL_VALIDATION=true
MAX_TTS_CHARS_PER_CHUNK=700
MAX_LIPSYNC_AUDIO_SECONDS_PER_CHUNK=30
MAX_AVATAR_AUDIO_SECONDS_PER_CHUNK=30
MAX_CHUNKS_PER_SLIDE=4
AVATAR_CHUNK_CONCURRENCY=2
AVATAR_PROVIDER_CHUNK_TIMEOUT_SECONDS=300
AVATAR_PROVIDER_MAX_RETRIES=1
ENABLE_STATIC_AVATAR_FALLBACK=true
MAX_AUDIO_CHUNK_DURATION_TOLERANCE_SECONDS=1.0
ALLOW_DUMMY_TTS=false
MIN_EXPECTED_AUDIO_DURATION_RATIO=0.5
CELERY_TASK_SOFT_TIME_LIMIT=3300
CELERY_TASK_TIME_LIMIT=3600
```

`MINIO_INTERNAL_ENDPOINT` is for API/worker storage reads and writes. `MINIO_PUBLIC_ENDPOINT` is for browser preview/download URLs.

Avatar source resolution order:

1. `avatar_source_url` query parameter.
2. Uploaded project avatar asset from the editor.
3. `DEBUG_AVATAR_SOURCE_URL` environment variable.
4. Legacy project generation config `avatar_id`, but only if it is an `http` or `https` URL.

In the normal editor flow, users should upload the avatar image in the Avatar section. The manual `avatar_source_url` query parameter is only for diagnostics.

If no avatar source is available, the debug endpoint returns `MISSING_AVATAR_SOURCE`; the real generation start path returns `MISSING_AVATAR_ASSET`.

The endpoint uses the same `WavespeedAvatarVideoProvider` adapter used by the generation worker. A successful response confirms the adapter can produce a real audio-driven lip-sync MP4, not a local placeholder clip.

## FFmpeg composition test

Run:

```bash
curl -X POST http://localhost:8000/api/v1/projects/{project_id}/debug/ffmpeg-compose-test
```

The endpoint uses:

- `/tmp/test_slide.png`
- `/tmp/educa_test_avatar.mp4`

It maps the talking-photo clip audio stream directly, writes `/tmp/educa_test_composed.mp4`, verifies video and audio streams with `ffprobe`, uploads a debug copy to storage, and returns a signed download URL.

## Pipeline asset links

Run:

```bash
curl http://localhost:8000/api/v1/projects/{project_id}/debug/generation-assets
```

The endpoint returns signed download URLs for the latest generation job assets, including `avatar_clip`, `slide_render`, `slide_video`, `final_video`, and `slide_audio` when an advanced voice mode is used.

In the editor, development builds also show a `Debug assets` section after a completed or failed generation.

## Confirming the real pipeline uses real providers

1. Run the ElevenLabs debug test and confirm the returned MP3 is playable.
2. Run the WaveSpeed audio-driven lip-sync debug test and confirm the returned MP4 is playable.
3. Generate a video from the editor.
4. Open `GET /api/v1/projects/{project_id}/debug/generation-assets`.
5. Confirm the latest generation contains:
   - `avatar_clip` assets with `.mp4` filenames.
   - `slide_render` assets.
   - `slide_video` assets.
   - `final_video`.
6. Download a `slide_video` and the `final_video` and inspect with:

```bash
ffprobe -v error -show_entries stream=codec_type -of csv=p=0 downloaded-file.mp4
```

The output must include both `video` and `audio`.

The generation worker now calls the same real provider adapters:

- `WavespeedClient.create_infinite_talk()` calls `https://api.wavespeed.ai/api/v3/wavespeed-ai/infinitetalk` with image and audio URLs and polls `/predictions/{request_id}/result` until a video URL is ready.
- `WavespeedTalkingPhotoProvider.generate_avatar_video()` remains available as the explicit fallback/dev mode and still calls `https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-talking-photos`.

The real pipeline should no longer create placeholder avatar clips for the default Wavespeed audio-driven flow.

If WaveSpeed fails, the job should fail with `WAVESPEED_TALKING_PHOTO_FAILED`, `INVALID_WAVESPEED_CREDENTIALS`, `MISSING_AVATAR_SOURCE`, `MISSING_AUDIO_ASSET`, or `INVALID_AVATAR_DURATION`.

If FFmpeg fails, the job should fail with `VIDEO_COMPOSITION_FAILED`.
