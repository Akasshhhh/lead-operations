import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Vox · Lead operations",
  description: "Live voice qualification and durable lead operations.",
};
export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
