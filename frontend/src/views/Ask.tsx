import { useState } from "react";

import { TurnstileWidget } from "../components/TurnstileWidget";
import { ApiError, MOCK, TURNSTILE_SITE_KEY, submitQuestion } from "../lib/client";
import { navigate } from "../lib/route";

const TICKERS = ["TSLA", "AAPL", "NVDA"] as const;

/** From the evaluation set, so the demo shows questions the run was scored on
 *  rather than ones picked to flatter it. The last is deliberately outside the
 *  filings: watching the engine decline is the point of it. */
const EXAMPLES: { question: string; note: string }[] = [
  {
    question:
      "Compare R&D spending as a share of revenue across Tesla, Apple, and Nvidia in their most recent fiscal years.",
    note: "three companies, one ratio",
  },
  {
    question:
      "How does Nvidia characterize the impact of export controls on its business with China?",
    note: "risk-factor prose",
  },
  {
    question:
      "Compare Apple's services revenue growth rate to its products revenue growth rate over the last two fiscal years.",
    note: "growth from two filings",
  },
  {
    question: "What is Apple's current market capitalization and today's stock price?",
    note: "not in any filing — watch it refuse",
  },
];

export function Ask() {
  const [question, setQuestion] = useState("");
  const [companies, setCompanies] = useState<string[]>([]);
  const [token, setToken] = useState<string | null>(null);
  const [resetSignal, setResetSignal] = useState(0);
  const [failure, setFailure] = useState<ApiError | null>(null);
  const [sending, setSending] = useState(false);

  const needsToken = Boolean(TURNSTILE_SITE_KEY) && !MOCK;
  const ready = question.trim().length > 0 && (!needsToken || Boolean(token)) && !sending;

  const submit = async (event: React.FormEvent) => {
    event.preventDefault();
    if (!ready) return;
    setSending(true);
    setFailure(null);
    try {
      const accepted = await submitQuestion({
        question: question.trim(),
        companies,
        turnstileToken: token,
      });
      navigate(`/run/${accepted.job_id}`);
    } catch (error) {
      setFailure(
        error instanceof ApiError
          ? error
          : new ApiError("server", 0, "Something went wrong sending the question."),
      );
      // The token was spent whether or not the run started, so the next
      // attempt needs a fresh challenge.
      setResetSignal((n) => n + 1);
      setToken(null);
    } finally {
      setSending(false);
    }
  };

  return (
    <main className="mx-auto max-w-3xl px-5 pb-24 pt-10 md:px-8">
      <h1 className="font-serif text-2xl leading-snug text-ink md:text-[1.75rem]">
        Ask about a filing
      </h1>
      <p className="mt-3 max-w-prose text-[14px] leading-relaxed text-slate">
        The corpus is the 10-K and 10-Q filings of Tesla, Apple and Nvidia. Ask about anything else
        and the engine will tell you it cannot answer from what it holds — which you can watch it
        do with the last example below.
      </p>

      <form onSubmit={submit} className="mt-8">
        <label htmlFor="question" className="font-mono text-[11px] uppercase tracking-wider text-slate">
          Your question
        </label>
        <textarea
          id="question"
          value={question}
          onChange={(event) => setQuestion(event.target.value)}
          rows={3}
          maxLength={2000}
          placeholder="What was Tesla's operating income in fiscal year 2025?"
          className="mt-2 w-full resize-y border border-rule bg-raised p-3 font-serif text-[16px] leading-relaxed text-ink placeholder:text-slate/50 focus:border-verified focus:outline-none"
        />

        <fieldset className="mt-5">
          <legend className="font-mono text-[11px] uppercase tracking-wider text-slate">
            Limit to
          </legend>
          <div className="mt-2 flex flex-wrap gap-2">
            {TICKERS.map((ticker) => {
              const on = companies.includes(ticker);
              return (
                <button
                  key={ticker}
                  type="button"
                  aria-pressed={on}
                  onClick={() =>
                    setCompanies((current) =>
                      on ? current.filter((c) => c !== ticker) : [...current, ticker],
                    )
                  }
                  className={`border px-3 py-1.5 font-mono text-[12px] tracking-wide ${
                    on
                      ? "border-verified bg-verified-wash text-verified"
                      : "border-rule bg-raised text-slate hover:border-slate"
                  }`}
                >
                  {ticker}
                </button>
              );
            })}
            <span className="self-center font-mono text-[11px] text-slate/70">
              {companies.length === 0 ? "all three" : `${companies.length} selected`}
            </span>
          </div>
        </fieldset>

        {needsToken ? (
          <div className="mt-6">
            <TurnstileWidget
              siteKey={TURNSTILE_SITE_KEY}
              onToken={setToken}
              resetSignal={resetSignal}
            />
          </div>
        ) : null}

        {failure ? <Failure error={failure} onRetry={() => setResetSignal((n) => n + 1)} /> : null}

        <button
          type="submit"
          disabled={!ready}
          className="mt-6 border border-verified bg-verified px-5 py-2.5 font-mono text-[12px] uppercase tracking-wider text-white hover:border-ink hover:bg-ink disabled:cursor-not-allowed disabled:border-rule disabled:bg-rule disabled:text-slate"
        >
          {sending ? "Sending…" : "Ask"}
        </button>
        {needsToken && !token ? (
          <p className="mt-2 font-mono text-[11px] text-slate">
            Complete the check above to ask.
          </p>
        ) : null}
      </form>

      <section className="mt-12 border-t border-rule pt-6">
        <h2 className="font-mono text-[11px] uppercase tracking-wider text-slate">
          Questions from the evaluation set
        </h2>
        <ul className="mt-3 space-y-2">
          {EXAMPLES.map((example) => (
            <li key={example.question}>
              <button
                type="button"
                onClick={() => setQuestion(example.question)}
                className="w-full border border-rule bg-raised px-3 py-2.5 text-left hover:border-verified"
              >
                <span className="block text-[13px] leading-snug text-ink">{example.question}</span>
                <span className="mt-1 block font-mono text-[10px] uppercase tracking-wider text-slate">
                  {example.note}
                </span>
              </button>
            </li>
          ))}
        </ul>
      </section>
    </main>
  );
}

/**
 * A refusal is a screen, not a toast.
 *
 * Turnstile declining is the single most likely failure on the public path
 * and it is recoverable in one action, so it says what happened and offers
 * that action. A spend or quota limit carries the server's own wording, which
 * is written for a reader and already names what is still available.
 */
function Failure({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const heading =
    error.kind === "turnstile"
      ? "The browser check did not pass."
      : error.kind === "limited"
        ? "The engine is not taking this run."
        : error.kind === "rejected"
          ? "That question cannot be run as written."
          : "The question did not reach the engine.";

  return (
    <div data-tone="retracted" className="mt-6 border-l-2 border-[var(--tone)] bg-[var(--tone-wash)] px-4 py-3">
      <p className="text-[13px] font-semibold text-ink">{heading}</p>
      <p className="mt-1 text-[13px] leading-relaxed text-slate">{error.message}</p>
      {error.kind === "turnstile" ? (
        <button
          type="button"
          onClick={onRetry}
          className="mt-2 font-mono text-[12px] uppercase tracking-wider text-verified underline"
        >
          Try the check again
        </button>
      ) : null}
      {error.kind === "limited" ? (
        <a href="#/" className="mt-2 inline-block font-mono text-[12px] uppercase tracking-wider text-verified underline">
          Read a recorded run instead
        </a>
      ) : null}
    </div>
  );
}
