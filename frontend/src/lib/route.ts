import { useEffect, useState } from "react";

/**
 * Four views and a hash. No router dependency, because everything a router
 * would buy here — nested layouts, loaders, code splitting across dozens of
 * routes — is for an application this deliberately is not. Hash routing also
 * means Cloudflare Pages needs no SPA rewrite rule to serve a deep link.
 */
export type Route =
  | { name: "landing"; runId: string | null }
  | { name: "ask" }
  | { name: "run"; jobId: string };

export function parseRoute(hash: string): Route {
  const path = hash.replace(/^#\/?/, "").split("/");
  if (path[0] === "ask") return { name: "ask" };
  if (path[0] === "run" && path[1]) return { name: "run", jobId: path[1] };
  if (path[0] === "recorded" && path[1]) return { name: "landing", runId: path[1] };
  return { name: "landing", runId: null };
}

export function navigate(to: string): void {
  window.location.hash = to;
}

export function useRoute(): Route {
  const [route, setRoute] = useState<Route>(() => parseRoute(window.location.hash));
  useEffect(() => {
    const onChange = () => {
      setRoute(parseRoute(window.location.hash));
      window.scrollTo({ top: 0 });
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return route;
}
