import { defineConfig } from "@playwright/test";
import path from "path";

const repoRoot = path.resolve(__dirname, "..", "..");
const tmpDataDir = path.join(__dirname, ".tmp-data");

export default defineConfig({
  testDir: __dirname,
  timeout: 60_000,
  retries: 0,
  use: {
    baseURL: "http://127.0.0.1:8765",
    trace: "retain-on-failure",
  },
  webServer: {
    // cwd must be the repo root: relative fixture paths resolve from there.
    cwd: repoRoot,
    command: "uv run cdp serve --port 8765 --fixture tests/e2e/queue_fixture.json",
    url: "http://127.0.0.1:8765/api/health",
    reuseExistingServer: false,
    env: {
      // Isolated data dir so e2e never touches dev data; metrics.json is
      // absent there, which also exercises the stats "n/a" path.
      CDP_DATA_DIR: tmpDataDir,
    },
    timeout: 60_000,
  },
});
