import { createClient } from '@supabase/supabase-js';

const supabaseUrl = import.meta.env.VITE_SUPABASE_URL || 'https://wpyldsmcklwtgblxkxjt.supabase.co';
const supabaseAnonKey = import.meta.env.VITE_SUPABASE_ANON_KEY || 'eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6IndweWxkc21ja2x3dGdibHhreGp0Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NzUzNzUyNDQsImV4cCI6MjA5MDk1MTI0NH0.XGKm5yI0NxS9Y3AQoS9W5upsy3D-F3nMKaktLhZvff8';

export const supabase = createClient(supabaseUrl, supabaseAnonKey);
