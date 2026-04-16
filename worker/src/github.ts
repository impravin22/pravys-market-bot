/**
 * GitHub REST API helpers for the Worker's scheduled handler.
 *
 * The Worker's cron triggers dispatch GitHub Actions workflow runs via
 * `POST /repos/{owner}/{repo}/actions/workflows/{workflow}/dispatches`,
 * which is the workflow_dispatch event. This sidesteps GitHub's flaky
 * scheduled-trigger cron (often 15–60 min late) in favour of Cloudflare's
 * sub-minute cron accuracy.
 */

/** Default ref when `options.ref` is omitted. */
const DEFAULT_REF = "main";
/** Hard cap on how long a dispatch call may wait before we bail. */
const DISPATCH_TIMEOUT_MS = 10_000;
/** Max body bytes surfaced in error messages so Worker logs stay readable. */
const ERROR_BODY_MAX_CHARS = 500;

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
   * Optional `fetch` override for tests. Production callers should omit this;
   * the default wraps `globalThis.fetch` in an arrow (a bare reference would
   * lose its `this` binding and Cloudflare's runtime rejects the call with
   * "Illegal invocation").
   */
  fetchImpl?: typeof fetch;
  /**
   * Milliseconds before the dispatch is aborted. Defaults to
   * {@link DISPATCH_TIMEOUT_MS}. Setting `0` disables the timeout (tests only).
   */
  timeoutMs?: number;
}

/**
 * Dispatches a workflow run. Returns once GitHub has accepted the request.
 *
 * Throws `DispatchError` on any non-204 response, including 401 (bad token),
 * 404 (bad repo or workflow path), or 422 (ref doesn't exist on repo).
 * Throws with `status: 0` on pre-flight validation failures (empty token,
 * bad repo slug, bad workflow filename) or when the request aborts on timeout.
 */
export async function dispatchWorkflow(options: DispatchWorkflowOptions): Promise<void> {
  const { repo, workflow, token } = options;
  const ref = options.ref ?? DEFAULT_REF;
  const timeoutMs = options.timeoutMs ?? DISPATCH_TIMEOUT_MS;
  const fetchImpl = options.fetchImpl ?? ((...args) => fetch(...args));

  if (!token) {
    throw new DispatchError("GITHUB_DISPATCH_TOKEN is empty or unset", 0);
  }
  if (!/^[\w.-]+\/[\w.-]+$/.test(repo)) {
    throw new DispatchError(`invalid repo slug: ${repo}`, 0);
  }
  if (!/^[\w.-]+\.ya?ml$/.test(workflow)) {
    throw new DispatchError(`invalid workflow filename: ${workflow}`, 0);
  }

  const url = `https://api.github.com/repos/${repo}/actions/workflows/${workflow}/dispatches`;
  const signalOpt = timeoutMs > 0 ? { signal: AbortSignal.timeout(timeoutMs) } : {};

  let resp: Response;
  try {
    resp = await fetchImpl(url, {
      method: "POST",
      headers: {
        Accept: "application/vnd.github+json",
        Authorization: `Bearer ${token}`,
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "pravys-market-bot-worker",
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ ref }),
      ...signalOpt,
    });
  } catch (exc) {
    // Covers AbortError (timeout) and low-level network errors. Re-wrapping
    // as DispatchError keeps the scheduled() caller's error handling uniform.
    const message = exc instanceof Error ? exc.message : String(exc);
    throw new DispatchError(`dispatch network error: ${message}`, 0);
  }

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
    return text.length > ERROR_BODY_MAX_CHARS
      ? `${text.slice(0, ERROR_BODY_MAX_CHARS)}…`
      : text;
  } catch (exc) {
    // Log the raw error so the stack trace survives; a body read failure
    // masking the real GitHub error body is the kind of thing that makes
    // 422 "no ref found" debugging impossible otherwise.
    console.warn(`safeReadText: body read failed for status ${resp.status}`, exc);
    return "<unreadable body>";
  }
}
