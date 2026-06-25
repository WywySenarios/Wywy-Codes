import { useState, useEffect } from "react";
import { LogViewer } from "./log-viewer";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../ui/tabs";
import { getSpaLogs, type SpaLogResponse } from "../../lib/api";

const SPA_TABS = ["system", "django"] as const;

function logTabLabel(value: string): string {
  if (value === "system") return "System";
  if (value === "django") return "Django";
  return value.replace(".log", "");
}

export function LogTabs({
  pipelineId,
  logFiles,
}: {
  pipelineId: string;
  logFiles: string[];
}) {
  const [spaData, setSpaData] = useState<SpaLogResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    getSpaLogs(pipelineId)
      .then((data) => {
        if (!cancelled) setSpaData(data);
      })
      .catch(() => {
        /* SPA endpoint may not be available */
      });
    return () => {
      cancelled = true;
    };
  }, [pipelineId]);

  const files = logFiles.length > 0 ? logFiles : ["orchestrator.log"];
  const allTabs = [...SPA_TABS, ...files];

  return (
    <Tabs defaultValue={allTabs[0]}>
      <TabsList>
        {allTabs.map((value) => (
          <TabsTrigger key={value} value={value}>
            {logTabLabel(value)}
          </TabsTrigger>
        ))}
      </TabsList>
      {allTabs.map((value) => (
        <TabsContent key={value} value={value}>
          {SPA_TABS.includes(value) ? (
            <LogViewer
              entries={
                value === "system"
                  ? (spaData?.system ?? [])
                  : (spaData?.django ?? [])
              }
            />
          ) : (
            <LogViewer pipelineId={pipelineId} stage={value} />
          )}
        </TabsContent>
      ))}
    </Tabs>
  );
}
