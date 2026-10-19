import type { Metadata } from "next";
import AppShell from "@/components/AppShell";
import "./globals.css";

export const metadata: Metadata = {
  title: "LeonTV",
  icons: { icon: "/icon.svg", shortcut: "/icon.svg" },
  description: "从灵感到成片的 AI 短片创作工作台",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body
        className="antialiased"
      >
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
