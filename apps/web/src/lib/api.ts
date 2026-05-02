const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json() as Promise<T>;
}

async function apiUpload(path: string, file: File): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw new Error(`API upload error ${res.status}: ${path}`);
}

export interface HealthResponse {
  status: string;
}

export interface Project {
  id: string;
  organization_id: string;
  owner_id: string | null;
  name: string;
  description: string | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
}

export interface ProjectList {
  items: Project[];
  total: number;
  skip: number;
  limit: number;
}

export interface CreateProjectInput {
  name: string;
  description?: string | null;
}

export interface InitPresentationUploadInput {
  filename: string;
  content_type: string;
}

export interface InitPresentationUploadResponse {
  presentation_id: string;
  upload_url: string;
  storage_key: string;
  expires_in: number;
  method: "PUT";
}

export interface ConfirmPresentationUploadResponse {
  presentation_id: string;
  status: "upload_pending" | "uploaded" | "processing" | "parsed" | "ready" | "failed";
  job_id: string;
}

export interface Slide {
  id: string;
  presentation_id: string;
  position: number;
  title: string | null;
  notes: string | null;
  thumbnail_key: string | null;
  preview_image_url: string | null;
  background_image_url: string | null;
  visible_text: string;
  dialogue: string;
  metadata: Record<string, unknown>;
  created_at: string;
  updated_at: string;
}

export interface UpdateSlideInput {
  title?: string | null;
  notes?: string | null;
  dialogue?: string;
  visible_text?: string;
  metadata?: Record<string, unknown>;
}

export interface PresignedDownloadResponse {
  url: string;
  key: string;
  expires_in: number;
}

export interface ProjectGenerationConfig {
  id: string | null;
  project_id: string;
  ai_provider: "gemini";
  tts_provider: "gemini" | "elevenlabs";
  video_provider: "wavespeed";
  voice_id: string | null;
  voice_name: string | null;
  avatar_id: string | null;
  gemini_api_key: string | null;
  elevenlabs_api_key: string | null;
  wavespeed_api_key: string | null;
  resolution: string;
  aspect_ratio: string;
  language: string;
  output_format: "mp4";
  subtitles_enabled: boolean;
  background_music_enabled: boolean;
  status: "draft" | "configured" | "ready_for_generation";
  created_at: string | null;
  updated_at: string | null;
}

export interface UpdateProjectGenerationConfigInput {
  ai_provider: "gemini";
  tts_provider: "gemini" | "elevenlabs";
  video_provider: "wavespeed";
  voice_id?: string | null;
  voice_name?: string | null;
  avatar_id?: string | null;
  gemini_api_key?: string | null;
  elevenlabs_api_key?: string | null;
  wavespeed_api_key?: string | null;
  resolution: string;
  aspect_ratio: string;
  language: string;
  output_format: "mp4";
  subtitles_enabled: boolean;
  background_music_enabled: boolean;
}

export interface ProviderCredentialStatus {
  id: string | null;
  provider_name: "gemini" | "elevenlabs" | "wavespeed";
  provider_type: "ai" | "tts" | "avatar_video";
  masked_api_key: string | null;
  key_last_four: string | null;
  status: "not_configured" | "configured" | "valid" | "invalid" | "expired_or_revoked";
  last_validated_at: string | null;
  updated_at: string | null;
}

export interface ProviderCredentialValidation {
  provider_name: string;
  provider_type: string;
  status: ProviderCredentialStatus["status"];
  valid: boolean;
  message: string;
  last_validated_at: string | null;
}

export interface GenerationJob {
  id: string;
  job_id: string | null;
  organization_id: string;
  project_id: string;
  status:
    | "pending"
    | "validating"
    | "queued"
    | "generating_audio"
    | "generating_avatar"
    | "rendering_slides"
    | "composing_video"
    | "completed"
    | "failed"
    | "cancelled";
  progress_percentage: number;
  current_step: string | null;
  current_slide: number | null;
  total_slides: number | null;
  error_code: string | null;
  error_message: string | null;
  final_asset_id: string | null;
  result: Record<string, unknown> | null;
  started_at: string | null;
  completed_at: string | null;
  created_at: string;
  updated_at: string;
}

export interface StartGenerationResponse {
  generation_job: GenerationJob;
  job_id: string;
}

export interface FinalVideoResponse {
  ready: boolean;
  asset_id: string | null;
  url: string | null;
  storage_key: string | null;
  mime_type: string | null;
  size_bytes: number | null;
}

export interface VideoSettings {
  elevenlabs_api_key_masked: string | null;
  elevenlabs_voice_id: string | null;
  wavespeed_api_key_masked: string | null;
  elevenlabs_valid: boolean;
  wavespeed_valid: boolean;
  validation_status: "not_configured" | "saved" | "valid" | "invalid";
  last_validated_at: string | null;
  updated_at: string | null;
}

