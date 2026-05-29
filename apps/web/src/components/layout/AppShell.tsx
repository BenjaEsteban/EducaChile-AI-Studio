import { Header } from "./Header";
import { Sidebar } from "./Sidebar";

interface AppShellProps {
  title: string;
  children: React.ReactNode;
}

export function AppShell({ title, children }: AppShellProps) {
  const showHeader = title.trim().length > 0;
  return (
    <div className="flex h-screen overflow-hidden">
      <Sidebar />
      <div className="flex flex-1 flex-col overflow-auto">
        {showHeader ? <Header title={title} /> : null}
        <main className="flex-1 p-6">{children}</main>
      </div>
    </div>
  );
}
