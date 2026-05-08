# Video Generation Debug Diagnostics

These diagnostics are development-only. They are disabled when `APP_ENV=production`.

## ElevenLabs test

Run:

```bash
curl -X POST http://localhost:8000/api/v1/projects/{project_id}/debug/elevenlabs-test
```

The endpoint loads the saved ElevenLabs API key and voice ID from backend settings, calls the real ElevenLabs text-to-speech API, writes `/tmp/educa_test_audio.mp3`, verifies it with `ffprobe`, uploads a debug copy to storage, and returns a signed download URL.

It logs only safe values: voice ID, output path, size, and duration. It never logs the API key.

The endpoint uses the same `ElevenLabsTTSProvider` adapter used by the generation worker. A successful response confirms the adapter can produce a real MP3, not a local silent placeholder.

## WaveSpeed test

Run:

```bash
curl -X POST "http://localhost:8000/api/v1/projects/{project_id}/debug/wavespeed-test?avatar_source_url=https://example.com/avatar.png"
```

The endpoint uses `/tmp/educa_test_audio.mp3`, uploads it to storage, creates a provider-accessible signed URL, calls the WaveSpeed LTX-2 lipsync API, writes `/tmp/educa_test_avatar.mp4`, verifies it with `ffprobe`, uploads a debug copy to storage, and returns a browser download URL.

WaveSpeed must be able to fetch the audio and avatar URLs from outside Docker. Use these settings for local Docker:

```env
MINIO_INTERNAL_ENDPOINT=http://minio:9000
MINIO_PUBLIC_ENDPOINT=http://localhost:9000
EXTERNAL_PROVIDER_ASSET_BASE_URL=https://your-public-minio-tunnel.example
```

`MINIO_INTERNAL_ENDPOINT` is for API/worker storage reads and writes. `MINIO_PUBLIC_ENDPOINT` is for browser preview/download URLs. `EXTERNAL_PROVIDER_ASSET_BASE_URL` is for WaveSpeed and must be a public URL, for example an ngrok/cloudflared tunnel or external object storage endpoint.

If `EXTERNAL_PROVIDER_ASSET_BASE_URL` is empty and the only available URL is `localhost`, `127.0.0.1`, `minio`, or a private IP, WaveSpeed diagnostics and real generation fail with `EXTERNAL_ASSET_URL_NOT_PUBLIC`.

Avatar source resolution order:

1. `avatar_source_url` query parameter.
2. Uploaded project avatar asset from the editor.
3. `DEBUG_AVATAR_SOURCE_URL` environment variable.
4. Legacy project generation config `avatar_id`, but only if it is an `http` or `https` URL.

In the normal editor flow, users should upload the avatar image in the Avatar section. The manual `avatar_source_url` query parameter is only for diagnostics.

If no avatar source is available, the debug endpoint returns `MISSING_AVATAR_SOURCE`; the real generation start path returns `MISSING_AVATAR_ASSET`.

The endpoint uses the same `WavespeedAvatarVideoProvider` adapter used by the generation worker. A successful response confirms the adapter can produce a real lipsync MP4, not a local placeholder clip.

## FFmpeg composition test

Run:

```bash
curl -X POST http://localhost:8000/api/v1/projects/{project_id}/debug/ffmpeg-compose-test
```

The endpoint uses:

- `/tmp/test_slide.png`
- `/tmp/educa_test_avatar.mp4`
- `/tmp/educa_test_audio.mp3`

It writes `/tmp/educa_test_composed.mp4`, verifies video and audio streams with `ffprobe`, uploads a debug copy to storage, and returns a signed download URL.

## Pipeline asset links

Run:

```bash
curl http://localhost:8000/api/v1/projects/{project_id}/debug/generation-assets
```

The endpoint returns signed download URLs for the latest generation job assets, including `tts_audio`, `avatar_clip`, `slide_render`, `slide_video`, and `final_video` when present.

In the editor, development builds also show a `Debug assets` section after a completed or failed generation.

## Confirming the real pipeline uses real providers

1. Run the ElevenLabs debug test and confirm the returned MP3 is playable.
2. Run the WaveSpeed debug test and confirm the returned MP4 is playable and shows the configured avatar source.
3. Generate a video from the editor.
4. Open `GET /api/v1/projects/{project_id}/debug/generation-assets`.
5. Confirm the latest generation contains:
   - `tts_audio` assets with `.mp3` filenames and `audio/mpeg` MIME type.
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

- `ElevenLabsTTSProvider` calls `https://api.elevenlabs.io/v1/text-to-speech/{voice_id}` and returns MP3 bytes.
- `WavespeedAvatarVideoProvider` calls `https://api.wavespeed.ai/api/v3/wavespeed-ai/ltx-2-19b/lipsync` and returns MP4 bytes.

The real pipeline should no longer create local silent WAV files or generated placeholder avatar clips for ElevenLabs/WaveSpeed jobs.

If ElevenLabs fails, the job should fail with `ELEVENLABS_TTS_FAILED`, `INVALID_ELEVENLABS_CREDENTIALS`, or `MISSING_ELEVENLABS_VOICE_ID`.

If WaveSpeed fails, the job should fail with `WAVESPEED_AVATAR_FAILED`, `INVALID_WAVESPEED_CREDENTIALS`, `MISSING_AVATAR_ASSET`, or `AVATAR_SIGNED_URL_FAILED`.

If FFmpeg fails, the job should fail with `VIDEO_COMPOSITION_FAILED`.
