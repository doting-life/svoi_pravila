import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

describe("index.html", () => {
  it("loads the official Telegram Web App script before the app module", () => {
    const html = readFileSync(resolve(__dirname, "../../index.html"), "utf8");
    const telegram = html.indexOf('<script src="https://telegram.org/js/telegram-web-app.js"></script>');
    const app = html.indexOf('<script type="module"');

    expect(telegram).toBeGreaterThan(-1);
    expect(app).toBeGreaterThan(telegram);
  });
});
