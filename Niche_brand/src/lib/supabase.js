import { createClient } from "@supabase/supabase-js";
import { requireEnv } from "./env";

export const supabase = createClient(
  requireEnv("VITE_SUPABASE_URL"),
  requireEnv("VITE_SUPABASE_ANON_KEY")
);
