require("node:fs").mkdirSync("work/browser", { recursive: true });
const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const page = await b.newPage({ viewport: { width: 1440, height: 1000 } });
  const errors = [];
  page.on("pageerror", (e) => errors.push(e.message));
  await page.goto("http://127.0.0.1:5173");
  await page
    .getByRole("button", { name: "Find common issues", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Start analysis", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "What customers are saying" })
    .waitFor();
  await page.screenshot({
    path: "work/browser/frontend-dashboard.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: /Battery loses capacity: 42/ })
    .click();
  await page
    .getByRole("button", { name: "Read full review", exact: true })
    .click();
  await page.getByText("Sample review", { exact: true }).waitFor();
  await page.screenshot({
    path: "work/browser/frontend-evidence.png",
    fullPage: true,
  });
  await page
    .getByRole("button", { name: "Add guidance about this issue" })
    .click();
  await page
    .getByLabel("What should the team know?")
    .fill("Separate long term wear from short runtime on a charge.");
  await page
    .getByRole("button", { name: "Save guidance", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "Your guidance is saved." })
    .waitFor();
  await page.getByRole("button", { name: "Check progress" }).click();
  await page.getByText("Ready", { exact: true }).waitFor();
  await page.getByRole("button", { name: "Close dialog" }).click();
  await page
    .getByRole("button", { name: "Find common issues", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Start analysis", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "What customers are saying" })
    .waitFor();
  await page.getByText("Previous guidance used", { exact: true }).click();
  await page
    .getByText("Separate long term wear from short runtime on a charge.", {
      exact: true,
    })
    .waitFor();
  await page.getByRole("button", { name: /Selected product/ }).click();
  await page.getByRole("button", { name: /Arc Portable Speaker/ }).click();
  if (
    await page
      .getByRole("heading", { name: "What customers are saying" })
      .count()
  )
    throw Error("Leaked previous product report");
  await page
    .getByRole("button", { name: "Write a review", exact: true })
    .last()
    .click();
  await page.getByRole("radio", { name: "4 stars" }).check();
  await page.getByLabel("Review title", { exact: true }).fill("Great speaker");
  await page
    .getByLabel("Your experience", { exact: true })
    .fill("Good sound in a small room.");
  await page.screenshot({
    path: "work/browser/frontend-reviewer.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Submit review" }).click();
  await page.getByRole("heading", { name: "Your review is saved." }).waitFor();
  await page.setViewportSize({ width: 390, height: 844 });
  await page.screenshot({
    path: "work/browser/frontend-mobile-reviewer.png",
    fullPage: true,
  });
  await page.getByRole("button", { name: "Back to feedback" }).click();
  await page
    .getByRole("button", { name: "Find common issues", exact: true })
    .click();
  await page
    .getByRole("button", { name: "Start analysis", exact: true })
    .click();
  await page
    .getByRole("heading", { name: "What customers are saying" })
    .waitFor();
  await page.screenshot({
    path: "work/browser/frontend-mobile-dashboard.png",
    fullPage: true,
  });
  if (
    await page.evaluate(() => document.documentElement.scrollWidth > innerWidth)
  )
    throw Error("Mobile horizontal overflow");
  console.log(
    JSON.stringify({
      errors,
      flow: "passed",
      productIsolation: "passed",
      mobile: "passed",
    }),
  );
  await b.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
