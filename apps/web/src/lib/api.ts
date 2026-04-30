const BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...init,
  });
  if (!res.ok) throw new Error(`API error ${res.status}: ${path}`);
  return res.json() as Promise<T>;
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
  tts_provider: "gemini" | "elevenlabs";
  video_provider: "wavespeed";
  voice_id: string | null;
  voice_name: string | null;
  gemini_api_key: string | null;
  elevenlabs_api_key: string | null;
  wavespeed_api_key: string | null;
  resolution: string;
  aspect_ratio: string;
  created_at: string | null;
  updated_at: string | null;
}

export interface UpdateProjectGenerationConfigInput {
  tts_provider: "gemini" | "elevenlabs";
  video_provider: "wavespeed";
  voice_id?: string | null;
  voice_name?: string | null;
  gemini_api_key?: string | null;
  elevenlabs_api_key?: string | null;
  wavespeed_api_key?: string | null;
  resolution: string;
  aspect_ratio: string;
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
    listSlides: (presentationId: string) =>
      apiFetch<Slide[]>(`/api/v1/presentations/${presentationId}/slides`),
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
