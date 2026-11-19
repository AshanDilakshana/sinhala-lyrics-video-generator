import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Auto-Lyrics Studio | TikTok & CapCut Synced Video Creator",
  description:
    "Generate TikTok & CapCut style videos with automatically synced lyrics via LRCLIB, Spotify, and AI Forced Alignment for Sinhala & custom songs.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        <link rel="preconnect" href="https://fonts.googleapis.com" />
        <link rel="preconnect" href="https://fonts.gstatic.com" crossOrigin="anonymous" />
        <link
          href="https://fonts.googleapis.com/css2?family=Abhaya+Libre:wght@400;600;700;800&family=Gemunu+Libre:wght@400;600;700;800&family=Noto+Sans+Sinhala:wght@400;600;700;800&family=Noto+Serif+Sinhala:wght@400;600;700;800&family=Yaldevi:wght@400;600;700&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="min-h-screen bg-zinc-950 text-zinc-100 antialiased selection:bg-yellow-400 selection:text-black">
        {children}
      </body>
    </html>
  );
}
