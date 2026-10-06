import type { ConsentRequiredError, ValidationError } from "./types";

/**
 * Raised for every non-2xx response from the Mini App API, and for a missing
 * Telegram initData (status 401) before any request is sent.
 *
 * `detail` is always a human-readable string. For FastAPI request validation
 * failures (422) the structured list is exposed via `validationErrors`.
 */
export class MiniAppApiError extends Error {
  readonly status: number;

  readonly detail: string;

  readonly validationErrors: ValidationError[];

  readonly body: unknown;

  constructor(status: number, detail: string, options: { body?: unknown; validationErrors?: ValidationError[] } = {}) {
    super(detail);
    this.name = "MiniAppApiError";
    this.status = status;
    this.detail = detail;
    this.body = options.body ?? null;
    this.validationErrors = options.validationErrors ?? [];
  }

  get isUnauthorized(): boolean {
    return this.status === 401;
  }

  get isNotFound(): boolean {
    return this.status === 404;
  }

  get isValidationError(): boolean {
    return this.status === 422;
  }

  get isNotConfigured(): boolean {
    return this.status === 503;
  }

  /** 403 with the ConsentRequiredError body: consent must be given in the Telegram bot via /start. */
  get isConsentRequired(): boolean {
    return this.status === 403 && isConsentRequiredBody(this.body);
  }
}

export const CONSENT_REQUIRED_ERROR = "consent_required";

/** Exact runtime shape of the Mini App 403 ConsentRequiredError body. */
export function isConsentRequiredBody(body: unknown): body is ConsentRequiredError {
  if (!body || typeof body !== "object") return false;
  const value = body as Record<string, unknown>;
  return value.error === CONSENT_REQUIRED_ERROR && typeof value.message === "string" && value.bot_command === "/start";
}

export function isConsentRequiredError(err: unknown): boolean {
  return err instanceof MiniAppApiError && err.isConsentRequired;
}

export const MISSING_INIT_DATA_DETAIL = "Missing Telegram initData";
export const DEFAULT_ERROR_DETAIL = "Request failed";
export const VALIDATION_ERROR_DETAIL = "Request validation failed";

function isValidationErrorItem(value: unknown): value is ValidationError {
  if (!value || typeof value !== "object") return false;
  const item = value as Record<string, unknown>;
  return Array.isArray(item.loc) && typeof item.msg === "string" && typeof item.type === "string";
}

/** Builds a MiniAppApiError from an HTTP status and parsed JSON body (or null). */
export function errorFromResponse(status: number, body: unknown): MiniAppApiError {
  const detail = body && typeof body === "object" ? (body as { detail?: unknown }).detail : undefined;
  if (typeof detail === "string") {
    return new MiniAppApiError(status, detail, { body });
  }
  if (isConsentRequiredBody(body)) {
    return new MiniAppApiError(status, body.message, { body });
  }
  if (Array.isArray(detail)) {
    const validationErrors = detail.filter(isValidationErrorItem);
    return new MiniAppApiError(status, VALIDATION_ERROR_DETAIL, { body, validationErrors });
  }
  return new MiniAppApiError(status, DEFAULT_ERROR_DETAIL, { body });
}

/**
 * Maps any thrown value to an HTTP-like status:
 * MiniAppApiError -> its status, network failure (TypeError from fetch) -> 0, anything else -> 500.
 */
export function toErrorStatus(err: unknown): number {
  if (err instanceof MiniAppApiError) {
    return err.status;
  }
  if (err instanceof TypeError) {
    return 0;
  }
  return 500;
}