export interface UpdateVideoSettingsInput {
  elevenlabs_api_key?: string | null;
  elevenlabs_voice_id?: string | null;
  wavespeed_api_key?: string | null;
}

export interface VideoSettingsValidation extends VideoSettings {
  message: string;
}

export interface GenerationStatus {
  status:
    | "idle"
    | "pending"
    | "validating"
    | "queued"
    | "generating_audio"
    | "generating_avatar"
    | "rendering_slides"
    | "composing_video"
    | "completed"
    | "failed"
    | "cancelled";
  progress: number;
  current_slide: number | null;
  total_slides: number | null;
  message: string | null;
  error_code: string | null;
  error_message: string | null;
  final_video_url: string | null;
}

export const api = {
  health: () => apiFetch<HealthResponse>("/health"),
  projects: {
    list: () => apiFetch<ProjectList>("/api/v1/projects/"),
    create: (input: CreateProjectInput) =>
      apiFetch<Project>("/api/v1/projects/", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    get: (projectId: string) => apiFetch<Project>(`/api/v1/projects/${projectId}`),
    getGenerationConfig: (projectId: string) =>
      apiFetch<ProjectGenerationConfig>(`/api/v1/projects/${projectId}/generation-config`),
    updateGenerationConfig: (
      projectId: string,
      input: UpdateProjectGenerationConfigInput,
    ) =>
      apiFetch<ProjectGenerationConfig>(`/api/v1/projects/${projectId}/generation-config`, {
        method: "PUT",
        body: JSON.stringify(input),
      }),
  },
  presentations: {
    initUpload: (projectId: string, input: InitPresentationUploadInput) =>
      apiFetch<InitPresentationUploadResponse>(
        `/api/v1/projects/${projectId}/presentations/init-upload`,
        {
          method: "POST",
          body: JSON.stringify(input),
        },
      ),
    confirmUpload: (presentationId: string) =>
      apiFetch<ConfirmPresentationUploadResponse>(
        `/api/v1/presentations/${presentationId}/confirm-upload`,
        { method: "POST" },
      ),
    uploadFile: (presentationId: string, file: File) =>
      apiUpload(`/api/v1/presentations/${presentationId}/upload-file`, file),
    listSlides: (presentationId: string) =>
      apiFetch<Slide[]>(`/api/v1/presentations/${presentationId}/slides`),
  },
  providerCredentials: {
    listStatus: () =>
      apiFetch<ProviderCredentialStatus[]>("/api/v1/provider-credentials/status"),
    save: (input: {
      provider_name: ProviderCredentialStatus["provider_name"];
      provider_type: ProviderCredentialStatus["provider_type"];
      api_key: string;
    }) =>
      apiFetch<ProviderCredentialStatus>("/api/v1/provider-credentials", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    validate: (
      providerName: ProviderCredentialStatus["provider_name"],
      providerType: ProviderCredentialStatus["provider_type"],
    ) =>
      apiFetch<ProviderCredentialValidation>(
        `/api/v1/provider-credentials/${providerName}/${providerType}/validate`,
        { method: "POST" },
      ),
  },
  generation: {
    start: (projectId: string) =>
      apiFetch<StartGenerationResponse>(`/api/v1/projects/${projectId}/generate-video`, {
        method: "POST",
      }),
    getJob: (projectId: string, jobId: string) =>
      apiFetch<GenerationJob>(`/api/v1/projects/${projectId}/generation-jobs/${jobId}`),
    status: (projectId: string) =>
      apiFetch<GenerationStatus>(`/api/v1/projects/${projectId}/generation-status`),
    finalVideo: (projectId: string) =>
      apiFetch<FinalVideoResponse>(`/api/v1/projects/${projectId}/final-video`),
  },
  videoSettings: {
    get: (projectId: string) =>
      apiFetch<VideoSettings>(`/api/v1/projects/${projectId}/video-settings`),
    update: (projectId: string, input: UpdateVideoSettingsInput) =>
      apiFetch<VideoSettings>(`/api/v1/projects/${projectId}/video-settings`, {
        method: "PUT",
        body: JSON.stringify(input),
      }),
    validate: (projectId: string) =>
      apiFetch<VideoSettingsValidation>(
        `/api/v1/projects/${projectId}/video-settings/validate`,
        { method: "POST" },
      ),
  },
  slides: {
    update: (slideId: string, input: UpdateSlideInput) =>
      apiFetch<Slide>(`/api/v1/slides/${slideId}`, {
        method: "PATCH",
        body: JSON.stringify(input),
      }),
  },
  storage: {
    presignedDownload: (key: string) =>
      apiFetch<PresignedDownloadResponse>(
        `/api/v1/storage/presigned-download?key=${encodeURIComponent(key)}`,
      ),
  },
};
