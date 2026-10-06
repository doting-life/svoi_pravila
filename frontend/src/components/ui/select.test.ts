import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("select.css", () => {
  it("gives native select options explicit Telegram theme colours", () => {
    const css = readFileSync(resolve(__dirname, "select.css"), "utf8");
    expect(css).toMatch(/select option,\s*select optgroup\s*\{/);
    expect(css).toContain("background-color: var(--tgui--section_bg_color");
    expect(css).toContain("color: var(--tgui--text_color");
  });

  it("is loaded after the telegram-ui styles", () => {
    const layout = readFileSync(resolve(__dirname, "layout.tsx"), "utf8");
    expect(layout.indexOf('import "./select.css";')).toBeGreaterThan(
      layout.indexOf('import "@telegram-apps/telegram-ui/dist/styles.css";'),
    );
  });
});
