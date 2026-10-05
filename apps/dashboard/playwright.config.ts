import { defineConfig } from "@playwright/test";

const port = process.env.DASHBOARD_TEST_PORT || "3175";

export default defineConfig({
  testDir: "./tests",
  timeout: 30000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL: `http://127.0.0.1:${port}`,
    launchOptions: {
      executablePath:
        process.env.CHROME_EXECUTABLE ||
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    },
  },
  webServer: {
    command: "npm run start",
    url: `http://127.0.0.1:${port}`,
    reuseExistingServer: false,
    env: {
      PORT: port,
      HOSTNAME: "127.0.0.1",
      GATEWAY_URL: process.env.GATEWAY_URL || "http://127.0.0.1:9",
      NEXT_TELEMETRY_DISABLED: "1",
    },
  },
});
