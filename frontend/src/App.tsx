import { useRoute } from "./lib/route";
import { Ask } from "./views/Ask";
import { Landing } from "./views/Landing";
import { RunStatus } from "./views/RunStatus";

export function App() {
  const route = useRoute();
  return (
    <div className="min-h-full bg-paper">
      <Masthead current={route.name} />
      {route.name === "ask" ? <Ask /> : null}
      {route.name === "run" ? <RunStatus jobId={route.jobId} /> : null}
      {route.name === "landing" ? <Landing runId={route.runId} /> : null}
      <Footer />
    </div>
  );
}

function Masthead({ current }: { current: string }) {
  return (
    <header className="sticky top-0 z-40 border-b border-rule bg-paper/90 backdrop-blur">
      <div className="mx-auto flex max-w-3xl items-baseline gap-4 px-5 py-3 md:px-8">
        <a
          href="#/"
          className="font-mono text-[13px] font-medium tracking-tight text-ink hover:text-verified"
        >
          fie
        </a>
        <p className="hidden font-mono text-[11px] uppercase tracking-wider text-slate sm:block">
          every claim, checked against the filing
        </p>
        <nav className="ml-auto flex gap-4 font-mono text-[11px] uppercase tracking-wider">
          <a
            href="#/"
            className={current === "landing" ? "text-ink" : "text-slate hover:text-ink"}
          >
            Runs
          </a>
          <a href="#/ask" className={current === "ask" ? "text-ink" : "text-slate hover:text-ink"}>
            Ask
          </a>
        </nav>
      </div>
    </header>
  );
}

function Footer() {
  return (
    <footer className="border-t border-rule">
      <div className="mx-auto max-w-3xl px-5 py-6 font-mono text-[11px] leading-relaxed text-slate md:px-8">
        Answers are drawn only from SEC filings for Tesla, Apple and Nvidia held in this corpus.
        Nothing here is investment advice.
      </div>
    </footer>
  );
}
