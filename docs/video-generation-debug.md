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

## WaveSpeed talking-photo test

Run:

```bash
curl -X POST "http://localhost:8000/api/v1/projects/{project_id}/debug/wavespeed-test"
```

The endpoint loads the configured avatar image from the project settings, uploads that image to WaveSpeed with `POST /api/v3/media/upload/binary`, then calls `POST /api/v3/wavespeed-ai/ai-talking-photos` with:

```json
{
  "image": "<download_url>",
  "text": "Hello, this is a test talking photo from Educa Chile.",
  "duration": 5,
  "seed": -1
}
```

It polls `GET /api/v3/predictions/{request_id}/result`, writes `/tmp/educa_test_avatar.mp4`, verifies it with `ffprobe`, uploads a debug copy to storage, and returns a browser download URL.

No ElevenLabs audio is required for this default talking-photo diagnostic. The output should contain both video and audio streams.

Use these settings for local Docker:

```env
MINIO_INTERNAL_ENDPOINT=http://minio:9000
MINIO_PUBLIC_ENDPOINT=http://localhost:9000
WAVESPEED_API_KEY=...
WAVESPEED_BASE_URL=https://api.wavespeed.ai/api/v3
```

`MINIO_INTERNAL_ENDPOINT` is for API/worker storage reads and writes. `MINIO_PUBLIC_ENDPOINT` is for browser preview/download URLs.

Avatar source resolution order:

1. `avatar_source_url` query parameter.
2. Uploaded project avatar asset from the editor.
3. `DEBUG_AVATAR_SOURCE_URL` environment variable.
4. Legacy project generation config `avatar_id`, but only if it is an `http` or `https` URL.

In the normal editor flow, users should upload the avatar image in the Avatar section. The manual `avatar_source_url` query parameter is only for diagnostics.

If no avatar source is available, the debug endpoint returns `MISSING_AVATAR_SOURCE`; the real generation start path returns `MISSING_AVATAR_ASSET`.

The endpoint uses the same `WavespeedAvatarVideoProvider` adapter used by the generation worker. A successful response confirms the adapter can produce a real talking-photo MP4, not a local placeholder clip.

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
2. Run the WaveSpeed talking-photo debug test and confirm the returned MP4 is playable.
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

- `WavespeedClient.upload_image()` calls `https://api.wavespeed.ai/api/v3/media/upload/binary` and returns a temporary image URL.
- `WavespeedTalkingPhotoProvider.generate_avatar_video()` calls `https://api.wavespeed.ai/api/v3/wavespeed-ai/ai-talking-photos` and polls `/predictions/{request_id}/result` until a video URL is ready.

The real pipeline should no longer create placeholder avatar clips for the default Wavespeed talking-photo flow.

If WaveSpeed fails, the job should fail with `WAVESPEED_TALKING_PHOTO_FAILED`, `INVALID_WAVESPEED_CREDENTIALS`, `MISSING_AVATAR_SOURCE`, or `INVALID_AVATAR_DURATION`.

If FFmpeg fails, the job should fail with `VIDEO_COMPOSITION_FAILED`.
