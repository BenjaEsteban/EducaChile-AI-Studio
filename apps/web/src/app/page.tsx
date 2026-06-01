"use client";

import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

const navItems = [
  { id: "inicio", label: "Inicio" },
  { id: "caracteristicas", label: "Características" },
  { id: "como-funciona", label: "Cómo funciona" },
  { id: "beneficios", label: "Beneficios" },
] as const;

const featureCards = [
  {
    title: "PowerPoint a video",
    description:
      "Convierte presentaciones PPT/PPTX en videos educativos listos para usar en clases, cursos y capacitación.",
  },
  {
    title: "Narración con IA",
    description:
      "Genera locución clara por diapositiva y ajusta el contenido para mantener una explicación precisa y natural.",
  },
  {
    title: "Avatar con IA",
    description:
      "Añade un presentador virtual para reforzar el contenido y mantener una experiencia visual más dinámica.",
  },
  {
    title: "Subtítulos",
    description:
      "Incluye subtítulos para mejorar comprensión, accesibilidad y reutilización del material en distintos canales.",
  },
  {
    title: "Biblioteca organizada",
    description:
      "Gestiona videos y carpetas en un entorno centralizado para escalar la creación de contenido en tu equipo.",
  },
];

const workflowSteps = [
  {
    title: "Sube tu PowerPoint",
    description:
      "Carga tu presentación y revisa cada diapositiva como base visual real del video.",
  },
  {
    title: "Edita narración y ajustes",
    description:
      "Define diálogo por slide, voz, avatar y parámetros clave para el resultado final.",
  },
  {
    title: "Genera el video con IA",
    description:
      "La plataforma procesa narración, avatar, subtítulos y composición automáticamente.",
  },
  {
    title: "Revisa y utiliza el resultado",
    description:
      "Descarga el video final y úsalo en clases, formación interna o contenidos para clientes.",
  },
];

const benefits = [
  "Acelera la producción de contenido educativo y de entrenamiento.",
  "Estandariza materiales para asegurar calidad y consistencia.",
  "Mejora el engagement con videos más claros y visualmente atractivos.",
  "Escala la creación de videos sin aumentar la complejidad operativa.",
];

