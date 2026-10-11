import { BackendMarquee } from "@/components/marketing/backend-marquee";
import { ComparisonArms } from "@/components/marketing/comparison-arms";
import { Guardrails } from "@/components/marketing/guardrails";
import { Hero } from "@/components/marketing/hero";
import { HeroRail } from "@/components/marketing/hero-rail";
import { HierarchyProblem } from "@/components/marketing/hierarchy-problem";
import { Pipeline } from "@/components/marketing/pipeline";
import { Reveal } from "@/components/marketing/reveal";
import { RunLocally } from "@/components/marketing/run-locally";
import { SiteFooter } from "@/components/marketing/site-footer";
import { SiteNav } from "@/components/marketing/site-nav";
import { ToolSignatures } from "@/components/marketing/tool-signatures";

/**
 * The hero is intentionally outside every Reveal: it is the first paint and
 * must never arrive hidden behind an observer callback.
 *
 * No `<main>` here. The root layout already owns the single main landmark and
 * the skip-link target, so a second one would ship an invalid document.
 */
export default function Home() {
  return (
    <>
      <SiteNav />
      <div className="fixed top-1/2 left-0 z-30 hidden -translate-y-1/2 lg:block">
        <HeroRail />
      </div>
      <div className="flex-1 lg:pl-16">
        <Hero />
        <BackendMarquee />
        <Reveal>
          <HierarchyProblem />
        </Reveal>
        <Reveal>
          <Pipeline />
        </Reveal>
        <Reveal>
          <ToolSignatures />
        </Reveal>
        <Reveal>
          <ComparisonArms />
        </Reveal>
        <Reveal>
          <Guardrails />
        </Reveal>
        <Reveal>
          <RunLocally />
        </Reveal>
      </div>
      <SiteFooter />
    </>
  );
}