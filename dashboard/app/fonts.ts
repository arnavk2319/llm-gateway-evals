import { Familjen_Grotesk, Hanken_Grotesk, IBM_Plex_Mono } from "next/font/google";

// Display, UI and data faces. The CSS tokens in globals.css (--font-display, --font-ui, --font-mono) read these variables.
export const familjen = Familjen_Grotesk({ subsets: ["latin"], weight: ["600", "700"], variable: "--font-familjen", display: "swap" });
export const hanken = Hanken_Grotesk({ subsets: ["latin"], weight: ["400", "500", "600"], variable: "--font-hanken", display: "swap" });
export const plex = IBM_Plex_Mono({ subsets: ["latin"], weight: ["400", "500"], variable: "--font-plex", display: "swap" });