export default function HomePage() {
  const [activeSection, setActiveSection] =
    useState<(typeof navItems)[number]["id"]>("inicio");
  const [isScrolled, setIsScrolled] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);

  const navById = useMemo(() => new Set(navItems.map((item) => item.id)), []);

  useEffect(() => {
    const sections = navItems
      .map((item) => document.getElementById(item.id))
      .filter((node): node is HTMLElement => Boolean(node));

    if (!sections.length) return;

    const sectionObserver = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => b.intersectionRatio - a.intersectionRatio);
        if (!visible.length) return;
        const next = visible[0].target.id;
        if (navById.has(next)) {
          setActiveSection(next as (typeof navItems)[number]["id"]);
        }
      },
      {
        rootMargin: "-35% 0px -50% 0px",
        threshold: [0.2, 0.35, 0.5],
      },
    );

    sections.forEach((section) => sectionObserver.observe(section));

    const onScroll = () => setIsScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });

    return () => {
      sectionObserver.disconnect();
      window.removeEventListener("scroll", onScroll);
    };
  }, [navById]);

  useEffect(() => {
    const elements = Array.from(document.querySelectorAll("[data-reveal]"));
    if (!elements.length) return;

    const revealObserver = new IntersectionObserver(
      (entries) => {
        entries.forEach((entry) => {
          if (!entry.isIntersecting) return;
          entry.target.classList.add("is-visible");
          revealObserver.unobserve(entry.target);
        });
      },
      {
        threshold: 0.12,
        rootMargin: "0px 0px -8% 0px",
      },
    );

    elements.forEach((element) => revealObserver.observe(element));
    return () => revealObserver.disconnect();
  }, []);

  const scrollToSection = (sectionId: (typeof navItems)[number]["id"]) => {
    const section = document.getElementById(sectionId);
    const navbar = document.getElementById("landing-navbar");
    if (!section) return;
    const offset = (navbar?.offsetHeight ?? 72) + 12;
    const top = section.getBoundingClientRect().top + window.scrollY - offset;
    window.scrollTo({ top, behavior: "smooth" });
    setMobileMenuOpen(false);
  };

  return (
    <div className="min-h-screen bg-gradient-to-b from-white via-slate-50 to-slate-100 text-slate-900">
      <header
        id="landing-navbar"
        className={`sticky top-0 z-40 transition-all duration-300 ${isScrolled
          ? "border-b border-slate-200/80 bg-white/85 shadow-sm backdrop-blur-lg"
          : "border-b border-transparent bg-white/70 backdrop-blur"
          }`}
      >
        <div className="mx-auto w-full max-w-7xl px-4 sm:px-6 lg:px-8">
          <div className="flex min-h-16 items-center justify-between gap-3">
            <button
              type="button"
              onClick={() => scrollToSection("inicio")}
              className="truncate text-sm font-semibold tracking-tight text-slate-900 sm:text-base"
            >
              Educa Chile AI Video Platform
            </button>

            <nav className="hidden items-center gap-1 md:flex">
              {navItems.map((item) => {
                const isActive = activeSection === item.id;
                return (
                  <button
                    key={item.id}
                    type="button"
                    onClick={() => scrollToSection(item.id)}
                    className={`rounded-md px-3 py-2 text-sm transition ${isActive
                      ? "bg-brand-50 font-medium text-brand-700"
                      : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                      }`}
                  >
                    {item.label}
                  </button>
                );
              })}
            </nav>

            <div className="flex items-center gap-2">
              <Link
                href="/dashboard"
                className="rounded-md bg-brand-600 px-4 py-2 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700"
              >
                Crear videos
              </Link>
              <button
                type="button"
                onClick={() => setMobileMenuOpen((value) => !value)}
                className="inline-flex h-9 w-9 items-center justify-center rounded-md border border-slate-300 bg-white text-slate-700 hover:bg-slate-50 md:hidden"
                aria-label="Abrir navegación"
              >
                ☰
              </button>
            </div>
          </div>

          {mobileMenuOpen ? (
            <div className="pb-3 md:hidden">
              <div className="grid gap-1 rounded-lg border border-slate-200 bg-slate-50 p-2">
                {navItems.map((item) => {
                  const isActive = activeSection === item.id;
                  return (
                    <button
                      key={item.id}
                      type="button"
                      onClick={() => scrollToSection(item.id)}
                      className={`rounded-md px-3 py-2 text-left text-sm ${isActive
                        ? "bg-white font-medium text-brand-700"
                        : "text-slate-700 hover:bg-white"
                        }`}
                    >
                      {item.label}
                    </button>
                  );
                })}
              </div>
            </div>
          ) : null}
        </div>
      </header>

      <main>
        <section id="inicio" className="scroll-mt-28">
          <div className="mx-auto grid w-full max-w-7xl items-start gap-8 px-4 pb-14 pt-12 sm:px-6 lg:grid-cols-[1.15fr_.85fr] lg:gap-10 lg:px-8 lg:pb-18 lg:pt-18">
            <div className="reveal-on-scroll space-y-6" data-reveal>
              <span className="inline-flex rounded-full border border-brand-200 bg-brand-50 px-3 py-1 text-xs font-semibold uppercase tracking-wide text-brand-700">
                Plataforma para educación y capacitación
              </span>
              <h1 className="text-4xl font-bold leading-tight tracking-tight text-slate-900 sm:text-5xl lg:text-6xl">
                Transforma presentaciones PowerPoint en videos educativos con IA
              </h1>
              <p className="max-w-2xl text-base leading-relaxed text-slate-600 sm:text-lg">
                Convierte tus diapositivas en videos listos para usar con narración, avatar, subtítulos
                y composición automática. Diseñado para equipos que necesitan producir contenido de forma
                rápida, clara y profesional.
              </p>
              <div className="flex flex-wrap items-center gap-3 pt-2">
                <Link
                  href="/dashboard"
                  className="rounded-md bg-brand-600 px-5 py-3 text-sm font-semibold text-white shadow-sm transition hover:bg-brand-700"
                >
                  Comenzar a crear videos
                </Link>
                <button
                  type="button"
                  onClick={() => scrollToSection("como-funciona")}
                  className="rounded-md border border-slate-300 bg-white px-5 py-3 text-sm font-semibold text-slate-700 transition hover:bg-slate-50"
                >
                  Ver cómo funciona
                </button>
              </div>
            </div>

            <div className="reveal-on-scroll self-start rounded-xl border border-slate-200 bg-white p-4 shadow-md shadow-slate-200/50 sm:p-5" data-reveal>
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-4">
                <div className="flex items-center justify-between gap-3">
                  <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
                    Vista previa del flujo
                  </p>
                  <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold text-emerald-700">
                    EN PROCESO
                  </span>
                </div>
                <p className="mt-2 text-sm font-medium text-slate-900">Curso de onboarding comercial</p>

                <div className="mt-4 space-y-2.5">
                  <div className="rounded-md border border-emerald-200/80 bg-white px-3 py-2.5">
                    <div className="flex items-center gap-3">
                      <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-emerald-100 text-[11px] font-semibold text-emerald-700">
                        1
                      </span>
                      <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                        <p className="truncate text-sm font-medium text-slate-800">Carga de PPT</p>
                        <span className="rounded-full bg-emerald-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-emerald-700">
                          Completado
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-md border border-brand-200/80 bg-white px-3 py-2.5">
                    <div className="flex items-center gap-3">
                      <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-brand-100 text-[11px] font-semibold text-brand-700">
                        2
                      </span>
                      <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                        <p className="truncate text-sm font-medium text-slate-800">Narración IA</p>
                        <span className="rounded-full bg-brand-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-brand-700">
                          8/12 slides
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-md border border-amber-200/80 bg-white px-3 py-2.5">
                    <div className="flex items-center gap-3">
                      <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-amber-100 text-[11px] font-semibold text-amber-700">
                        3
                      </span>
                      <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                        <p className="truncate text-sm font-medium text-slate-800">Avatar + subtítulos</p>
                        <span className="rounded-full bg-amber-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-amber-700">
                          En cola
                        </span>
                      </div>
                    </div>
                  </div>

                  <div className="rounded-md border border-slate-200 bg-white px-3 py-2.5">
                    <div className="flex items-center gap-3">
                      <span className="inline-flex h-6 w-6 shrink-0 items-center justify-center rounded-full bg-slate-100 text-[11px] font-semibold text-slate-600">
                        4
                      </span>
                      <div className="flex min-w-0 flex-1 items-center justify-between gap-2">
                        <p className="truncate text-sm font-medium text-slate-800">Exportación final</p>
                        <span className="rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-slate-600">
                          Pendiente
                        </span>
                      </div>
                    </div>
                  </div>
                </div>
              </div>

              <div className="mt-3 grid grid-cols-3 gap-2.5 text-center">
                <div className="rounded-md border border-slate-200 bg-white p-2.5">
                  <p className="text-base font-semibold text-slate-900">12</p>
                  <p className="mt-0.5 text-[11px] text-slate-500">Diapositivas</p>
                </div>
                <div className="rounded-md border border-slate-200 bg-white p-2.5">
                  <p className="text-base font-semibold text-slate-900">9 min</p>
                  <p className="mt-0.5 text-[11px] text-slate-500">Duración</p>
                </div>
                <div className="rounded-md border border-slate-200 bg-white p-2.5">
                  <p className="text-base font-semibold text-slate-900">HD</p>
                  <p className="mt-0.5 text-[11px] text-slate-500">Exportación</p>
                </div>
              </div>
            </div>
          </div>
        </section>

        <section id="caracteristicas" className="scroll-mt-28 border-y border-slate-200 bg-white/80 py-16 sm:py-20">
          <div className="mx-auto w-full max-w-7xl px-4 sm:px-6 lg:px-8">
            <div className="reveal-on-scroll max-w-2xl" data-reveal>
              <h2 className="text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">
                Características clave
              </h2>
              <p className="mt-3 text-slate-600">
                Todo lo necesario para convertir presentaciones en videos educativos con calidad de entrega.
              </p>
            </div>
            <div className="mt-10 grid gap-4 sm:grid-cols-2 xl:grid-cols-3">
              {featureCards.map((feature, index) => (
                <article
                  key={feature.title}
                  data-reveal
                  style={{ transitionDelay: `${Math.min(index, 4) * 70}ms` }}
                  className="reveal-on-scroll rounded-lg border border-slate-200 bg-white p-5 shadow-sm transition duration-200 hover:-translate-y-0.5 hover:shadow-md"
                >
                  <h3 className="text-base font-semibold text-slate-900">{feature.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-slate-600">{feature.description}</p>
                </article>
              ))}
            </div>
          </div>
        </section>

        <section id="como-funciona" className="scroll-mt-28 py-16 sm:py-20">
          <div className="mx-auto w-full max-w-7xl px-4 sm:px-6 lg:px-8">
            <div className="reveal-on-scroll max-w-2xl" data-reveal>
              <h2 className="text-3xl font-bold tracking-tight text-slate-900 sm:text-4xl">
                Cómo funciona
              </h2>
              <p className="mt-3 text-slate-600">
                Flujo simple para pasar de una presentación estática a un video listo para usar.
              </p>
            </div>
            <ol className="mt-10 grid gap-4 md:grid-cols-2 xl:grid-cols-4">
              {workflowSteps.map((step, index) => (
                <li
                  key={step.title}
                  data-reveal
                  style={{ transitionDelay: `${Math.min(index, 4) * 80}ms` }}
                  className="reveal-on-scroll rounded-lg border border-slate-200 bg-white p-5 shadow-sm transition hover:shadow-md"
                >
                  <span className="inline-flex h-7 w-7 items-center justify-center rounded-md bg-slate-900 text-xs font-semibold text-white">
                    {index + 1}
                  </span>
                  <h3 className="mt-3 text-base font-semibold text-slate-900">{step.title}</h3>
                  <p className="mt-2 text-sm leading-relaxed text-slate-600">{step.description}</p>
                </li>
              ))}
            </ol>
          </div>
        </section>

        <section id="beneficios" className="scroll-mt-28 bg-slate-900 py-16 text-white sm:py-20">
          <div className="mx-auto w-full max-w-7xl px-4 sm:px-6 lg:px-8">
            <div className="reveal-on-scroll max-w-2xl" data-reveal>
              <h2 className="text-3xl font-bold tracking-tight sm:text-4xl">
                Beneficios para tu organización
              </h2>
              <p className="mt-3 text-slate-200">
                Crea contenido educativo más rápido, con calidad constante y mejor experiencia para el usuario final.
              </p>
            </div>
            <div className="mt-10 grid gap-3 sm:grid-cols-2">
              {benefits.map((item, index) => (
                <article
                  key={item}
                  data-reveal
                  style={{ transitionDelay: `${Math.min(index, 4) * 80}ms` }}
                  className="reveal-on-scroll rounded-lg border border-white/20 bg-white/5 p-4 text-sm text-slate-100 transition hover:bg-white/10"
                >
                  {item}
                </article>
              ))}
            </div>
            <div className="reveal-on-scroll mt-10 flex flex-wrap items-center justify-between gap-4 rounded-lg border border-white/20 bg-white/10 p-5" data-reveal>
              <div>
                <h3 className="text-lg font-semibold">Empieza a producir videos con IA desde hoy</h3>
                <p className="mt-1 text-sm text-slate-200">
                  Centraliza tu flujo y convierte presentaciones en contenido audiovisual escalable.
                </p>
              </div>
              <Link
                href="/dashboard"
                className="rounded-md bg-white px-5 py-3 text-sm font-semibold text-slate-900 transition hover:bg-slate-100"
              >
                Comenzar
              </Link>
            </div>
          </div>
        </section>
      </main>

      <footer className="border-t border-slate-200 bg-white">
        <div className="mx-auto grid w-full max-w-7xl gap-8 px-4 py-10 sm:px-6 md:grid-cols-[1.2fr_.8fr] lg:px-8">
          <div>
            <p className="text-base font-semibold text-slate-900">Educa Chile AI Video Platform</p>
            <p className="mt-2 max-w-xl text-sm leading-relaxed text-slate-600">
              Plataforma para transformar presentaciones en videos educativos con narración, avatar
              y flujo de producción asistido por IA.
            </p>
          </div>
          <div className="grid grid-cols-2 gap-4 text-sm">
            <div>
              <p className="font-semibold text-slate-900">Explorar</p>
              <ul className="mt-2 space-y-2 text-slate-600">
                {navItems.map((item) => (
                  <li key={item.id}>
                    <button
                      type="button"
                      onClick={() => scrollToSection(item.id)}
                      className="text-left transition hover:text-brand-700"
                    >
                      {item.label}
                    </button>
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <p className="font-semibold text-slate-900">Acceso</p>
              <ul className="mt-2 space-y-2 text-slate-600">
                <li>
                  <Link href="/dashboard" className="transition hover:text-brand-700">
                    Crear videos
                  </Link>
                </li>
                <li>
                  <Link href="/dashboard" className="transition hover:text-brand-700">
                    Ir al dashboard
                  </Link>
                </li>
              </ul>
            </div>
          </div>
        </div>
        <div className="border-t border-slate-200 py-4 text-center text-xs text-slate-500">
          © {new Date().getFullYear()} Educa Chile AI Video Platform. Todos los derechos reservados.
        </div>
      </footer>
    </div>
  );
}
