import { lazy, Suspense } from "react";

const PeoplePage = lazy(() => import("../people/PeoplePage"));
const MemoryPage = lazy(() => import("./MemoryPage"));
const GroupDirectoryPage = lazy(() => import("./GroupDirectoryPage"));

export default function PeopleWorkspace({ section }: { section: number }) {
  return (
    <Suspense fallback={<p role="status">正在打开用户空间…</p>}>
      {section === 0 || section === 2 ? (
        <PeoplePage section={section} />
      ) : section === 1 ? (
        <GroupDirectoryPage />
      ) : (
        <MemoryPage section={section} />
      )}
    </Suspense>
  );
}
