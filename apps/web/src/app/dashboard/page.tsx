"use client";

import Link from "next/link";
import { FormEvent, useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/layout/AppShell";
import { ApiError, FolderTreeNode, Project, api } from "@/lib/api";

const ALL_SCOPE = "__all__";

type FolderOption = { id: string; name: string; depth: number };
type SortMode = "name" | "created" | "updated";
type ViewMode = "grid" | "list";
type DeleteTarget = { kind: "folder" | "project"; id: string; name: string };

function flattenFolderTree(nodes: FolderTreeNode[], depth = 0): FolderOption[] {
  const rows: FolderOption[] = [];
  for (const node of nodes) {
    rows.push({ id: node.id, name: node.name, depth });
    rows.push(...flattenFolderTree(node.children, depth + 1));
  }
  return rows;
}

function flattenFolderNodes(nodes: FolderTreeNode[]): FolderTreeNode[] {
  const output: FolderTreeNode[] = [];
  for (const node of nodes) {
    output.push(node);
    output.push(...flattenFolderNodes(node.children));
  }
  return output;
}

function filterTree(nodes: FolderTreeNode[], query: string): FolderTreeNode[] {
  if (!query.trim()) return nodes;
  const normalized = query.trim().toLowerCase();
  const walk = (node: FolderTreeNode): FolderTreeNode | null => {
    const children = node.children
      .map((child) => walk(child))
      .filter((child): child is FolderTreeNode => Boolean(child));
    const matches = node.name.toLowerCase().includes(normalized);
    if (!matches && children.length === 0) return null;
    return { ...node, children };
  };
  return nodes
    .map((node) => walk(node))
    .filter((node): node is FolderTreeNode => Boolean(node));
}

function formatDate(iso: string) {
  const date = new Date(iso);
  return new Intl.DateTimeFormat("es-CL", {
    day: "2-digit",
    month: "short",
    year: "numeric",
  }).format(date);
}

function sortFolders(items: FolderTreeNode[], mode: SortMode): FolderTreeNode[] {
  return [...items].sort((a, b) => {
    if (mode === "created") return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    if (mode === "updated") return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
    return a.name.localeCompare(b.name, "es", { sensitivity: "base" });
  });
}

function sortProjects(items: Project[], mode: SortMode): Project[] {
  return [...items].sort((a, b) => {
    if (mode === "created") return new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
    if (mode === "updated") return new Date(b.updated_at).getTime() - new Date(a.updated_at).getTime();
    return a.name.localeCompare(b.name, "es", { sensitivity: "base" });
  });
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

  const [isFolderFormOpen, setIsFolderFormOpen] = useState(false);
  const [folderName, setFolderName] = useState("");
  const [folderParentId, setFolderParentId] = useState<string | null>(null);
  const [isSavingFolder, setIsSavingFolder] = useState(false);
  const [openFolderMenuId, setOpenFolderMenuId] = useState<string | null>(null);
  const [openProjectMenuId, setOpenProjectMenuId] = useState<string | null>(null);
  const [isNewMenuOpen, setIsNewMenuOpen] = useState(false);
  const [folderRenameTarget, setFolderRenameTarget] = useState<{ id: string; currentName: string } | null>(null);
  const [renameFolderValue, setRenameFolderValue] = useState("");
  const [isRenamingFolder, setIsRenamingFolder] = useState(false);
  const [renameFolderError, setRenameFolderError] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<DeleteTarget | null>(null);
  const [deleteConfirmStep, setDeleteConfirmStep] = useState<1 | 2>(1);
  const [deleteConfirmValue, setDeleteConfirmValue] = useState("");
  const [deleteNeedsCascade, setDeleteNeedsCascade] = useState(false);
  const [isDeletingTarget, setIsDeletingTarget] = useState(false);
  const [deleteDialogError, setDeleteDialogError] = useState<string | null>(null);
  const [draggingProjectId, setDraggingProjectId] = useState<string | null>(null);
  const [dragOverFolderId, setDragOverFolderId] = useState<string | null>(null);
  const [expandedFolderIds, setExpandedFolderIds] = useState<Set<string>>(new Set());

  const [folderQuery, setFolderQuery] = useState("");
  const [projectQuery, setProjectQuery] = useState("");
  const [sortMode, setSortMode] = useState<SortMode>("name");
  const [viewMode, setViewMode] = useState<ViewMode>("grid");

  const folderOptions = useMemo(() => flattenFolderTree(folders), [folders]);
  const folderNameById = useMemo(
    () => new Map<string, string>(folderOptions.map((folder) => [folder.id, folder.name] as const)),
    [folderOptions],
  );

  const { folderMap, parentMap } = useMemo(() => {
    const nodes = flattenFolderNodes(folders);
    const map = new Map<string, FolderTreeNode>();
    const parents = new Map<string, string | null>();
    for (const node of nodes) {
      map.set(node.id, node);
      parents.set(node.id, node.parent_folder_id);
    }
    return { folderMap: map, parentMap: parents };
  }, [folders]);

  const selectedFolder = selectedScope !== ALL_SCOPE ? folderMap.get(selectedScope) ?? null : null;

  const breadcrumb = useMemo(() => {
    if (!selectedFolder) return [];
    const chain: FolderTreeNode[] = [];
    let cursor: FolderTreeNode | null = selectedFolder;
    while (cursor) {
      chain.unshift(cursor);
      const parentId = parentMap.get(cursor.id);
      cursor = parentId ? folderMap.get(parentId) ?? null : null;
    }
    return chain;
  }, [folderMap, parentMap, selectedFolder]);

  const projectsByFolder = useMemo(() => {
    const map = new Map<string, Project[]>();
    for (const project of projects) {
      if (!project.folder_id) continue;
      if (!map.has(project.folder_id)) map.set(project.folder_id, []);
      map.get(project.folder_id)!.push(project);
    }
    return map;
  }, [projects]);

  const sidebarTree = useMemo(() => filterTree(folders, folderQuery), [folders, folderQuery]);
  const folderCandidates = useMemo(() => {
    if (selectedScope === ALL_SCOPE) return folders;
    return selectedFolder?.children ?? [];
  }, [folders, selectedScope, selectedFolder]);

  const visibleFolders = useMemo(() => {
    const query = folderQuery.trim().toLowerCase();
    const filtered = query
      ? folderCandidates.filter((folder) => folder.name.toLowerCase().includes(query))
      : folderCandidates;
    return sortFolders(filtered, sortMode);
  }, [folderCandidates, folderQuery, sortMode]);

  const selectedScopeLabel = useMemo(() => {
    if (selectedScope === ALL_SCOPE) return "Todas las carpetas";
    return selectedFolder ? selectedFolder.name : "Carpeta";
  }, [selectedScope, selectedFolder]);
  const isFolderScope = selectedScope !== ALL_SCOPE;

  const filteredProjects = useMemo(() => {
    const byScope =
      selectedScope === ALL_SCOPE
        ? projects
        : projects.filter((project) => project.folder_id === selectedScope);
    const query = projectQuery.trim().toLowerCase();
    const queried = query
      ? byScope.filter(
          (project) =>
            project.name.toLowerCase().includes(query) ||
            (project.description ?? "").toLowerCase().includes(query),
        )
      : byScope;
    return sortProjects(queried, sortMode);
  }, [projects, selectedScope, projectQuery, sortMode]);

  async function loadDashboard() {
    try {
      setError(null);
      const [projectsData, foldersData] = await Promise.all([api.projects.list(), api.projects.listFolderTree()]);
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
    if (selectedScope === ALL_SCOPE) return;
    if (!folderMap.has(selectedScope)) setSelectedScope(ALL_SCOPE);
  }, [folderMap, selectedScope]);

  useEffect(() => {
    if (selectedScope === ALL_SCOPE) return;
    if (breadcrumb.length <= 1) return;
    setExpandedFolderIds((prev) => {
      const next = new Set(prev);
      breadcrumb.slice(0, -1).forEach((node) => next.add(node.id));
      return next;
    });
  }, [breadcrumb, selectedScope]);

  useEffect(() => {
    const handleClickOutside = (event: MouseEvent) => {
      const target = event.target as HTMLElement | null;
      if (
        target?.closest("[data-folder-menu-root='true']") ||
        target?.closest("[data-project-menu-root='true']") ||
        target?.closest("[data-new-menu-root='true']")
      ) {
        return;
      }
      setOpenFolderMenuId(null);
      setOpenProjectMenuId(null);
      setIsNewMenuOpen(false);
    };
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  async function handleCreateProject(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmedName = projectName.trim();
    if (!trimmedName) return;
    setIsCreatingProject(true);
    setError(null);
    setNotice(null);
    try {
      await api.projects.create({ name: trimmedName, description: projectDescription.trim() || null, folder_id: projectFolderId });
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
      await api.projects.createFolder({ name: trimmedName, parent_folder_id: folderParentId });
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

  async function handleRenameFolder() {
    if (!folderRenameTarget) return;
    const nextName = renameFolderValue.trim();
    if (!nextName) {
      setRenameFolderError("El nombre no puede estar vacío.");
      return;
    }
    if (nextName === folderRenameTarget.currentName.trim()) {
      setFolderRenameTarget(null);
      setRenameFolderValue("");
      setRenameFolderError(null);
      return;
    }
    setIsRenamingFolder(true);
    setRenameFolderError(null);
    setError(null);
    try {
      await api.projects.renameFolder(folderRenameTarget.id, nextName);
      setNotice("Carpeta renombrada.");
      setFolderRenameTarget(null);
      setRenameFolderValue("");
      setRenameFolderError(null);
      await loadDashboard();
    } catch (err) {
      setRenameFolderError(err instanceof Error ? err.message : "No se pudo renombrar la carpeta.");
    } finally {
      setIsRenamingFolder(false);
    }
  }

  function openRenameFolderModal(folderId: string, currentName: string) {
    setFolderRenameTarget({ id: folderId, currentName });
    setRenameFolderValue(currentName);
    setRenameFolderError(null);
  }

  function openDeleteModal(target: DeleteTarget) {
    setDeleteTarget(target);
    setDeleteConfirmStep(1);
    setDeleteConfirmValue("");
    setDeleteNeedsCascade(false);
    setDeleteDialogError(null);
  }

  async function handleDeleteTarget() {
    if (!deleteTarget) return;
    if (deleteConfirmStep === 1) {
      setDeleteConfirmStep(2);
      setDeleteDialogError(null);
      return;
    }
    if (deleteConfirmValue.trim() !== deleteTarget.name.trim()) {
      setDeleteDialogError("Escribe el nombre exactamente para confirmar.");
      return;
    }

    setIsDeletingTarget(true);
    setError(null);
    setNotice(null);
    setDeleteDialogError(null);

    if (deleteTarget.kind === "project") {
      try {
        await api.projects.delete(deleteTarget.id);
        setNotice(`Proyecto "${deleteTarget.name}" eliminado.`);
        setDeleteTarget(null);
        await loadDashboard();
      } catch (err) {
        setDeleteDialogError(err instanceof Error ? err.message : "No se pudo eliminar el proyecto.");
      } finally {
        setIsDeletingTarget(false);
      }
      return;
    }

    try {
      await api.projects.deleteFolder(deleteTarget.id, deleteNeedsCascade);
      setNotice(deleteNeedsCascade ? "Carpeta y contenido eliminados." : "Carpeta eliminada.");
      setDeleteTarget(null);
      await loadDashboard();
    } catch (err) {
      const isNotEmpty =
        err instanceof ApiError &&
        err.status === 409 &&
        (err.errorCode === "FOLDER_NOT_EMPTY" || err.message.includes("contains subfolders"));
      if (isNotEmpty && !deleteNeedsCascade) {
        setDeleteNeedsCascade(true);
        setDeleteDialogError("La carpeta contiene subcarpetas o proyectos. Confirma nuevamente para eliminar todo su contenido.");
      } else {
        setDeleteDialogError(err instanceof Error ? err.message : "No se pudo eliminar la carpeta.");
      }
    } finally {
      setIsDeletingTarget(false);
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

  function handleProjectDragStart(projectId: string) {
    setDraggingProjectId(projectId);
  }

  function handleProjectDragEnd() {
    setDraggingProjectId(null);
    setDragOverFolderId(null);
  }

  async function handleDropProjectIntoFolder(targetFolderId: string) {
    if (!draggingProjectId || !targetFolderId) return;
    const project = projects.find((item) => item.id === draggingProjectId);
    if (project?.folder_id === targetFolderId) {
      setDraggingProjectId(null);
      setDragOverFolderId(null);
      return;
    }
    await handleMoveProject(draggingProjectId, targetFolderId);
    setDraggingProjectId(null);
    setDragOverFolderId(null);
  }

  function toggleFolderExpanded(folderId: string) {
    setExpandedFolderIds((prev) => {
      const next = new Set(prev);
      if (next.has(folderId)) next.delete(folderId);
      else next.add(folderId);
      return next;
    });
  }

  function renderSidebarTree(nodes: FolderTreeNode[], depth = 0): JSX.Element[] {
    return nodes.flatMap((node) => {
      const isSelected = selectedScope === node.id;
      const hasChildren = node.children.length > 0;
      const isExpanded = folderQuery.trim().length > 0 || expandedFolderIds.has(node.id);
      const isDropTarget = draggingProjectId !== null && dragOverFolderId === node.id;
      const row = (
        <li key={node.id}>
          <div
            onDragOver={(event) => {
              if (!draggingProjectId) return;
              event.preventDefault();
              event.dataTransfer.dropEffect = "move";
              if (dragOverFolderId !== node.id) setDragOverFolderId(node.id);
            }}
            onDragLeave={() => {
              if (dragOverFolderId === node.id) setDragOverFolderId(null);
            }}
            onDrop={(event) => {
              if (!draggingProjectId) return;
              event.preventDefault();
              event.stopPropagation();
              void handleDropProjectIntoFolder(node.id);
            }}
            className={`flex items-center gap-1 rounded-md pr-1 ${
              isSelected ? "bg-brand-50 text-brand-800" : "text-gray-700 hover:bg-gray-50"
            } ${isDropTarget ? "ring-2 ring-brand-300 bg-brand-50/70" : ""}`}
            style={{ paddingLeft: `${8 + depth * 14}px` }}
          >
            {hasChildren ? (
              <button
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  toggleFolderExpanded(node.id);
                }}
                className="flex h-7 w-7 items-center justify-center rounded text-sm text-gray-500 hover:bg-gray-200 hover:text-gray-700"
                aria-label={isExpanded ? `Contraer ${node.name}` : `Expandir ${node.name}`}
                aria-expanded={isExpanded}
              >
                {isExpanded ? "▾" : "▸"}
              </button>
            ) : (
              <span className="inline-block w-4" />
            )}
            <button
              type="button"
              onClick={() => setSelectedScope(node.id)}
              className={`flex min-w-0 flex-1 items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm ${
                isSelected ? "font-medium text-brand-800" : "text-gray-700"
              }`}
            >
              <span aria-hidden className="text-base leading-none">📁</span>
              <span className="truncate">{node.name}</span>
            </button>
          </div>
        </li>
      );
      return [row, ...(hasChildren && isExpanded ? renderSidebarTree(node.children, depth + 1) : [])];
    });
  }

  return (
    <AppShell title="">
      <div className="mb-4 flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
        <div>
          <h2 className="text-xl font-semibold text-gray-900">Videos</h2>
          <p className="mt-1 text-sm text-gray-500">
            Organiza en carpetas tus videos y administra tus proyectos en un espacio escalable estilo Drive.
          </p>
        </div>
        <div className="relative" data-new-menu-root="true">
          <button
            type="button"
            onClick={() => setIsNewMenuOpen((value) => !value)}
            className="inline-flex items-center gap-2 rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm hover:bg-brand-700"
          >
            <span className="text-base leading-none">＋</span>
            Nuevo
          </button>
          {isNewMenuOpen ? (
            <div className="absolute right-0 z-30 mt-2 w-52 rounded-md border border-gray-200 bg-white py-1 shadow-lg">
              <button
                type="button"
                onClick={() => {
                  setIsNewMenuOpen(false);
                  setFolderParentId(null);
                  setFolderName("");
                  setIsFolderFormOpen(true);
                }}
                className="block w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
              >
                Crear carpeta
              </button>
              <button
                type="button"
                disabled={selectedScope === ALL_SCOPE}
                onClick={() => {
                  setIsNewMenuOpen(false);
                  if (selectedScope !== ALL_SCOPE) openSubfolderForm(selectedScope);
                }}
                className="block w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:text-gray-400"
              >
                Crear subcarpeta
              </button>
              <button
                type="button"
                onClick={() => {
                  setIsNewMenuOpen(false);
                  openProjectForm(selectedScope === ALL_SCOPE ? null : selectedScope);
                }}
                className="block w-full px-3 py-2 text-left text-sm text-gray-700 hover:bg-gray-50"
              >
                Crear proyecto
              </button>
            </div>
          ) : null}
        </div>
      </div>

      {isFolderFormOpen ? (
        <form onSubmit={handleCreateFolder} className="mb-4 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
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
              className="rounded-md bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-400"
            >
              {isSavingFolder ? "Guardando..." : "Guardar carpeta"}
            </button>
          </div>
        </form>
      ) : null}

      {isProjectFormOpen ? (
        <form onSubmit={handleCreateProject} className="mb-6 rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <div className="mb-4 rounded-md border border-gray-200 bg-gray-50 px-3 py-2 text-sm text-gray-700">
            <span className="font-medium">Ubicación:</span>{" "}
            {projectFolderId ? folderNameById.get(projectFolderId) ?? "Carpeta seleccionada" : "Todas las carpetas"}
          </div>
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
                <option value="">Todos los proyectos (sin carpeta)</option>
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
              className="rounded-md bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-400"
            >
              {isCreatingProject ? "Creando..." : "Crear proyecto"}
            </button>
          </div>
        </form>
      ) : null}

      {error ? <div className="mb-6 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">{error}</div> : null}
      {notice ? <div className="mb-6 rounded-lg border border-emerald-200 bg-emerald-50 px-4 py-3 text-sm text-emerald-700">{notice}</div> : null}

      <div className="grid min-h-[calc(100vh-220px)] grid-cols-1 gap-6 xl:grid-cols-[280px_1fr]">
        <section className="flex h-full min-h-0 flex-col rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-200 px-4 py-3">
            <h3 className="text-sm font-semibold text-gray-900">Navegación</h3>
          </div>
          <div className="flex min-h-0 flex-1 flex-col gap-3 p-4">
            <input
              value={folderQuery}
              onChange={(event) => setFolderQuery(event.target.value)}
              placeholder="Buscar carpetas"
              className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
            />
            <button
              type="button"
              onClick={() => setSelectedScope(ALL_SCOPE)}
              className={`w-full rounded-md px-3 py-2 text-left text-sm ${
                selectedScope === ALL_SCOPE ? "bg-brand-50 font-medium text-brand-800" : "text-gray-700 hover:bg-gray-50"
              }`}
            >
              Todas las carpetas
            </button>
            {isLoading ? (
              <div className="space-y-2">
                <div className="h-9 animate-pulse rounded-md bg-gray-100" />
                <div className="h-9 animate-pulse rounded-md bg-gray-100" />
              </div>
            ) : sidebarTree.length === 0 ? (
              <p className="rounded-md border border-dashed border-gray-200 px-3 py-4 text-xs text-gray-500">
                {folderQuery.trim() ? "No hay carpetas que coincidan con la búsqueda." : "No hay carpetas creadas."}
              </p>
            ) : (
              <div className="min-h-0 flex-1 overflow-auto pr-1">
                <ul className="space-y-0.5">{renderSidebarTree(sidebarTree)}</ul>
              </div>
            )}
          </div>
        </section>

        <section className="rounded-lg border border-gray-200 bg-white shadow-sm">
          <div className="border-b border-gray-200 px-5 py-4">
            <div className="flex flex-col gap-3">
              <div className="flex items-center justify-between gap-3">
                <h3 className="text-base font-semibold text-gray-900">{selectedScopeLabel}</h3>
                <span className="rounded-full bg-gray-100 px-2.5 py-1 text-xs font-medium text-gray-700">{filteredProjects.length} proyectos</span>
              </div>
              <nav className="flex flex-wrap items-center gap-1 text-xs text-gray-600">
                <button type="button" onClick={() => setSelectedScope(ALL_SCOPE)} className="rounded px-1.5 py-0.5 hover:bg-gray-100">
                  Todas las carpetas
                </button>
                {breadcrumb.map((node, index) => (
                  <span key={node.id} className="inline-flex items-center gap-1">
                    <span>/</span>
                    {index === breadcrumb.length - 1 ? (
                      <span className="rounded bg-brand-50 px-1.5 py-0.5 font-medium text-brand-800">{node.name}</span>
                    ) : (
                      <button type="button" onClick={() => setSelectedScope(node.id)} className="rounded px-1.5 py-0.5 hover:bg-gray-100">
                        {node.name}
                      </button>
                    )}
                  </span>
                ))}
              </nav>
              <div className="grid grid-cols-1 gap-3 lg:grid-cols-[1fr_1fr_auto_auto]">
                <input
                  value={projectQuery}
                  onChange={(event) => setProjectQuery(event.target.value)}
                  placeholder="Buscar proyectos"
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                />
                <select
                  value={sortMode}
                  onChange={(event) => setSortMode(event.target.value as SortMode)}
                  className="w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                >
                  <option value="name">Ordenar por nombre</option>
                  <option value="created">Recientemente creado</option>
                  <option value="updated">Recientemente actualizado</option>
                </select>
                <div className="inline-flex rounded-md border border-gray-300 p-0.5">
                  <button
                    type="button"
                    onClick={() => setViewMode("grid")}
                    className={`rounded px-3 py-1.5 text-xs font-medium ${viewMode === "grid" ? "bg-gray-900 text-white" : "text-gray-700 hover:bg-gray-100"}`}
                  >
                    Cuadrícula
                  </button>
                  <button
                    type="button"
                    onClick={() => setViewMode("list")}
                    className={`rounded px-3 py-1.5 text-xs font-medium ${viewMode === "list" ? "bg-gray-900 text-white" : "text-gray-700 hover:bg-gray-100"}`}
                  >
                    Lista
                  </button>
                </div>
                <button
                  type="button"
                  onClick={() => openProjectForm(selectedScope === ALL_SCOPE ? null : selectedScope)}
                  className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50"
                >
                  {isFolderScope ? "Crear video en esta carpeta" : "Crear video"}
                </button>
              </div>
            </div>
          </div>

          <div className="space-y-6 p-4">
            <div>
              <div className="mb-3 flex items-center justify-between">
                <h4 className="text-sm font-semibold text-gray-900">Carpetas</h4>
                <span className="text-xs text-gray-500">{visibleFolders.length}</span>
              </div>
              {isLoading ? (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  <div className="h-24 animate-pulse rounded-lg bg-gray-100" />
                  <div className="h-24 animate-pulse rounded-lg bg-gray-100" />
                </div>
              ) : visibleFolders.length === 0 ? (
                <div className="rounded-lg border border-dashed border-gray-200 px-4 py-5 text-sm text-gray-500">
                  {folderQuery.trim() ? "No hay resultados de carpetas en esta ubicación." : "No hay subcarpetas en esta ubicación."}
                </div>
              ) : viewMode === "grid" ? (
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-3">
                  {visibleFolders.map((folder) => {
                    const folderProjects = projectsByFolder.get(folder.id) ?? [];
                    const isDropTarget = draggingProjectId !== null && dragOverFolderId === folder.id;
                    return (
                      <article
                        key={folder.id}
                        role="button"
                        tabIndex={0}
                        onDragOver={(event) => {
                          if (!draggingProjectId) return;
                          event.preventDefault();
                          event.dataTransfer.dropEffect = "move";
                          if (dragOverFolderId !== folder.id) setDragOverFolderId(folder.id);
                        }}
                        onDragLeave={() => {
                          if (dragOverFolderId === folder.id) setDragOverFolderId(null);
                        }}
                        onDrop={(event) => {
                          if (!draggingProjectId) return;
                          event.preventDefault();
                          event.stopPropagation();
                          void handleDropProjectIntoFolder(folder.id);
                        }}
                        onClick={() => setSelectedScope(folder.id)}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            setSelectedScope(folder.id);
                          }
                        }}
                        className={`rounded-lg border p-4 transition ${
                          selectedScope === folder.id
                            ? "border-brand-300 bg-brand-50 ring-1 ring-brand-200"
                            : "cursor-pointer border-gray-200 bg-white hover:border-gray-300 hover:shadow-sm"
                        } ${isDropTarget ? "ring-2 ring-brand-300 bg-brand-50/70" : ""}`}
                      >
                        <div className="flex items-start justify-between gap-2">
                          <div className="min-w-0 text-left">
                            <p className="truncate text-sm font-semibold text-gray-900">📁 {folder.name}</p>
                            <p className="mt-1 text-xs text-gray-500">{folderProjects.length} proyectos</p>
                          </div>
                          <div className="relative shrink-0" data-folder-menu-root="true">
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                setOpenFolderMenuId((prev) => (prev === folder.id ? null : folder.id));
                              }}
                              className="rounded-md border border-gray-200 px-2 py-1 text-sm text-gray-600 hover:bg-gray-100"
                              aria-label={`Acciones para carpeta ${folder.name}`}
                            >
                              &#8942;
                            </button>
                            {openFolderMenuId === folder.id ? (
                              <div className="absolute right-0 z-20 mt-1 w-44 rounded-md border border-gray-200 bg-white py-1 shadow-lg">
                                <button
                                  type="button"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    setOpenFolderMenuId(null);
                                    openSubfolderForm(folder.id);
                                  }}
                                  className="block w-full px-3 py-2 text-left text-xs font-medium text-gray-700 hover:bg-gray-50"
                                >
                                  Crear subcarpeta
                                </button>
                                <button
                                  type="button"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    setOpenFolderMenuId(null);
                                    openRenameFolderModal(folder.id, folder.name);
                                  }}
                                  className="block w-full px-3 py-2 text-left text-xs font-medium text-gray-700 hover:bg-gray-50"
                                >
                                  Renombrar carpeta
                                </button>
                                <button
                                  type="button"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    setOpenFolderMenuId(null);
                                    openDeleteModal({ kind: "folder", id: folder.id, name: folder.name });
                                  }}
                                  className="block w-full px-3 py-2 text-left text-xs font-medium text-red-700 hover:bg-red-50"
                                >
                                  Eliminar carpeta
                                </button>
                              </div>
                            ) : null}
                          </div>
                        </div>
                      </article>
                    );
                  })}
                </div>
              ) : (
                <div className="overflow-hidden rounded-lg border border-gray-200">
                  <ul className="divide-y divide-gray-200">
                    {visibleFolders.map((folder) => {
                      const folderProjects = projectsByFolder.get(folder.id) ?? [];
                      const isDropTarget = draggingProjectId !== null && dragOverFolderId === folder.id;
                      return (
                        <li
                          key={folder.id}
                          role="button"
                          tabIndex={0}
                          onDragOver={(event) => {
                            if (!draggingProjectId) return;
                            event.preventDefault();
                            event.dataTransfer.dropEffect = "move";
                            if (dragOverFolderId !== folder.id) setDragOverFolderId(folder.id);
                          }}
                          onDragLeave={() => {
                            if (dragOverFolderId === folder.id) setDragOverFolderId(null);
                          }}
                          onDrop={(event) => {
                            if (!draggingProjectId) return;
                            event.preventDefault();
                            event.stopPropagation();
                            void handleDropProjectIntoFolder(folder.id);
                          }}
                          onClick={() => setSelectedScope(folder.id)}
                          onKeyDown={(event) => {
                            if (event.key === "Enter" || event.key === " ") {
                              event.preventDefault();
                              setSelectedScope(folder.id);
                            }
                          }}
                          className={`flex cursor-pointer items-center justify-between gap-3 bg-white px-4 py-3 hover:bg-gray-50 ${
                            isDropTarget ? "ring-2 ring-inset ring-brand-300 bg-brand-50/70" : ""
                          }`}
                        >
                          <div className="min-w-0 text-left">
                            <p className="truncate text-sm font-medium text-gray-900">📁 {folder.name}</p>
                            <p className="mt-0.5 text-xs text-gray-500">{folderProjects.length} proyectos</p>
                          </div>
                          <div className="relative shrink-0" data-folder-menu-root="true">
                            <button
                              type="button"
                              onClick={(event) => {
                                event.stopPropagation();
                                setOpenFolderMenuId((prev) => (prev === folder.id ? null : folder.id));
                              }}
                              className="rounded-md border border-gray-200 px-2 py-1 text-sm text-gray-600 hover:bg-gray-100"
                            >
                              &#8942;
                            </button>
                            {openFolderMenuId === folder.id ? (
                              <div className="absolute right-0 z-20 mt-1 w-44 rounded-md border border-gray-200 bg-white py-1 shadow-lg">
                                <button
                                  type="button"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    setOpenFolderMenuId(null);
                                    openSubfolderForm(folder.id);
                                  }}
                                  className="block w-full px-3 py-2 text-left text-xs font-medium text-gray-700 hover:bg-gray-50"
                                >
                                  Crear subcarpeta
                                </button>
                                <button
                                  type="button"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    setOpenFolderMenuId(null);
                                    openRenameFolderModal(folder.id, folder.name);
                                  }}
                                  className="block w-full px-3 py-2 text-left text-xs font-medium text-gray-700 hover:bg-gray-50"
                                >
                                  Renombrar carpeta
                                </button>
                                <button
                                  type="button"
                                  onClick={(event) => {
                                    event.stopPropagation();
                                    setOpenFolderMenuId(null);
                                    openDeleteModal({ kind: "folder", id: folder.id, name: folder.name });
                                  }}
                                  className="block w-full px-3 py-2 text-left text-xs font-medium text-red-700 hover:bg-red-50"
                                >
                                  Eliminar carpeta
                                </button>
                              </div>
                            ) : null}
                          </div>
                        </li>
                      );
                    })}
                  </ul>
                </div>
              )}
            </div>

            {isFolderScope ? (
              <div>
              <div className="mb-3 flex items-center justify-between">
                <h4 className="text-sm font-semibold text-gray-900">Proyectos en esta carpeta</h4>
                <span className="text-xs text-gray-500">{filteredProjects.length}</span>
              </div>
              {isLoading ? (
                <div className="grid grid-cols-1 gap-3 xl:grid-cols-2">
                  <div className="h-28 animate-pulse rounded-lg bg-gray-100" />
                  <div className="h-28 animate-pulse rounded-lg bg-gray-100" />
                </div>
              ) : filteredProjects.length === 0 ? (
                <div className="rounded-lg border border-dashed border-gray-200 px-4 py-6 text-sm text-gray-500">
                  {projectQuery.trim()
                    ? "No se encontraron proyectos para esa búsqueda."
                    : "No hay proyectos en esta carpeta. Crea uno aquí o mueve uno existente."}
                </div>
              ) : (
                <div className={viewMode === "grid" ? "grid grid-cols-1 gap-3 xl:grid-cols-2" : "space-y-2"}>
                  {filteredProjects.map((project) => (
                    <article
                      key={project.id}
                      draggable
                      onDragStart={() => handleProjectDragStart(project.id)}
                      onDragEnd={handleProjectDragEnd}
                      className={`rounded-lg border border-gray-200 bg-white p-4 transition hover:border-brand-200 hover:shadow-sm ${
                        viewMode === "list" ? "flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between" : ""
                      } ${draggingProjectId === project.id ? "opacity-70 ring-2 ring-brand-200" : ""}`}
                    >
                      <div className="flex min-w-0 items-start justify-between gap-3">
                        <div className="min-w-0">
                          <Link href={`/projects/${project.id}`} className="block">
                            <p className="truncate text-sm font-semibold text-gray-900 hover:text-brand-700">{project.name}</p>
                          </Link>
                          <p className="mt-1 truncate text-sm text-gray-500">{project.description || "Sin descripción"}</p>
                          <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-gray-500">
                            <span className="rounded-full bg-gray-100 px-2 py-1">
                              {project.folder_id ? folderNameById.get(project.folder_id) ?? "Carpeta" : "Todas las carpetas"}
                            </span>
                            <span>Actualizado: {formatDate(project.updated_at)}</span>
                            <span className="rounded-full bg-gray-100 px-2.5 py-1 font-medium text-gray-700">{project.status}</span>
                          </div>
                        </div>
                        <div className="relative shrink-0 self-start" data-project-menu-root="true">
                          <button
                            type="button"
                            onClick={() => setOpenProjectMenuId((prev) => (prev === project.id ? null : project.id))}
                            className="inline-flex h-8 w-8 items-center justify-center rounded-md border border-gray-300 text-sm text-gray-600 hover:bg-gray-100"
                            aria-label={`Acciones para proyecto ${project.name}`}
                          >
                            &#8942;
                          </button>
                          {openProjectMenuId === project.id ? (
                            <div className="absolute right-0 z-20 mt-1 w-64 rounded-md border border-gray-200 bg-white py-1 shadow-lg">
                              <Link
                                href={`/projects/${project.id}`}
                                onClick={() => setOpenProjectMenuId(null)}
                                className="block px-3 py-2 text-left text-xs font-medium text-gray-700 hover:bg-gray-50"
                              >
                                Abrir proyecto
                              </Link>
                              <div className="my-1 border-t border-gray-100" />
                              <div className="px-3 py-2">
                                <p className="mb-1 text-xs font-medium text-gray-500">Mover a carpeta</p>
                                <select
                                  value={project.folder_id ?? ""}
                                  onChange={(event) => {
                                    setOpenProjectMenuId(null);
                                    void handleMoveProject(project.id, event.target.value || null);
                                  }}
                                  disabled={movingProjectId === project.id}
                                  className="w-full rounded-md border border-gray-300 px-2 py-1.5 text-xs outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100 disabled:cursor-not-allowed disabled:bg-gray-100"
                                >
                                  <option value="">Todas las carpetas</option>
                                  {folderOptions.map((folder) => (
                                    <option key={folder.id} value={folder.id}>
                                      {"\u00A0".repeat(folder.depth * 2)}
                                      {folder.name}
                                    </option>
                                  ))}
                                </select>
                              </div>
                              <div className="my-1 border-t border-gray-100" />
                              <button
                                type="button"
                                onClick={() => {
                                  setOpenProjectMenuId(null);
                                  openDeleteModal({ kind: "project", id: project.id, name: project.name });
                                }}
                                className="block w-full px-3 py-2 text-left text-xs font-medium text-red-700 hover:bg-red-50"
                              >
                                Eliminar proyecto
                              </button>
                            </div>
                          ) : null}
                        </div>
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </div>
            ) : null}
          </div>
        </section>
      </div>

      {folderRenameTarget ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/40 p-4">
          <div className="w-full max-w-md rounded-lg border border-gray-200 bg-white p-5 shadow-xl">
            <h4 className="text-base font-semibold text-gray-900">Renombrar carpeta</h4>
            <p className="mt-2 text-sm text-gray-600">
              Ingresa un nuevo nombre para <span className="font-medium text-gray-900">{folderRenameTarget.currentName}</span>.
            </p>
            <label className="mt-4 block">
              <span className="text-xs font-medium text-gray-700">Nuevo nombre</span>
              <input
                value={renameFolderValue}
                onChange={(event) => {
                  setRenameFolderValue(event.target.value);
                  setRenameFolderError(null);
                }}
                className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-brand-500 focus:ring-2 focus:ring-brand-100"
                maxLength={255}
                autoFocus
              />
            </label>
            {renameFolderError ? (
              <p className="mt-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{renameFolderError}</p>
            ) : null}
            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                type="button"
                disabled={isRenamingFolder}
                onClick={() => {
                  setFolderRenameTarget(null);
                  setRenameFolderError(null);
                }}
                className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:bg-gray-100"
              >
                Cancelar
              </button>
              <button
                type="button"
                disabled={isRenamingFolder}
                onClick={() => void handleRenameFolder()}
                className="rounded-md bg-gray-900 px-3 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:cursor-not-allowed disabled:bg-gray-400"
              >
                {isRenamingFolder ? "Guardando..." : "Guardar"}
              </button>
            </div>
          </div>
        </div>
      ) : null}

      {deleteTarget ? (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-gray-900/50 p-4">
          <div className="w-full max-w-md rounded-lg border border-red-200 bg-white p-5 shadow-xl">
            <h4 className="text-base font-semibold text-gray-900">
              {deleteTarget.kind === "folder" ? "Eliminar carpeta" : "Eliminar proyecto"}
            </h4>
            <p className="mt-2 text-sm text-gray-600">
              Vas a eliminar <span className="font-medium text-gray-900">{deleteTarget.name}</span>. Esta acción no se puede deshacer.
            </p>
            <p className="mt-1 text-sm text-red-700">
              {deleteTarget.kind === "folder"
                ? "Si contiene subcarpetas o proyectos, será necesario confirmar la eliminación total."
                : "También se eliminarán presentaciones, assets y configuraciones asociadas al proyecto."}
            </p>
            <div className="mt-4 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">
              {deleteConfirmStep === 1
                ? "Paso 1 de 2: confirma que quieres continuar."
                : "Paso 2 de 2: escribe el nombre exacto para confirmar."}
            </div>
            {deleteConfirmStep === 2 ? (
              <label className="mt-3 block">
                <span className="text-xs font-medium text-gray-700">Escribe: {deleteTarget.name}</span>
                <input
                  value={deleteConfirmValue}
                  onChange={(event) => {
                    setDeleteConfirmValue(event.target.value);
                    setDeleteDialogError(null);
                  }}
                  className="mt-1 w-full rounded-md border border-gray-300 px-3 py-2 text-sm outline-none focus:border-red-500 focus:ring-2 focus:ring-red-100"
                  autoFocus
                />
              </label>
            ) : null}
            {deleteDialogError ? (
              <p className="mt-3 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{deleteDialogError}</p>
            ) : null}
            <div className="mt-5 flex items-center justify-end gap-2">
              <button
                type="button"
                disabled={isDeletingTarget}
                onClick={() => setDeleteTarget(null)}
                className="rounded-md border border-gray-300 bg-white px-3 py-2 text-sm font-medium text-gray-700 hover:bg-gray-50 disabled:cursor-not-allowed disabled:bg-gray-100"
              >
                Cancelar
              </button>
              <button
                type="button"
                disabled={isDeletingTarget}
                onClick={() => void handleDeleteTarget()}
                className="rounded-md bg-red-600 px-3 py-2 text-sm font-semibold text-white hover:bg-red-700 disabled:cursor-not-allowed disabled:bg-red-300"
              >
                {isDeletingTarget
                  ? "Eliminando..."
                  : deleteConfirmStep === 1
                  ? "Eliminar"
                  : deleteNeedsCascade
                  ? "Sí, eliminar permanentemente todo"
                  : "Sí, eliminar permanentemente"}
              </button>
            </div>
          </div>
        </div>
      ) : null}
    </AppShell>
  );
}
