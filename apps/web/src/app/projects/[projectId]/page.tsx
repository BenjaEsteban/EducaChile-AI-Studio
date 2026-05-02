"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { ChangeEvent, useCallback, useEffect, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Project, Slide, api } from "@/lib/api";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("es-CL", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

type UploadStatus =
  | "idle"
  | "selected"
  | "initializing"
  | "uploading"
  | "confirming"
  | "processing"
  | "completed"
  | "failed";

const uploadStatusLabels: Record<UploadStatus, string> = {
  idle: "Selecciona un archivo PPT o PPTX.",
  selected: "Archivo seleccionado.",
  initializing: "Inicializando upload...",
  uploading: "Subiendo archivo...",
  confirming: "Confirmando upload...",
  processing: "Procesando presentacion...",
  completed: "Presentacion parseada.",
  failed: "El upload o parsing fallo.",
};

function isAllowedPresentation(file: File) {
  const lowerName = file.name.toLowerCase();
  return lowerName.endsWith(".ppt") || lowerName.endsWith(".pptx");
}

export default function ProjectDetailPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;
  const [project, setProject] = useState<Project | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [uploadStatus, setUploadStatus] = useState<UploadStatus>("idle");
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [presentationId, setPresentationId] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [slides, setSlides] = useState<Slide[]>([]);

  useEffect(() => {
    async function loadProject() {
      try {
        setError(null);
        const data = await api.projects.get(projectId);
        setProject(data);
      } catch (err) {
        setError(err instanceof Error ? err.message : "No se pudo cargar el proyecto.");
      } finally {
        setIsLoading(false);
      }
    }

    void loadProject();
  }, [projectId]);

  const refreshSlides = useCallback(
    async (id = presentationId) => {
      if (!id) return;
      const parsedSlides = await api.presentations.listSlides(id);
      setSlides(parsedSlides);
      if (parsedSlides.length > 0) {
        setUploadStatus("completed");
      }
    },
    [presentationId],
  );

  useEffect(() => {
    if (!presentationId || uploadStatus !== "processing") return;

    const interval = window.setInterval(() => {
      void refreshSlides(presentationId).catch(() => {
        setUploadStatus("failed");
        setUploadError("No se pudieron consultar las diapositivas parseadas.");
      });
    }, 2000);

    return () => window.clearInterval(interval);
  }, [presentationId, refreshSlides, uploadStatus]);

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0] ?? null;
    setUploadError(null);
    setPresentationId(null);
    setJobId(null);
    setSlides([]);

    if (!file) {
      setSelectedFile(null);
      setUploadStatus("idle");
      return;
    }

    if (!isAllowedPresentation(file)) {
      setSelectedFile(null);
      setUploadStatus("failed");
      setUploadError("Solo se permiten archivos .ppt o .pptx.");
      return;
    }

    setSelectedFile(file);
    setUploadStatus("selected");
  }

  async function handleUpload() {
    if (!selectedFile) return;

    setUploadError(null);
    setSlides([]);
    try {
      setUploadStatus("initializing");
      const init = await api.presentations.initUpload(projectId, {
        filename: selectedFile.name,
        content_type: selectedFile.type || "application/octet-stream",
      });
      setPresentationId(init.presentation_id);

      setUploadStatus("uploading");
      await api.presentations.uploadFile(init.presentation_id, selectedFile);

      setUploadStatus("confirming");
      const confirmed = await api.presentations.confirmUpload(init.presentation_id);
      setJobId(confirmed.job_id);
      setUploadStatus("processing");
      await refreshSlides(init.presentation_id);
    } catch (err) {
      setUploadStatus("failed");
      setUploadError(err instanceof Error ? err.message : "No se pudo subir la presentacion.");
    }
  }

  return (
    <AppShell title="Proyecto">
      <div className="mb-6">
        <Link href="/dashboard" className="text-sm font-medium text-brand-700 hover:text-brand-800">
          Volver al dashboard
        </Link>
      </div>

      {isLoading ? (
        <div className="rounded-lg border border-gray-200 bg-white p-6 text-sm text-gray-500 shadow-sm">
          Cargando proyecto...
        </div>
      ) : error ? (
        <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : project ? (
        <div className="space-y-6">
          <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
            <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h2 className="text-xl font-semibold text-gray-900">{project.name}</h2>
                <p className="mt-1 text-sm text-gray-500">
                  {project.description || "Sin descripcion"}
                </p>
              </div>
              <span className="w-fit rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700">
                {project.status}
              </span>
            </div>

            <dl className="mt-6 grid gap-4 sm:grid-cols-2">
              <div>
                <dt className="text-xs font-medium uppercase text-gray-500">Creado</dt>
                <dd className="mt-1 text-sm text-gray-900">{formatDate(project.created_at)}</dd>
              </div>
              <div>
                <dt className="text-xs font-medium uppercase text-gray-500">Actualizado</dt>
                <dd className="mt-1 text-sm text-gray-900">{formatDate(project.updated_at)}</dd>
              </div>
            </dl>
          </section>

          <section className="rounded-lg border border-gray-200 bg-white p-6 shadow-sm">
            <div className="flex flex-col gap-2 sm:flex-row sm:items-start sm:justify-between">
              <div>
                <h3 className="text-sm font-semibold text-gray-900">Presentation Upload</h3>
                <p className="mt-1 text-sm text-gray-500">
                  Sube un archivo PPT o PPTX para extraer sus diapositivas.
                </p>
              </div>
              {presentationId && slides.length > 0 ? (
                <Link
                  href={`/projects/${project.id}/editor?presentationId=${presentationId}`}
                  className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700"
                >
                  Abrir editor
                </Link>
              ) : null}
            </div>

            <div className="mt-5 flex flex-col gap-4">
              <input
                type="file"
                accept=".ppt,.pptx,application/vnd.ms-powerpoint,application/vnd.openxmlformats-officedocument.presentationml.presentation"
                onChange={handleFileChange}
                className="block w-full text-sm text-gray-700 file:mr-4 file:rounded-md file:border-0 file:bg-gray-900 file:px-4 file:py-2 file:text-sm file:font-semibold file:text-white hover:file:bg-gray-800"
              />

              {selectedFile ? (
                <div className="rounded-lg bg-gray-50 px-4 py-3 text-sm text-gray-700">
                  <span className="font-medium">{selectedFile.name}</span>
                  <span className="ml-2 text-gray-500">
                    {(selectedFile.size / 1024 / 1024).toFixed(2)} MB
                  </span>
                </div>
              ) : null}

              <div className="flex flex-col gap-3 sm:flex-row sm:items-center">
                <button
                  type="button"
                  onClick={handleUpload}
                  disabled={!selectedFile || ["initializing", "uploading", "confirming", "processing"].includes(uploadStatus)}
                  className="w-fit rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700 disabled:cursor-not-allowed disabled:bg-gray-400"
                >
                  Subir presentacion
                </button>
                {presentationId ? (
                  <button
                    type="button"
                    onClick={() => void refreshSlides()}
                    className="w-fit rounded-md border border-gray-300 px-4 py-2 text-sm font-semibold text-gray-700 transition-colors hover:bg-gray-50"
                  >
                    Revisar slides
                  </button>
                ) : null}
              </div>

              <div className="rounded-lg border border-gray-200 px-4 py-3 text-sm text-gray-700">
                {uploadStatusLabels[uploadStatus]}
                {presentationId ? (
                  <div className="mt-1 text-xs text-gray-500">presentation_id: {presentationId}</div>
                ) : null}
                {jobId ? <div className="mt-1 text-xs text-gray-500">job_id: {jobId}</div> : null}
                {slides.length > 0 ? (
                  <div className="mt-1 text-xs text-green-700">
                    {slides.length} slide{slides.length === 1 ? "" : "s"} disponible
                    {slides.length === 1 ? "" : "s"}.
                  </div>
                ) : null}
              </div>

              {uploadError ? (
                <div className="rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
                  {uploadError}
                </div>
              ) : null}
            </div>
          </section>

        </div>
      ) : null}
    </AppShell>
  );
}
