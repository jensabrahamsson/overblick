# Vercel Deployment — Next Steps

## Current state

- **Live URL**: https://website-ten-henna-67.vercel.app
- **Alias URL**: https://website-fr9k7i9yz-jensabrahamssons-projects.vercel.app
- **Vercel project**: `jensabrahamssons-projects/website`
- **Serving**: `coming-soon.html` at `/` (via rewrite in `vercel.json`)
- **Full site**: `index.html` preserved at `/index.html`

---

## Step 1 — Create the GitHub repo

```bash
gh repo create jensabrahamsson/overblick --public --source=. --remote=origin
git push -u origin main
```

This pushes all local commits (including the Vercel config) to GitHub.

---

## Step 2 — Connect Vercel to GitHub (auto-deploy)

```bash
cd website
vercel link          # re-link if needed, select existing project
```

Or via the Vercel dashboard:
1. Go to https://vercel.com/jensabrahamssons-projects/website/settings/git
2. Connect to `jensabrahamsson/overblick`
3. Set **Root Directory** to `website`

After this, every push to `main` triggers a new production deployment automatically.

---

## Step 3 — Add a custom domain

```bash
vercel domains add overblick.dev       # or whatever domain you own
```

Then add a DNS record at your registrar:
- **Type**: CNAME
- **Name**: `@` (or `www`)
- **Value**: `cname.vercel-dns.com`

Vercel handles HTTPS automatically.

---

## Step 4 — Go live with the full site

When ready to launch, update `vercel.json` to remove the rewrite:

```json
{
  "headers": [ ... ]
}
```

The root (`/`) will then serve `index.html` (the full marketing site).
Redeploy:

```bash
cd website && vercel --prod
```

---

## Useful commands

```bash
# Re-authenticate (if token expires)
vercel login

# Deploy manually
cd website && vercel --prod

# Check deployment status
vercel ls

# View live logs
vercel logs website-ten-henna-67.vercel.app

# List domains
vercel domains ls
```
