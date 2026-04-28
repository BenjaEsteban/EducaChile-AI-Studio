"use client";

import Link from "next/link";
import { FormEvent, useEffect, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { Project, api } from "@/lib/api";

const stats = [
  { label: "Videos generados", value: "0" },
  { label: "Presentaciones subidas", value: "0" },
  { label: "Tareas en cola", value: "0" },
];

export default function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [isCreating, setIsCreating] = useState(false);
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");

  async function loadProjects() {
    try {
      setError(null);
      const data = await api.projects.list();
      setProjects(data.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudieron cargar los proyectos.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadProjects();
  }, []);

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) return;

    setIsCreating(true);
    setError(null);
    try {
      await api.projects.create({
        name: trimmedName,
        description: description.trim() || null,
      });
      setName("");
      setDescription("");
      setIsFormOpen(false);
      await loadProjects();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear el proyecto.");
    } finally {
      setIsCreating(false);
    }
  }

  return (
    <AppShell title="Dashboard">
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Proyectos</h2>
          <p className="mt-1 text-sm text-gray-500">
            Crea y gestiona proyectos para convertir presentaciones en videos.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setIsFormOpen((value) => !value)}
          className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700"
        >
          {isFormOpen ? "Cancelar" : "Crear proyecto"}
        </button>
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
        {stats.map(({ label, value }) => (
          <div key={label} className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            <p className="text-sm text-gray-500">{label}</p>
            <p className="mt-1 text-3xl font-bold text-gray-900">{value}</p>
          </div>
        ))}
      </div>

      {isFormOpen ? (
        <form
          onSubmit={handleCreateProject}
          className="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
        >
          <div className="grid gap-4 md:grid-cols-[1fr_1fr_auto] md:items-end">
            <label className="block">
              <span className="text-sm font-medium text-gray-700">Nombre</span>
              <input
                value={name}
                onChange={(event) => setName(event.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                placeholder="Clase de biologia"
                maxLength={255}
                required
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-gray-700">Descripcion</span>
              <input
                value={description}
                onChange={(event) => setDescription(event.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                placeholder="Unidad, curso o contexto"
              />
            </label>
            <button
              type="submit"
              disabled={isCreating}
              className="rounded-md bg-gray-900 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-400"
            >
              {isCreating ? "Creando..." : "Crear"}
            </button>
          </div>
        </form>
      ) : null}

      {error ? (
        <div className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}

      <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
        <div className="border-b border-gray-200 px-5 py-4">
          <h3 className="text-sm font-semibold text-gray-900">Lista de proyectos</h3>
        </div>

        {isLoading ? (
          <div className="px-5 py-10 text-sm text-gray-500">Cargando proyectos...</div>
        ) : projects.length === 0 ? (
          <div className="px-5 py-10">
            <p className="text-sm font-medium text-gray-900">No hay proyectos todavia.</p>
            <p className="mt-1 text-sm text-gray-500">
              Crea el primer proyecto para comenzar el flujo de presentaciones.
            </p>
          </div>
        ) : (
          <div className="divide-y divide-gray-200">
            {projects.map((project) => (
              <Link
                key={project.id}
                href={`/projects/${project.id}`}
                className="block px-5 py-4 transition-colors hover:bg-gray-50"
              >
                <div className="flex flex-col gap-2 sm:flex-row sm:items-center sm:justify-between">
                  <div>
                    <p className="text-sm font-semibold text-gray-900">{project.name}</p>
                    <p className="mt-1 text-sm text-gray-500">
                      {project.description || "Sin descripcion"}
                    </p>
                  </div>
                  <span className="w-fit rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700">
                    {project.status}
                  </span>
                </div>
              </Link>
            ))}
          </div>
        )}
      </section>
    </AppShell>
  );
}
