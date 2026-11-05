import type { MetadataRoute } from "next";

export default function manifest(): MetadataRoute.Manifest {
  return {
    name: "آشپز تنبل مهاجر",
    short_name: "آشپز تنبل",
    description: "برنامه‌ی غذایی هفتگی که با زندگی واقعی خونه تغییر می‌کنه",
    start_url: "/today",
    scope: "/",
    display: "standalone",
    dir: "rtl",
    lang: "fa",
    orientation: "portrait",
    background_color: "#FBF6EE",
    theme_color: "#9B2D3A",
    categories: ["food", "lifestyle"],
    icons: [
      { src: "/icons/icon-192.png", sizes: "192x192", type: "image/png" },
      { src: "/icons/icon-512.png", sizes: "512x512", type: "image/png" },
      { src: "/icons/icon-maskable-512.png", sizes: "512x512", type: "image/png", purpose: "maskable" },
      { src: "/icons/icon.svg", sizes: "any", type: "image/svg+xml" },
    ],
  };
}
