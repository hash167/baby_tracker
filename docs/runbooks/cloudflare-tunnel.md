# Cloudflare Tunnel

The public name is `https://baby.thewalawalas.com`. Add a public hostname on the existing tunnel:

- Hostname: `baby.thewalawalas.com`
- Service: `http://baby-tracker.baby-tracker.svc.cluster.local:8000`

Do not publish PostgreSQL, the collector, or any other port. The tunnel credentials stay in the cluster's existing tunnel secret. They are not part of this repository.

Django settings for that hostname:

- `DJANGO_ALLOWED_HOSTS=baby.thewalawalas.com`
- `DJANGO_CSRF_TRUSTED_ORIGINS=https://baby.thewalawalas.com`
- `DJANGO_BEHIND_PROXY=true`

A request whose Host header is not in `DJANGO_ALLOWED_HOSTS` is rejected. Cookies are Secure, HttpOnly, and SameSite=Lax when the proxy flag is on. HTTP to the public name redirects to HTTPS because the proxy sets `X-Forwarded-Proto`.

This change is made in the tunnel configuration the owner already uses for other hostnames. It is not applied from GitHub Actions.
