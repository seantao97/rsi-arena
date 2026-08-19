import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import Link from "next/link";
import { Toaster } from "@/components/ui/sonner";
import { NavTabs } from "@/components/nav-tabs";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "RSI Arena",
  description: "Run and compare agent harnesses live.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-background text-foreground">
        <header className="sticky top-0 z-20 border-b bg-background/80 backdrop-blur">
          <div className="mx-auto flex h-14 max-w-[1600px] items-center gap-6 px-6">
            <Link href="/playground" className="text-sm font-semibold tracking-tight">
              RSI Arena
            </Link>
            <NavTabs />
            <span className="ml-auto hidden text-xs text-muted-foreground sm:block">
              agent = LLM + primitives + orchestration
            </span>
          </div>
        </header>
        <main className="mx-auto w-full max-w-[1600px] flex-1 px-6 py-6">{children}</main>
        <Toaster position="bottom-right" />
      </body>
    </html>
  );
}
