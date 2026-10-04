import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { init } from "@tma.js/sdk-react";

vi.mock("@tma.js/sdk-react", () => ({
  init: vi.fn(),
}));

import { getLaunchLanguage, getRawInitData, initTelegram, resolveDevInitData, useBackButton } from "./sdk";

describe("telegram adapter", () => {
  beforeEach(() => {
    window.Telegram = undefined;
    vi.mocked(init).mockReset();
  });

  afterEach(() => {
    vi.unstubAllEnvs();
    vi.restoreAllMocks();
  });

  function launchParamsError(): Error {
    const error = new Error(
      "Unable to retrieve launch parameters from any known source. Perhaps, you have opened your app outside Telegram?",
    );
    error.name = "LaunchParamsRetrieveError";
    return error;
  }

  it("does not throw when opened outside Telegram", () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    vi.mocked(init).mockImplementation(() => {
      throw launchParamsError();
    });

    expect(() => initTelegram()).not.toThrow();
  });

  it("still calls ready/expand when the SDK init fails but WebApp is available", () => {
    vi.spyOn(console, "warn").mockImplementation(() => undefined);
    vi.mocked(init).mockImplementation(() => {
      throw launchParamsError();
    });
    const ready = vi.fn();
    const expand = vi.fn();
    window.Telegram = { WebApp: { ready, expand } };

    initTelegram();

    expect(ready).toHaveBeenCalledOnce();
    expect(expand).toHaveBeenCalledOnce();
  });

  it("returns null initData in production outside Telegram", () => {
    vi.stubEnv("DEV", false);
    vi.stubEnv("VITE_DEV_INIT_DATA", "dev_data");

    expect(getRawInitData()).toBeNull();
  });

  it("reads initData from Telegram web app", () => {
    window.Telegram = { WebApp: { initData: "raw_init_data" } };
    expect(getRawInitData()).toBe("raw_init_data");
  });

  it("uses ru fallback when language is unknown", () => {
    expect(getLaunchLanguage()).toBe("ru");
  });

  it("registers and cleans up back button handler", () => {
    const onClick = vi.fn();
    const offClick = vi.fn();
    const show = vi.fn();
    const hide = vi.fn();

    window.Telegram = {
      WebApp: {
        BackButton: { onClick, offClick, show, hide },
      },
    };

    const handler = vi.fn();
    const cleanup = useBackButton(handler);

    expect(show).toHaveBeenCalledOnce();
    expect(onClick).toHaveBeenCalledWith(handler);

    cleanup();

    expect(offClick).toHaveBeenCalledWith(handler);
    expect(hide).toHaveBeenCalledOnce();
  });

  it("initializes Telegram web app shell", () => {
    const ready = vi.fn();
    const expand = vi.fn();
    window.Telegram = { WebApp: { ready, expand } };

    initTelegram();

    expect(init).toHaveBeenCalledOnce();
    expect(ready).toHaveBeenCalledOnce();
    expect(expand).toHaveBeenCalledOnce();
  });

  it("ignores development fallback when not in development mode", () => {
    expect(resolveDevInitData(false, "dev_data")).toBeNull();
    expect(resolveDevInitData(true, "dev_data")).toBe("dev_data");
  });
});
