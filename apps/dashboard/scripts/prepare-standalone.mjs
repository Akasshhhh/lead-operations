import { cp } from "node:fs/promises";

// Complete Next's standalone artifact for identical host/container startup.
await cp(".next/static", ".next/standalone/apps/dashboard/.next/static", {
  recursive: true,
});
