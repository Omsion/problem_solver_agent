import type { ReactNode } from "react";
import { Group, Panel, Separator } from "react-resizable-panels";
import { useLayoutStore } from "../../stores/useLayoutStore";
import { useIsMobile } from "../../hooks/useMediaQuery";
import { MobileLayout } from "./MobileLayout";

interface Props {
  left: ReactNode;
  right: ReactNode;
}

export const SplitPanelLayout = ({ left, right }: Props) => {
  const isMobile = useIsMobile();
  const setRatio = useLayoutStore((s) => s.setLeftPanelRatio);

  if (isMobile) {
    return <MobileLayout left={left} right={right} />;
  }

  return (
    <Group orientation="horizontal" className="flex-1 min-h-0">
      <Panel
        defaultSize={45}
        minSize={30}
        onResize={({ asPercentage }) => setRatio(asPercentage)}
        className="bg-white"
      >
        <div className="h-full overflow-auto p-4">{left}</div>
      </Panel>

      <Separator className="w-1 bg-gray-200 hover:bg-indigo-400 transition-colors cursor-col-resize relative group">
        <div className="absolute inset-y-0 -left-1 -right-1" />
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-1 h-8 rounded-full bg-gray-300 group-hover:bg-indigo-500 transition-colors" />
      </Separator>

      <Panel defaultSize={55} minSize={30} className="bg-white">
        <div className="h-full overflow-hidden flex flex-col">{right}</div>
      </Panel>
    </Group>
  );
};
