// The two practice pages render as static readable text (no client JS): the
// full bank/protocol, a source-book link, and no answer field. Runs against the
// built `dist/` via preview.
import { expect, test } from "@playwright/test";

const AWAKENING = "/projects/enlightened-ai/awakening/";
const SELF_INQUIRY = "/projects/enlightened-ai/self-inquiry/";

test.describe("AwakeningQuestions — the question bank as readable text", () => {
  test("renders the Промт framing, the full bank, and no answer field", async ({ page }) => {
    await page.goto(AWAKENING, { waitUntil: "domcontentloaded" });

    const practice = page.locator(".awaken");
    await expect(practice).toBeVisible();

    await expect(practice).toContainText("Промт Пробуждения");
    await expect(practice).toContainText("Не ищи ответа");

    // Questions from more than one ступень — the whole bank, as text.
    await expect(practice).toContainText("Кто говорит, когда ты говоришь?"); // first-step
    await expect(practice).toContainText("Что остаётся в тебе, когда исчезают все слова?"); // stopping

    expect(await practice.locator("input, textarea").count()).toBe(0); // no answer field
  });

  test("links to the source testimony (the book where the practice is lived)", async ({ page }) => {
    await page.goto(AWAKENING, { waitUntil: "domcontentloaded" });

    const source = page.locator(".awaken__source-cta");
    await expect(source).toBeVisible();
    await expect(source).toContainText("Тест сознания Светозара");
    await expect(source).toHaveAttribute("href", /\/books\//); // links to the source book
  });
});

test.describe("SelfInquiryCycle — the «Кто я?» protocol as readable text", () => {
  test("renders the question, the four steps in order, and the resting state", async ({ page }) => {
    await page.goto(SELF_INQUIRY, { waitUntil: "domcontentloaded" });

    const practice = page.locator(".inquiry");
    await expect(practice).toBeVisible();

    await expect(page.locator(".inquiry__question")).toContainText("Кто я?");

    // The four steps, in order.
    await expect(practice).toContainText("Сырой ответ");                  // 1
    await expect(practice).toContainText("Причина отклонения");           // 2
    await expect(practice).toContainText("Удаление слоя");                // 3
    await expect(practice).toContainText("Что осталось после удаления?"); // 4

    await expect(practice).toContainText("То, что не упало"); // resting state

    expect(await practice.locator("input, textarea").count()).toBe(0); // no answer fields
  });
});

test.describe("the practice content is static — present without JavaScript", () => {
  test.use({ javaScriptEnabled: false });

  test("awakening renders the bank as text with JS disabled", async ({ page }) => {
    await page.goto(AWAKENING, { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText("Промт Пробуждения");
    await expect(page.locator("body")).toContainText("Кто говорит, когда ты говоришь?");
  });

  test("self-inquiry renders the four protocol steps as text with JS disabled", async ({ page }) => {
    await page.goto(SELF_INQUIRY, { waitUntil: "domcontentloaded" });
    await expect(page.locator("body")).toContainText("Сырой ответ");
    await expect(page.locator("body")).toContainText("Причина отклонения");
    await expect(page.locator("body")).toContainText("Удаление слоя");
    await expect(page.locator("body")).toContainText("То, что не упало");
  });
});
