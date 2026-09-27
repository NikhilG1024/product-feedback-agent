require("node:fs").mkdirSync("work/browser", { recursive: true });
const { chromium } = require("playwright");
(async () => {
  const b = await chromium.launch({ channel: "chrome", headless: true });
  const p = await b.newPage({ viewport: { width: 1400, height: 1000 } });
  const errors = [];
  p.on("pageerror", (e) => errors.push(e.message));
  await p.goto("http://127.0.0.1:5174");
  await p.getByRole("button", { name: "Connect API", exact: true }).click();
  await p.getByLabel("Access token").fill("fixture-pm");
  await p.getByRole("button", { name: "Connect", exact: true }).click();
  await p
    .getByRole("button", { name: /Selected product.*Contract test product 0/ })
    .waitFor();
  await p.getByRole("button", { name: /Selected product/ }).click();
  await p.getByRole("button", { name: "Load more products" }).click();
  await p.getByRole("button", { name: /Contract test product 34/ }).click();
  await p
    .getByRole("button", { name: "Find common issues", exact: true })
    .click();
  await p
    .getByRole("combobox", { name: "Review group", exact: true })
    .selectOption("contract:A");
  await p.getByRole("button", { name: "Start analysis", exact: true }).click();
  await p
    .getByText("One review reports a short battery runtime.", { exact: true })
    .waitFor();
  await p.getByText("Ask about these reviews", { exact: true }).click();
  await p
    .getByLabel("Your question", { exact: true })
    .fill("What supports the issue?");
  await p.getByRole("button", { name: "Ask", exact: true }).click();
  await p
    .getByText("The review reports a two-hour battery runtime.", {
      exact: true,
    })
    .waitFor();
  await p.getByRole("button", { name: /Short battery runtime: 1/ }).click();
  await p
    .getByRole("button", { name: "Read full review", exact: true })
    .click();
  await p.getByText("Battery concern", { exact: true }).waitFor();
  await p
    .getByRole("button", { name: "Add guidance about this issue" })
    .click();
  await p
    .getByLabel("What should the team know?")
    .fill("Verify the runtime measurement.");
  await p.getByRole("button", { name: "Save guidance", exact: true }).click();
  await p.getByRole("heading", { name: "Your guidance is saved." }).waitFor();
  await p.getByRole("button", { name: "Close dialog" }).click();
  await p.getByRole("button", { name: "Change account" }).click();
  await p.getByLabel("Access token").fill("fixture-reviewer");
  await p.getByRole("button", { name: "Connect", exact: true }).click();
  await p
    .getByRole("button", { name: "Write a review", exact: true })
    .last()
    .click();
  await p.getByRole("radio", { name: "2 stars" }).check();
  await p.getByLabel("Review title", { exact: true }).fill("Runtime concern");
  await p
    .getByLabel("Your experience", { exact: true })
    .fill("The battery only lasts two hours.");
  await p.getByRole("button", { name: "Submit review", exact: true }).click();
  await p.getByRole("heading", { name: "Your review is saved." }).waitFor();
  await p.getByRole("button", { name: "Check progress", exact: true }).click();
  await p.getByRole("button", { name: "Back to feedback" }).click();
  await p
    .getByRole("button", { name: "Find common issues", exact: true })
    .click();
  await p.getByRole("button", { name: "Start analysis", exact: true }).click();
  await p.getByRole("alert").filter({ hasText: "permission" }).waitFor();
  await p.keyboard.press("Escape");
  await p
    .getByRole("button", { name: "Find common issues", exact: true })
    .click();
  await p.getByRole("heading", { name: "Find common issues" }).waitFor();
  console.log(
    JSON.stringify({
      errors,
      realFastApiRoutes: "passed",
      catalogPagination: 35,
      pmAnalysisEvidenceGuidance: "passed",
      questions: "passed",
      reviewerSubmissionStatus: "passed",
      serverRoleDenial: "passed",
      providers: "deterministic in-memory fixtures; no Mongo/LLM/Hindsight",
    }),
  );
  await b.close();
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
