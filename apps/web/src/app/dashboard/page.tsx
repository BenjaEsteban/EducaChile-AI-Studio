"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { ApiError, FolderTreeNode, Project, api } from "@/lib/api";

const stats = [
  { label: "Videos generados", value: "0" },
  { label: "Presentaciones subidas", value: "0" },
  { label: "Tareas en cola", value: "0" },
];

const ALL_SCOPE = "__all__";

type FolderOption = { id: string; name: string; depth: number };

function flattenFolderTree(nodes: FolderTreeNode[], depth = 0): FolderOption[] {
  const rows: FolderOption[] = [];
  for (const node of nodes) {
    rows.push({ id: node.id, name: node.name, depth });
    rows.push(...flattenFolderTree(node.children, depth + 1));
  }
  return rows;
}

function folderDescendants(nodes: FolderTreeNode[], targetId: string): string[] {
  function walk(node: FolderTreeNode): string[] | null {
    if (node.id === targetId) {
      return [node.id, ...node.children.flatMap((child) => walkAll(child))];
    }
    for (const child of node.children) {
      const found = walk(child);
      if (found) return found;
    }
    return null;
  }

  function walkAll(node: FolderTreeNode): string[] {
    return [node.id, ...node.children.flatMap((child) => walkAll(child))];
  }

  for (const root of nodes) {
    const result = walk(root);
    if (result) return result;
  }
  return [];
}

