import { createAvatar } from "@dicebear/core";
import { bottts, notionists } from "@dicebear/collection";
import { useMemo } from "react";

// Robot avatars (Bottts by Pablo Stanley, free for personal and commercial use), generated locally from the
// agent's name: the same agent always gets the same face. People (Production) get a drawn person instead.
const STAGE_BG: Record<string, string> = {
  requirements: "b6e3f4", design: "c0aede", planning: "d1f4d9", development: "ffd5dc", cicd: "e5e7eb",
  qa: "ffdfbf", release: "c7d2fe", production: "fde68a", monitoring: "e5e7eb",
};

// every Bottts eye style except "dizzy" (crossed-out eyes read as a broken agent)
const FRIENDLY_EYES = ["bulging", "eva", "frame1", "frame2", "glow", "happy", "hearts", "robocop", "round",
  "roundFrame01", "roundFrame02", "sensor", "shade01"] as const;

export function Avatar({ seed, stage, size = 40, person = false, working = false, title }: {
  seed: string; stage: string; size?: number; person?: boolean; working?: boolean; title?: string;
}) {
  const uri = useMemo(
    () => {
      const opts = { seed, size: size * 2, radius: 50, backgroundColor: [STAGE_BG[stage] ?? "e5e7eb"] };
      return (person ? createAvatar(notionists, opts) : createAvatar(bottts, { ...opts, eyes: [...FRIENDLY_EYES] })).toDataUri();
    },
    [seed, stage, size, person],
  );
  return (
    <span className={`avatar ${working ? "working" : ""}`} style={{ width: size, height: size }} title={title}>
      <img src={uri} width={size} height={size} alt="" />
    </span>
  );
}

const PREFIX_STAGE: Record<string, string> = {
  req: "requirements", design: "design", plan: "planning", dev: "development", qa: "qa", rel: "release",
};

/** The stage an agent belongs to, from its name: "qa-uat-tester" → "qa". */
export const stageOfAgent = (name: string) =>
  PREFIX_STAGE[name.replace(/^(agentic-sdlc|sdlc-pilot):/, "").split("-")[0]] ?? "cicd";

/** A sub-agent's avatar: the same face everywhere the agent appears (seeded by its short name). */
export function AgentAvatar({ name, size = 28, working = false }: { name: string; size?: number; working?: boolean }) {
  const short = name.replace(/^(agentic-sdlc|sdlc-pilot):/, "");
  return <Avatar seed={short} stage={stageOfAgent(short)} size={size} working={working} title={short} />;
}
