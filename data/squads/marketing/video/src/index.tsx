import { registerRoot, Composition } from "remotion";
import { TopicHighlight, type TopicHighlightProps } from "./TopicHighlight";
import { StatCard, type StatCardProps } from "./StatCard";

const DEFAULT_TOPIC: TopicHighlightProps = {
  topicTitle: "Building with AI Agents",
  hook: "Everyone is writing specs.\nWe're running the economy.",
  stats: [
    { label: "Agents on the bus", value: "19", color: "#D4A017" },
    { label: "MCP servers", value: "1,000+", color: "#06B6D4" },
    { label: "GitHub stars (MCP)", value: "83,400", color: "#D4A017" },
    { label: "Economy wires", value: "7", color: "#06B6D4" },
  ],
  insight: "The unsolved problems — orchestration,\nobservability, brittle topologies —\nwe've built working answers to all three.",
  cta: "mumega.com/topics/building-with-ai-agents",
};

const DEFAULT_STAT: StatCardProps = {
  value: "83,400",
  label: "GitHub stars on MCP",
  sublabel: "1,000+ production servers",
  color: "#D4A017",
};

const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="TopicHighlight"
        component={TopicHighlight}
        durationInFrames={1800}
        fps={30}
        width={1080}
        height={1920}
        defaultProps={DEFAULT_TOPIC}
      />
      <Composition
        id="StatCard"
        component={StatCard}
        durationInFrames={450}
        fps={30}
        width={1080}
        height={1080}
        defaultProps={DEFAULT_STAT}
      />
    </>
  );
};

registerRoot(RemotionRoot);
