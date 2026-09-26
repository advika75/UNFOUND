import { createClient } from "@supabase/supabase-js";

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL;
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY;

if (!supabaseUrl || !supabaseAnonKey) {
  console.warn(
    "Supabase environment variables are missing. Add VITE_SUPABASE_URL and VITE_SUPABASE_ANON_KEY to run the app."
  );
}

export const supabase = createClient(
  supabaseUrl || "https://qilerhwlccptkpdghfen.supabase.co",
  supabaseAnonKey || "sb_publishable_uAe6CIhO7u19Tk7zsdHVMA_WNC4hSPz"
);
