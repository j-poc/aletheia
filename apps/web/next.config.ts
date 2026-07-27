import type { NextConfig } from "next";

const config: NextConfig = {
  // Every page reads live from the warehouse; caching a point-in-time answer
  // would be the one form of staleness this project exists to eliminate.
  reactStrictMode: true,
};

export default config;