export default function DashboardPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [folders, setFolders] = useState<FolderTreeNode[]>([]);
  const [isLoading, setIsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [selectedScope, setSelectedScope] = useState<string>(ALL_SCOPE);

  const [isProjectFormOpen, setIsProjectFormOpen] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [projectFolderId, setProjectFolderId] = useState<string | null>(null);
  const [isCreatingProject, setIsCreatingProject] = useState(false);
  const [movingProjectId, setMovingProjectId] = useState<string | null>(null);
  const [projectPendingDelete, setProjectPendingDelete] = useState<Project | null>(null);
  const [isDeletingProject, setIsDeletingProject] = useState(false);

  const [isFolderFormOpen, setIsFolderFormOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [folderParentId, setFolderParentId] = useState<string | null>(null);
  const [isSavingFolder, setIsSavingFolder] = useState(false);
  const [openFolderMenuId, setOpenFolderMenuId] = useState<string | null>(null);

  const folderOptions = useMemo(() => flattenFolderTree(folders), [folders]);
  const folderNameById = useMemo(() => {
    const pairs = folderOptions.map((folder) => [folder.id, folder.name] as const);
    return new Map<string, string>(pairs);
  }, [folderOptions]);
  const projectsByFolder = useMemo(() => {
    const map = new Map<string, Project[]>();
    for (const project of projects) {
      if (!project.folder_id) continue;
      if (!map.has(project.folder_id)) map.set(project.folder_id, []);
      map.get(project.folder_id)!.push(project);
    }
    return map;
  }, [projects]);

  const filteredProjects = useMemo(() => {
    if (selectedScope === ALL_SCOPE) return projects;
    const validFolderIds = new Set(folderDescendants(folders, selectedScope));
    return projects.filter((project) => project.folder_id && validFolderIds.has(project.folder_id));
  }, [folders, projects, selectedScope]);

  const selectedScopeLabel = useMemo(() => {
    if (selectedScope === ALL_SCOPE) return "Todos los proyectos";
    const found = folderOptions.find((option) => option.id === selectedScope);
    return found ? `Carpeta: ${found.name}` : "Carpeta seleccionada";
  }, [folderOptions, selectedScope]);

  async function loadDashboard() {
    try {
      setError(null);
      const [projectsData, foldersData] = await Promise.all([
        api.projects.list(),
        api.projects.listFolderTree(),
      ]);
      setProjects(projectsData.items);
      setFolders(foldersData.items);
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo cargar el dashboard.");
    } finally {
      setIsLoading(false);
    }
  }

  useEffect(() => {
    void loadDashboard();
  }, []);

  useEffect(() => {
    if (!openFolderMenuId) return;
    function handleClickOutside(event: MouseEvent) {
      const target = event.target as HTMLElement | null;
      if (target?.closest("[data-folder-menu-root='true']")) return;
      setOpenFolderMenuId(null);
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, [openFolderMenuId]);

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = projectName.trim();
    if (!trimmedName) return;
    setIsCreatingProject(true);
    setError(null);
    setNotice(null);
    try {
      await api.projects.create({
        name: trimmedName,
        description: projectDescription.trim() || null,
        folder_id: projectFolderId,
      });
      setProjectName("");
      setProjectDescription("");
      setIsProjectFormOpen(false);
      setNotice("Proyecto creado correctamente.");
      await loadDashboard();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear el proyecto.");
    } finally {
      setIsCreatingProject(false);
    }
  }

  function openProjectForm(folderId: string | null) {
    setProjectFolderId(folderId);
    setIsProjectFormOpen(true);
  }

  async function handleCreateFolder(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = folderName.trim();
    if (!trimmedName) return;
    setIsSavingFolder(true);
    setError(null);
    setNotice(null);
    try {
      await api.projects.createFolder({
        name: trimmedName,
        parent_folder_id: folderParentId,
      });
      setFolderName("");
      setFolderParentId(null);
      setIsFolderFormOpen(false);
      setNotice("Carpeta creada correctamente.");
      await loadDashboard();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo crear la carpeta.");
    } finally {
      setIsSavingFolder(false);
    }
  }

  function openSubfolderForm(parentFolderId: string) {
    setFolderParentId(parentFolderId);
    setFolderName("");
    setIsFolderFormOpen(true);
  }

  async function handleRenameFolder(folderId: string, currentName: string) {
    const nextName = window.prompt("Nuevo nombre de la carpeta", currentName)?.trim();
    if (!nextName || nextName === currentName) return;
    setError(null);
    try {
      await api.projects.renameFolder(folderId, nextName);
      setNotice("Carpeta renombrada.");
      await loadDashboard();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo renombrar la carpeta.");
    }
  }

  async function handleDeleteFolder(folderId: string) {
    const confirmed = window.confirm("¿Eliminar esta carpeta?");
    if (!confirmed) return;
    setError(null);
    setNotice(null);
    try {
      await api.projects.deleteFolder(folderId, false);
      setNotice("Carpeta eliminada.");
      await loadDashboard();
      return;
    } catch (err) {
      const isNotEmpty =
        err instanceof ApiError &&
        err.status === 409 &&
        (err.errorCode === "FOLDER_NOT_EMPTY" || err.message.includes("contains subfolders"));
      if (!isNotEmpty) {
        setError(err instanceof Error ? err.message : "No se pudo eliminar la carpeta.");
        return;
      }
    }

    const cascadeConfirmed = window.confirm(
      "La carpeta contiene subcarpetas o proyectos. ¿Eliminar todo su contenido?",
    );
    if (!cascadeConfirmed) return;
    try {
      await api.projects.deleteFolder(folderId, true);
      setNotice("Carpeta y contenido eliminados.");
      await loadDashboard();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo eliminar la carpeta.");
    }
  }

  async function handleMoveProject(projectId: string, nextFolderId: string | null) {
    setMovingProjectId(projectId);
    setError(null);
    setNotice(null);
    try {
      await api.projects.move(projectId, nextFolderId);
      setNotice("Proyecto movido correctamente.");
      await loadDashboard();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo mover el proyecto.");
    } finally {
      setMovingProjectId(null);
    }
  }

  async function handleDeleteProject() {
    if (!projectPendingDelete) return;
    setIsDeletingProject(true);
    setError(null);
    setNotice(null);
    try {
      await api.projects.delete(projectPendingDelete.id);
      setNotice(`Proyecto "${projectPendingDelete.name}" eliminado.`);
      setProjectPendingDelete(null);
      await loadDashboard();
    } catch (err) {
      setError(err instanceof Error ? err.message : "No se pudo eliminar el proyecto.");
    } finally {
      setIsDeletingProject(false);
    }
  }

  function formatDate(iso: string) {
    const date = new Date(iso);
    return new Intl.DateTimeFormat("es-CL", {
      day: "2-digit",
      month: "short",
      year: "numeric",
    }).format(date);
  }

  function renderFolderNodes(nodes: FolderTreeNode[], depth = 0): JSX.Element[] {
    return nodes.flatMap((node) => {
      const folderProjects = projectsByFolder.get(node.id) ?? [];
      const isSelected = selectedScope === node.id;
      const item = (
        <li key={node.id}>
          <div
            className={`rounded-md border px-3 py-2 ${
              isSelected
                ? "border-brand-300 bg-brand-50 ring-1 ring-brand-200"
                : "border-gray-200 bg-white hover:border-gray-300 hover:bg-gray-50"
            }`}
            style={{ marginLeft: `${depth * 12}px` }}
          >
            <div className="flex items-center justify-between gap-3">
              <button
                type="button"
                onClick={() => setSelectedScope(node.id)}
                className="min-w-0 text-left"
              >
                <p className="truncate text-sm font-medium text-gray-900">{node.name}</p>
                <p className="mt-0.5 text-xs text-gray-500">
                  {(folderProjects.length || 0).toString()} proyecto
                  {folderProjects.length === 1 ? "" : "s"}
                </p>
              </button>
              <div className="relative shrink-0" data-folder-menu-root="true">
                <button
                  type="button"
                  onClick={() => setOpenFolderMenuId((prev) => (prev === node.id ? null : node.id))}
                  className="rounded-md border border-gray-200 px-2 py-1 text-sm text-gray-600 hover:bg-gray-100 hover:text-gray-800"
                  aria-label={`Acciones para carpeta ${node.name}`}
                >
                  &#8942;
                </button>
                {openFolderMenuId === node.id ? (
                  <div className="absolute right-0 z-20 mt-1 w-44 rounded-md border border-gray-200 bg-white py-1 shadow-lg">
                    <button
                      type="button"
                      onClick={() => {
                        setOpenFolderMenuId(null);
                        openSubfolderForm(node.id);
                      }}
                      className="block w-full px-3 py-2 text-left text-xs font-medium text-gray-700 hover:bg-gray-50"
                    >
                      Crear subcarpeta
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setOpenFolderMenuId(null);
                        void handleRenameFolder(node.id, node.name);
                      }}
                      className="block w-full px-3 py-2 text-left text-xs font-medium text-gray-700 hover:bg-gray-50"
                    >
                      Renombrar carpeta
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setOpenFolderMenuId(null);
                        void handleDeleteFolder(node.id);
                      }}
                      className="block w-full px-3 py-2 text-left text-xs font-medium text-red-700 hover:bg-red-50"
                    >
                      Eliminar carpeta
                    </button>
                  </div>
                ) : null}
              </div>
            </div>
            {folderProjects.length > 0 ? (
              <ul className="mt-2 space-y-1 border-t border-gray-100 pt-2">
                {folderProjects.map((project) => (
                  <li key={project.id}>
                    <Link
                      href={`/projects/${project.id}`}
                      className="block truncate text-xs text-gray-600 hover:text-brand-700"
                    >
                      {project.name}
                    </Link>
                  </li>
                ))}
              </ul>
            ) : null}
          </div>
        </li>
      );
      return [item, ...renderFolderNodes(node.children, depth + 1)];
    });
  }

  return (
    <AppShell title="Dashboard">
      <div className="mb-6 flex flex-col gap-4 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Proyectos</h2>
          <p className="mt-1 text-sm text-gray-500">
            Organiza proyectos en carpetas y genera videos desde presentaciones.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button
            type="button"
            onClick={() => {
              setFolderParentId(null);
              setFolderName("");
              setIsFolderFormOpen((value) => !value);
            }}
            className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
          >
            {isFolderFormOpen ? "Cancelar carpeta" : "Crear carpeta"}
          </button>
          <button
            type="button"
            onClick={() =>
              openProjectForm(
                selectedScope !== ALL_SCOPE ? selectedScope : null,
              )
            }
            className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition-colors hover:bg-brand-700"
          >
            {isProjectFormOpen ? "Editando formulario" : "Crear proyecto"}
          </button>
        </div>
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 sm:grid-cols-3">
        {stats.map(({ label, value }) => (
          <div key={label} className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
            <p className="text-sm text-gray-500">{label}</p>
            <p className="mt-1 text-3xl font-bold text-gray-900">{value}</p>
          </div>
        ))}
      </div>

      {isFolderFormOpen ? (
        <form
          onSubmit={handleCreateFolder}
          className="mb-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
        >
          <div className="grid gap-4 md:grid-cols-[1fr_1fr_auto] md:items-end">
            <label className="block">
              <span className="text-sm font-medium text-gray-700">Nombre de carpeta</span>
              <input
                value={folderName}
                onChange={(event) => setFolderName(event.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                placeholder="Unidad 1"
                maxLength={255}
                required
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-gray-700">Carpeta padre</span>
              <select
                value={folderParentId ?? ""}
                onChange={(event) => setFolderParentId(event.target.value || null)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
              >
                <option value="">Raíz</option>
                {folderOptions.map((folder) => (
                  <option key={folder.id} value={folder.id}>
                    {"\u00A0".repeat(folder.depth * 2)}
                    {folder.name}
                  </option>
                ))}
              </select>
            </label>
            <button
              type="submit"
              disabled={isSavingFolder}
              className="rounded-md bg-gray-900 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-400"
            >
              {isSavingFolder ? "Guardando..." : "Guardar carpeta"}
            </button>
          </div>
        </form>
      ) : null}

      {isProjectFormOpen ? (
        <form
          onSubmit={handleCreateProject}
          className="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm"
        >
          <div className="grid gap-4 md:grid-cols-2">
            <label className="block">
              <span className="text-sm font-medium text-gray-700">Nombre</span>
              <input
                value={projectName}
                onChange={(event) => setProjectName(event.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                placeholder="Clase de biología"
                maxLength={255}
                required
              />
            </label>
            <label className="block">
              <span className="text-sm font-medium text-gray-700">Carpeta</span>
              <select
                value={projectFolderId ?? ""}
                onChange={(event) => setProjectFolderId(event.target.value || null)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
              >
                <option value="">Sin carpeta</option>
                {folderOptions.map((folder) => (
                  <option key={folder.id} value={folder.id}>
                    {"\u00A0".repeat(folder.depth * 2)}
                    {folder.name}
                  </option>
                ))}
              </select>
            </label>
            <label className="block md:col-span-2">
              <span className="text-sm font-medium text-gray-700">Descripción</span>
              <input
                value={projectDescription}
                onChange={(event) => setProjectDescription(event.target.value)}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                placeholder="Unidad, curso o contexto"
              />
            </label>
          </div>
          <div className="mt-4 flex items-center justify-end gap-2">
            <button
              type="button"
              onClick={() => setIsProjectFormOpen(false)}
              className="rounded-md border border-gray-300 bg-white px-4 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
            >
              Cancelar
            </button>
            <button
              type="submit"
              disabled={isCreatingProject}
              className="rounded-md bg-gray-900 px-4 py-2 text-sm font-semibold text-white transition-colors hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-400"
            >
              {isCreatingProject ? "Creando..." : "Crear proyecto"}
            </button>
          </div>
        </form>
      ) : null}

      {error ? (
        <div className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          {error}
        </div>
      ) : null}
      {notice ? (
        <div className="mb-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">
          {notice}
        </div>
      ) : null}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-[340px_1fr]">
        <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-200 px-5 py-4">
            <h3 className="text-sm font-semibold text-gray-900">Carpetas</h3>
          </div>
          <div className="space-y-2 px-4 py-4">
            <button
              type="button"
              onClick={() => setSelectedScope(ALL_SCOPE)}
              className={`w-full rounded-md px-3 py-2 text-left text-sm ${
                selectedScope === ALL_SCOPE
                  ? "bg-brand-50 font-medium text-brand-800 ring-1 ring-brand-200"
                  : "text-gray-700 hover:bg-gray-50"
              }`}
            >
              Todos los proyectos
            </button>
            {isLoading ? (
              <div className="space-y-2 pt-1">
                <div className="h-10 animate-pulse rounded-md bg-gray-100" />
                <div className="h-10 animate-pulse rounded-md bg-gray-100" />
                <div className="h-10 animate-pulse rounded-md bg-gray-100" />
              </div>
            ) : folders.length === 0 ? (
              <p className="rounded-md border border-dashed border-gray-200 px-3 py-4 text-xs text-gray-500">
                No hay carpetas creadas.
              </p>
            ) : (
              <ul className="space-y-2">{renderFolderNodes(folders)}</ul>
            )}
          </div>
        </section>

        <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="flex items-center justify-between border-b border-gray-200 px-5 py-4">
            <h3 className="text-sm font-semibold text-gray-900">{selectedScopeLabel}</h3>
            <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700">
              {filteredProjects.length}
            </span>
          </div>

          {isLoading ? (
            <div className="px-5 py-10 text-sm text-gray-500">Cargando dashboard...</div>
          ) : filteredProjects.length === 0 ? (
            <div className="px-5 py-10">
              <p className="text-sm font-medium text-gray-900">No hay proyectos en esta vista.</p>
              <p className="mt-1 text-sm text-gray-500">
                Crea un proyecto o mueve uno existente a esta carpeta.
              </p>
            </div>
          ) : (
            <div className="grid grid-cols-1 gap-3 p-4 xl:grid-cols-2">
              {filteredProjects.map((project) => (
                <article
                  key={project.id}
                  className="rounded-lg border border-gray-200 bg-white p-4 transition hover:border-brand-200 hover:shadow-sm"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <Link href={`/projects/${project.id}`} className="block">
                        <p className="truncate text-sm font-semibold text-gray-900 hover:text-brand-700">
                          {project.name}
                        </p>
                      </Link>
                      <p className="mt-1 truncate text-sm text-gray-500">
                        {project.description || "Sin descripción"}
                      </p>
                      <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-500">
                        <span className="rounded-full bg-gray-100 px-2 py-1">
                          {project.folder_id ? folderNameById.get(project.folder_id) ?? "Carpeta" : "Sin carpeta"}
                        </span>
                        <span>Actualizado: {formatDate(project.updated_at)}</span>
                      </div>
                    </div>
                    <div className="flex items-center gap-2">
                      <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700">
                        {project.status}
                      </span>
                    </div>
                  </div>
                  <div className="mt-3 flex flex-wrap items-center gap-2 border-t border-gray-100 pt-3">
                    <label className="text-xs text-gray-500">Mover a</label>
                    <select
                      value={project.folder_id ?? ""}
                      onChange={(event) => void handleMoveProject(project.id, event.target.value || null)}
                      disabled={movingProjectId === project.id}
                      className="max-w-[220px] rounded-md border border-gray-300 px-2 py-1.5 text-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100 disabled:cursor-not-allowed disabled:bg-gray-100"
                    >
                      <option value="">Sin carpeta</option>
                      {folderOptions.map((folder) => (
                        <option key={folder.id} value={folder.id}>
                          {"\u00A0".repeat(folder.depth * 2)}
                          {folder.name}
                        </option>
                      ))}
                    </select>
                    <Link
                      href={`/projects/${project.id}`}
                      className="rounded-md border border-gray-300 px-2.5 py-1.5 text-xs font-medium text-gray-700 hover:bg-gray-50"
                    >
                      Abrir
                    </Link>
                    <button
                      type="button"
                      onClick={() => setProjectPendingDelete(project)}
                      className="rounded-md border border-red-200 px-2.5 py-1.5 text-xs font-medium text-red-700 hover:bg-red-50"
                    >
                      Eliminar
                    </button>
                  </div>
                </article>
              ))}
            </div>
          )}
        </section>
      </div>

      {projectPendingDelete ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4">
          <div className="w-full max-w-md rounded-lg border border-gray-200 bg-white p-5 shadow-xl">
            <h4 className="text-base font-semibold text-gray-900">Eliminar proyecto</h4>
            <p className="mt-2 text-sm text-gray-600">
              Vas a eliminar <span className="font-medium text-gray-900">{projectPendingDelete.name}</span>. Esta
              acción no se puede deshacer.
            </p>
            <p className="mt-1 text-sm text-red-700">
              También se eliminarán presentaciones, assets y configuraciones asociadas al proyecto.
            </p>
            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                type="button"
                disabled={isDeletingProject}
                onClick={() => setProjectPendingDelete(null)}
                className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:bg-gray-100"
              >
                Cancelar
              </button>
              <button
                type="button"
                disabled={isDeletingProject}
                onClick={() => void handleDeleteProject()}
                className="rounded-md bg-red-600 px-3 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-300"
              >
                {isDeletingProject ? "Eliminando..." : "Eliminar proyecto"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </AppShell>
  );
}
