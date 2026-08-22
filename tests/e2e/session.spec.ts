import { expect, test } from "@playwright/test";

// One scripted end-to-end user session over the seeded 4-item queue.
// Order matters: state persists in the server across tests in this file,
// so the whole session lives in one test.
test("full review session", async ({ page }) => {
  await page.goto("/");

  // Initial load: stats visible, metrics absent in the e2e fixture context -> n/a.
  await expect(page.getByTestId("stats-pending")).toContainText("4");
  await expect(page.getByTestId("stats-approved")).toContainText("0");
  await expect(page.getByTestId("stats-rejected")).toContainText("0");
  await expect(page.getByTestId("stats-precision")).toContainText("n/a");

  // First pair renders: side-by-side fields and the agent panel.
  // record-a/record-b carry the column headers (source + record id); company
  // names render inside the field rows.
  await expect(page.getByTestId("queue-card")).toBeVisible();
  await expect(page.getByTestId("record-a")).toContainText("salesforce:SA-000001");
  await expect(page.getByTestId("record-b")).toContainText("marketo:MA-000007");
  const nameRow = page.getByTestId("field-row-company_name");
  await expect(nameRow).toContainText("Northwind Systems Inc.");
  await expect(nameRow).toContainText("Nortwhind Systems");
  await expect(page.getByTestId("field-row-domain")).toBeVisible();
  await expect(page.getByTestId("agent-panel")).toBeVisible();
  await expect(page.getByTestId("agent-confidence")).toContainText("0.79");
  await expect(page.getByTestId("agent-reason")).toContainText("same company");
  expect(await page.getByTestId("evidence-chip").count()).toBeGreaterThan(0);

  // API-error path: intercept the decision POST once; the UI must toast and stay put.
  await page.route("**/api/queue/*/decision", (route) =>
    route.fulfill({ status: 500, contentType: "application/json", body: '{"detail":"boom"}' })
  );
  await page.getByTestId("btn-approve").click();
  await expect(page.locator("[data-sonner-toast]")).toBeVisible();
  await expect(page.getByTestId("record-a")).toContainText("salesforce:SA-000001");
  await expect(page.getByTestId("stats-approved")).toContainText("0");
  await page.unroute("**/api/queue/*/decision");

  // Approve pair 1 for real — with a rapid double click. The double-submit
  // guard must produce exactly one POST and no error toast.
  const posts: string[] = [];
  page.on("request", (req) => {
    if (req.method() === "POST" && req.url().includes("/decision")) posts.push(req.url());
  });
  await expect(page.getByTestId("btn-approve")).toBeEnabled();
  await page.getByTestId("btn-approve").dblclick();
  await expect(page.getByTestId("stats-approved")).toContainText("1");
  await page.waitForTimeout(400);
  expect(posts.length).toBe(1);
  await expect(page.getByTestId("stats-pending")).toContainText("3");
  await expect(page.getByTestId("pending-ingestion-chip")).toBeVisible();

  // Pair 2 (shared-domain subsidiary): reject via button.
  await expect(page.getByTestId("field-row-company_name")).toContainText("Acme Logistics");
  await page.getByTestId("btn-reject").click();
  await expect(page.getByTestId("stats-rejected")).toContainText("1");

  // Keyboard shortcuts are ignored during the short settle window after a new
  // pair renders (double-submit guard), so wait for the action bar to re-enable.
  const ready = () => expect(page.getByTestId("btn-skip")).toBeEnabled();

  // Pair 3: skip via keyboard; it must resurface after the rest drain.
  await expect(page.getByTestId("field-row-company_name")).toContainText("Blue Peak Analytics");
  await ready();
  await page.keyboard.press("s");
  await expect(page.getByTestId("field-row-company_name")).toContainText("Iron Gate Security");

  // Pair 4 (franchise): reject via keyboard shortcut.
  await ready();
  await page.keyboard.press("r");
  await expect(page.getByTestId("stats-rejected")).toContainText("2");

  // Skipped pair resurfaces; approve it via keyboard.
  await expect(page.getByTestId("field-row-company_name")).toContainText("Blue Peak Analytics");
  await ready();
  await page.keyboard.press("a");

  // Queue drained.
  await expect(page.getByTestId("empty-state")).toBeVisible();
  await expect(page.getByTestId("stats-approved")).toContainText("2");
  await expect(page.getByTestId("stats-rejected")).toContainText("2");

  // Decisions persist across a reload.
  await page.reload();
  await expect(page.getByTestId("empty-state")).toBeVisible();
  await expect(page.getByTestId("stats-approved")).toContainText("2");
  await expect(page.getByTestId("stats-rejected")).toContainText("2");
  await expect(page.getByTestId("pending-ingestion-chip")).toBeVisible();
});

test("analyst role masks PII in the queue payload", async ({ page }) => {
  // Self-sufficient: ensure at least one approved item exists regardless of
  // test order, then compare the two role views of the same item.
  let approved = await (await page.request.get("/api/queue?status=approved&role=steward")).json();
  if (approved.total === 0) {
    const next = await page.request.get("/api/queue/next?role=steward");
    expect(next.status()).toBe(200);
    const item = await next.json();
    await page.request.post(`/api/queue/${item.id}/decision`, { data: { decision: "approve" } });
    approved = await (await page.request.get("/api/queue?status=approved&role=steward")).json();
  }
  expect(approved.items[0].record_a.phone).not.toBe("•••");
  const masked = await (await page.request.get("/api/queue?status=approved&role=analyst")).json();
  expect(masked.items[0].record_a.phone).toBe("•••");
  expect(masked.items[0].record_a.normalized.phone).toBe("•••");
});
