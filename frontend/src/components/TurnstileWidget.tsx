import { useEffect, useRef } from "react";

interface TurnstileApi {
  render: (
    container: HTMLElement,
    options: {
      sitekey: string;
      callback: (token: string) => void;
      "error-callback": () => void;
      "expired-callback": () => void;
      theme?: "light" | "dark";
      appearance?: "always" | "execute" | "interaction-only";
    },
  ) => string;
  reset: (widgetId: string) => void;
  remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
    onTurnstileReady?: () => void;
  }
}

const SCRIPT = "https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit";

interface TurnstileWidgetProps {
  siteKey: string;
  onToken: (token: string | null) => void;
  /** Bumped by the caller after each submission. A token is single-use and is
   *  bound to one challenge, so a second question must carry a second token —
   *  minting per session would hand a scraper one solve and every run after
   *  it. */
  resetSignal: number;
}

export function TurnstileWidget({ siteKey, onToken, resetSignal }: TurnstileWidgetProps) {
  const container = useRef<HTMLDivElement>(null);
  const widgetId = useRef<string | null>(null);
  const latest = useRef(onToken);
  latest.current = onToken;

  useEffect(() => {
    let cancelled = false;

    const mount = () => {
      if (cancelled || !container.current || !window.turnstile || widgetId.current) return;
      widgetId.current = window.turnstile.render(container.current, {
        sitekey: siteKey,
        callback: (token) => latest.current(token),
        "error-callback": () => latest.current(null),
        "expired-callback": () => latest.current(null),
        theme: "light",
      });
    };

    if (window.turnstile) {
      mount();
    } else {
      const existing = document.querySelector<HTMLScriptElement>(`script[src="${SCRIPT}"]`);
      const script = existing ?? document.createElement("script");
      script.src = SCRIPT;
      script.async = true;
      script.defer = true;
      script.addEventListener("load", mount);
      if (!existing) document.head.appendChild(script);
    }

    return () => {
      cancelled = true;
    };
  }, [siteKey]);

  useEffect(() => {
    if (resetSignal > 0 && widgetId.current && window.turnstile) {
      window.turnstile.reset(widgetId.current);
      latest.current(null);
    }
  }, [resetSignal]);

  return <div ref={container} className="min-h-[65px]" />;
}
