"use client";

import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Project, api } from "@/lib/api";

function formatDate(value: string) {
  return new Intl.DateTimeFormat("es-CL", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value));
}

export default function ProjectDetailPage() {
  const params = useParams<{ projectId: string }>();
  const projectId = params.projectId;
  const [project, setProject] = useState<Project | null>(null);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

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

          <section className="rounded-lg border border-dashed border-gray-300 bg-white p-6">
            <h3 className="text-sm font-semibold text-gray-900">Presentation Upload</h3>
            <p className="mt-2 text-sm text-gray-500">
              La carga de presentaciones se implementara en el siguiente paso.
            </p>
          </section>
        </div>
      ) : null}
    </AppShell>
  );
}
