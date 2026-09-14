import type { Metadata, Viewport } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "PHONEOPS AI — Adaptive Recovery Control",
  description:
    "Autonomous exception recovery for phone-dependent operations. When the plan breaks, PHONEOPS calls, learns and recovers.",
  icons: { icon: "/favicon.png", apple: "/apple-icon.png" },
};

export const viewport: Viewport = {
  themeColor: "#030b1e",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen antialiased">{children}</body>
    </html>
  );
}
