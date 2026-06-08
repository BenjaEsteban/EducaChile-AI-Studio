const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export class ApiError extends Error {
  status: number;
  detail: unknown;
  errorCode?: string;

  constructor(status: number, path: string, detail: unknown) {
    const parsed = parseErrorDetail(detail);
    super(parsed.message || `API error ${status}: ${path}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.errorCode = parsed.errorCode;
  }
}

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw await buildApiError(res, path);
  if (res.status === 204) return undefined as T;
  return res.json() as Promise<T>;
}

async function apiFetchOptional<T>(path: string, init?: RequestInit): Promise<T | null> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (res.status === 404) return null;
  if (!res.ok) throw await buildApiError(res, path);
  return res.json() as Promise<T>;
}

async function apiUpload(path: string, file: File): Promise<void> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw await buildApiError(res, path);
}

async function apiUploadJson<T>(path: string, file: File): Promise<T> {
  const form = new FormData();
  form.append("file", file);
  const res = await fetch(`${BASE_URL}${path}`, {
    method: "POST",
    body: form,
  });
  if (!res.ok) throw await buildApiError(res, path);
  return res.json() as Promise<T>;
}

async function buildApiError(res: Response, path: string): Promise<ApiError> {
  let detail: unknown = null;
  try {
    detail = await res.json();
  } catch {
    detail = await res.text().catch(() => null);
  }
  return new ApiError(res.status, path, detail);
}

function parseErrorDetail(detail: unknown): { message?: string; errorCode?: string } {
  const payload = isRecord(detail) && "detail" in detail ? detail.detail : detail;
  if (isRecord(payload)) {
    const message = typeof payload.message === "string" ? payload.message : undefined;
    const errorCode =
      typeof payload.error_code === "string"
        ? payload.error_code
        : typeof payload.code === "string"
          ? payload.code
          : undefined;
    return { message, errorCode };
  }
  if (typeof payload === "string") return { message: payload };
  return {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

export interface HealthResponse {
  status: string;
}

export interface Project {
  id: string;
  organization_id: string;
  owner_id: string | null;
  folder_id: string | null;
  name: string;
  description: string | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
}

export interface ProjectOpenState {
  project_id: string;
  has_presentation: boolean;
  presentation_id: string | null;
  presentation_status:
    | "upload_pending"
    | "uploaded"
    | "processing"
    | "parsed"
    | "ready"
    | "failed"
    | null;
  slide_count: number;
  has_slides: boolean;
  has_generated_video: boolean;
  generated_video_asset_id: string | null;
  generated_video_url: string | null;
  latest_generation_job_id: string | null;
  latest_generation_status:
    | "pending"
    | "validating"
    | "queued"
    | "generating_audio"
    | "generating_avatar"
    | "rendering_slides"
    | "composing_slide"
    | "composing_video"
    | "completed"
    | "failed"
    | "cancelled"
    | null;
}

export interface ProjectList {
  items: Project[];
  total: number;
  skip: number;
  limit: number;
}

export type AvatarFitMode = "custom" | "full_slide" | "cover" | "contain";

export interface ProjectAvatar {
  object_key: string;
  filename: string;
  content_type: string | null;
  url: string;
  x: number;
  y: number;
  width: number;
  height: number;
  border_radius: number;
  fit_mode: AvatarFitMode;
  updated_at: string | null;
  avatar_asset_id: string;
  avatar_preview_url: string;
  mime_type: string | null;
  size_bytes: number | null;
}

export interface ProjectAvatarLayoutUpdate {
  x: number;
  y: number;
  width: number;
  height: number;
  border_radius?: number;
  fit_mode?: AvatarFitMode;
}

/** Per-slide avatar placement metadata stored inside slide.metadata. */
export interface SlideAvatarMeta {
  x: number;        // canvas x in 960×540 coordinate space
  y: number;
  width: number;
  height: number;
  visible: boolean;
  borderRadius: number;  // 0–50 (0 = square, 50 = circle)
  fitMode: AvatarFitMode;
}

export const CANVAS_W = 960;
export const CANVAS_H = 540;

export const DEFAULT_SLIDE_AVATAR: SlideAvatarMeta = {
  x: 720,
  y: 310,
  width: 200,
  height: 200,
  visible: true,
  borderRadius: 0,
  fitMode: "custom",
};

export function extractSlideAvatarMeta(slide: Slide): SlideAvatarMeta {
  const meta = slide.metadata;
  const canvas = (meta.canvas as Record<string, unknown>) || {};
  const avatar = (canvas.avatar as Record<string, unknown>) || {};

  const visible = meta.avatar_visible !== false;
  const borderRadius =
    typeof meta.avatar_border_radius === "number" ? meta.avatar_border_radius : 0;
  const fitMode = (meta.avatar_fit_mode as AvatarFitMode) || "custom";

  return {
    x: typeof avatar.x === "number" ? avatar.x : DEFAULT_SLIDE_AVATAR.x,
    y: typeof avatar.y === "number" ? avatar.y : DEFAULT_SLIDE_AVATAR.y,
    width: typeof avatar.width === "number" ? avatar.width : DEFAULT_SLIDE_AVATAR.width,
    height: typeof avatar.height === "number" ? avatar.height : DEFAULT_SLIDE_AVATAR.height,
    visible,
    borderRadius,
    fitMode,
  };
}

/** Build the metadata patch that stores avatar config inside a slide's canvas. */
export function buildSlideAvatarPatch(
  slide: Slide,
  avatarMeta: SlideAvatarMeta,
): Record<string, unknown> {
  const existingCanvas = (slide.metadata.canvas as Record<string, unknown>) || {};
  return {
    canvas: {
      ...existingCanvas,
      width: CANVAS_W,
      height: CANVAS_H,
      avatar: {
        x: avatarMeta.x,
        y: avatarMeta.y,
        width: avatarMeta.width,
        height: avatarMeta.height,
      },
    },
    avatar_visible: avatarMeta.visible,
    avatar_border_radius: avatarMeta.borderRadius,
    avatar_fit_mode: avatarMeta.fitMode,
  };
}

export interface CreateProjectInput {
  name: string;
  description?: string | null;
  folder_id?: string | null;
}

export interface Folder {
  id: string;
  organization_id: string;
  parent_folder_id: string | null;
  name: string;
  created_at: string;
  updated_at: string;
}

export interface FolderTreeNode {
  id: string;
  parent_folder_id: string | null;
  name: string;
  created_at: string;
  updated_at: string;
  children: FolderTreeNode[];
}

export interface FolderTree {
  items: FolderTreeNode[];
}

export interface CreateFolderInput {
  name: string;
  parent_folder_id?: string | null;
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
  voice_id: string | null;
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
    | "composing_slide"
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
  avatar_source_url: string | null;
  avatar_source_asset_id: string | null;
  using_debug_avatar_source: boolean;
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
  avatar_source_url?: string | null;
  avatar_source_asset_id?: string | null;
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
    | "composing_slide"
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
  updated_at: string | null;
}

export interface DebugGenerationAsset {
  id: string;
  slide_id: string | null;
  asset_type: string;
  storage_key: string;
  filename: string;
  mime_type: string | null;
  size_bytes: number;
  duration_seconds: number | null;
  download_url: string;
}

export interface DebugGenerationAssetsResponse {
  ok: boolean;
  has_video_settings: boolean;
  latest_generation_job_id: string | null;
  assets: DebugGenerationAsset[];
}

export const api = {
  health: () => apiFetch<HealthResponse>("/health"),
  projects: {
    list: (input?: { folderId?: string | null }) => {
      const params = new URLSearchParams();
      if (input?.folderId) params.set("folder_id", input.folderId);
      const query = params.toString();
      return apiFetch<ProjectList>(`/api/v1/projects/${query ? `?${query}` : ""}`);
    },
    create: (input: CreateProjectInput) =>
      apiFetch<Project>("/api/v1/projects/", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    move: (projectId: string, folder_id: string | null) =>
      apiFetch<Project>(`/api/v1/projects/${projectId}/move`, {
        method: "POST",
        body: JSON.stringify({ folder_id }),
      }),
    listFolderTree: () => apiFetch<FolderTree>("/api/v1/projects/folders/tree"),
    createFolder: (input: CreateFolderInput) =>
      apiFetch<Folder>("/api/v1/projects/folders", {
        method: "POST",
        body: JSON.stringify(input),
      }),
    renameFolder: (folderId: string, name: string) =>
      apiFetch<Folder>(`/api/v1/projects/folders/${folderId}`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      }),
    deleteFolder: (folderId: string, cascade = false) =>
      apiFetch<void>(`/api/v1/projects/folders/${folderId}?cascade=${cascade}`, {
        method: "DELETE",
      }),
    delete: (projectId: string) =>
      apiFetch<void>(`/api/v1/projects/${projectId}`, {
        method: "DELETE",
      }),
    get: (projectId: string) => apiFetch<Project>(`/api/v1/projects/${projectId}`),
    getOpenState: (projectId: string) =>
      apiFetch<ProjectOpenState>(`/api/v1/projects/${projectId}/open-state`),
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
    getAvatar: (projectId: string) =>
      apiFetchOptional<ProjectAvatar>(`/api/v1/projects/${projectId}/avatar`),
    uploadAvatar: (projectId: string, file: File) =>
      apiUploadJson<ProjectAvatar>(`/api/v1/projects/${projectId}/avatar`, file),
    updateAvatarLayout: (projectId: string, payload: ProjectAvatarLayoutUpdate) =>
      apiFetch<ProjectAvatar>(`/api/v1/projects/${projectId}/avatar/layout`, {
        method: "PATCH",
        body: JSON.stringify(payload),
      }),
    deleteAvatar: (projectId: string) =>
      apiFetch<void>(`/api/v1/projects/${projectId}/avatar`, {
        method: "DELETE",
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
      api_key?: string | null;
      voice_id?: string | null;
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
    debugAssets: (projectId: string) =>
      apiFetch<DebugGenerationAssetsResponse>(
        `/api/v1/projects/${projectId}/debug/generation-assets`,
      ),
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
