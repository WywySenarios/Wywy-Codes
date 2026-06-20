import { LogViewer } from "./log-viewer";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "../ui/tabs";

export function LogTabs({
  pipelineId,
  logFiles,
}: {
  pipelineId: string;
  logFiles: string[];
}) {
  const files = logFiles.length > 0 ? logFiles : ["orchestrator.log"];

  return (
    <Tabs defaultValue={files[0]}>
      <TabsList>
        {files.map((file) => (
          <TabsTrigger key={file} value={file}>
            {file.replace(".log", "")}
          </TabsTrigger>
        ))}
      </TabsList>
      {files.map((file) => (
        <TabsContent key={file} value={file}>
          <LogViewer pipelineId={pipelineId} stage={file} />
        </TabsContent>
      ))}
    </Tabs>
  );
}
