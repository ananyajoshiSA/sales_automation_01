# Work Style Profile quiz: hosting

Static site (`index.html`, `config.js`) plus a Supabase database. No Claude account needed by anyone.
Takers enter name, email, position and team (no sign-up), take the test once, see their result.
The admin signs in at `<your link>#admin` (or the small "Admin sign in" link at the bottom of the first page).

## One-time setup (about 10 minutes)
1. supabase.com > New project (free).
2. SQL Editor > paste `schema.sql` (edit the admin email in it first) > Run.
3. Authentication > Users > Add user > create user with your admin email and a password (tick "Auto confirm").
4. Authentication > Sign In / Providers > turn **off** "Allow new users to sign up" (so only you can be admin).
5. Project Settings > API: copy the Project URL and the `anon` public key into `config.js`.
6. Host the folder: Netlify Drop (app.netlify.com/drop), Cloudflare Pages or Vercel. Upload `index.html` and `config.js` only.
7. Open the link, take a test submission, then sign in as admin to see it. Use Reset to delete your test row.

`original.html` is the uploaded Claude-artifact version, kept for reference only.
