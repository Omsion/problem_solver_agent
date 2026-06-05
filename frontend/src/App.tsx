import { useState, useCallback } from "react";
import { HashRouter, Routes, Route } from "react-router-dom";
import { AppHeader } from "./components/layout/AppHeader";
import { SplitPanelLayout } from "./components/layout/SplitPanelLayout";
import { UploadZone } from "./components/upload/UploadZone";
import { FilePreviewList } from "./components/upload/FilePreviewList";
import { UploadActions } from "./components/upload/UploadActions";
import { ImageViewer } from "./components/viewer/ImageViewer";
import { ImageLightbox } from "./components/viewer/ImageLightbox";
import { OutputPanel } from "./components/output/OutputPanel";
import { TaskHistoryPage } from "./components/tasks/TaskHistoryPage";
import { useUploadStore } from "./stores/useUploadStore";
import { useTaskStore } from "./stores/useTaskStore";
import { createTask } from "./api/client";

function MainPage() {
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [isProcessing, setIsProcessing] = useState(false);
  const files = useUploadStore((s) => s.files);
  const connectSSE = useTaskStore((s) => s.connectSSE);

  const handleStart = useCallback(async () => {
    if (files.length === 0) return;
    setIsProcessing(true);
    try {
      const fileObjs = files.map((f) => f.file);
      const { task_id } = await createTask(fileObjs);
      setActiveTaskId(task_id);
      connectSSE(task_id, true);
    } catch (err) {
      console.error("创建任务失败:", err);
      alert("创建任务失败，请重试");
    } finally {
      setIsProcessing(false);
    }
  }, [files, connectSSE]);

  return (
    <div className="h-[calc(100vh-3.5rem)] flex flex-col">
      <SplitPanelLayout
        left={
          <div className="flex flex-col gap-3 h-full">
            {files.length === 0 ? (
              <UploadZone />
            ) : (
              <>
                <ImageViewer />
                <FilePreviewList />
              </>
            )}
            <UploadActions onStart={handleStart} loading={isProcessing} />
          </div>
        }
        right={<OutputPanel taskId={activeTaskId} />}
      />
      <ImageLightbox />
    </div>
  );
}

function HistoryPage() {
  const handleSelectTask = (taskId: string) => {
    window.location.hash = `#/?task=${taskId}`;
  };

  return (
    <div className="h-[calc(100vh-3.5rem)] bg-white">
      <TaskHistoryPage onSelectTask={handleSelectTask} />
    </div>
  );
}

export default function App() {
  return (
    <HashRouter>
      <div className="min-h-screen flex flex-col bg-gray-50">
        <AppHeader />
        <Routes>
          <Route path="/" element={<MainPage />} />
          <Route path="/history" element={<HistoryPage />} />
        </Routes>
      </div>
    </HashRouter>
  );
}
