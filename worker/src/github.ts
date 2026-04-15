/**
 * GitHub REST API helpers for the Worker's scheduled handler.
 *
 * The Worker's cron triggers dispatch GitHub Actions workflow runs via
 * `POST /repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches`,
 * which is the workflow_dispatch event. This sidesteps GitHub's flaky
 * scheduled-trigger cron (often 15–60 min late) in favour of Cloudflare's
 * sub-minute cron accuracy.
 */

export interface DispatchWorkflowOptions {
  /** `owner/repo`, e.g. `impravin22/pravys-market-bot`. */
  repo: string;
  /** Workflow filename as it exists in `.github/workflows/`, e.g. `market-pulse-morning.yml`. */
  workflow: string;
  /** Branch or tag ref to run against. Defaults to `main`. */
  ref?: string;
  /** Fine-grained PAT or GitHub App installation token with `actions:write` on the repo. */
  token: string;
  /**
   * Optional `fetch` override for tests. Defaults to `globalThis.fetch`.
   * Must be bound or arrow — a bare reference to `globalThis.fetch` loses
   * its `this`, which Cloudflare's runtime rejects with "Illegal invocation".
   */
  fetchImpl?: typeof fetch;
}

/**
 * Dispatches a workflow run. Returns once GitHub has accepted the request.
 *
 * Throws `DispatchError` on any non-204 response, including 401 (bad token),
 * 404 (bad repo or workflow path), or 422 (ref doesn't exist on repo).
 */
export async function dispatchWorkflow(options: DispatchWorkflowOptions): Promise<void> {
  const { repo, workflow, token } = options;
  const ref = options.ref ?? "main";
  const fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args));

  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) {
    throw new DispatchError(`invalid repo slug: ${repo}`, 0);
  }
  if (!/^[\w.-]+\.ya?ml$/.test(workflow)) {
    throw new DispatchError(`invalid workflow filename: ${workflow}`, 0);
  }

  const url = `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`;
  const resp = await fetchImpl(url, {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${token}`,
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "pravys-market-bot-worker",
      "Content-Type": "application/json",
    },
    body: JSON.stringify({ ref }),
  });

  // GitHub returns 204 No Content on success. Anything else is a real error
  // we want to see in Worker logs so cron failures don't stay silent.
  if (resp.status !== 204) {
    const body = await safeReadText(resp);
    throw new DispatchError(
      `dispatch failed: ${resp.status} ${resp.statusText} — ${body}`,
      resp.status,
    );
  }
}

export class DispatchError extends Error {
  public readonly status: number;
  constructor(message: string, status: number) {
    super(message);
    this.name = "DispatchError";
    this.status = status;
  }
}

async function safeReadText(resp: Response): Promise<string> {
  try {
    const text = await resp.text();
    // Cap the body so a huge HTML error page doesn't flood Worker logs.
    return text.length > 500 ? `${text.slice(0, 500)}…` : text;
  } catch (exc) {
    // Surface the read failure at warn level — otherwise a transient
    // body-read glitch looks identical to GitHub returning an empty body.
    console.warn("safeReadText: body read failed", (exc as Error).message);
    return "<unreadable body>";
  }
}
