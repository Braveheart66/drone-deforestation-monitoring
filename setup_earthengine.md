# Earth Engine setup (quick)
1. Create/enable Google account and sign up for Earth Engine: https://earthengine.google.com/
2. Install `earthengine-api` (requirements.txt covers this).
3. Authenticate locally:
   - Run: `earthengine authenticate --quiet`
   - Follow the URL, log in, paste the token.
4. For headless/production (server), consider a service account and upload private key and set EE credentials in environment variables. See Earth Engine docs for service accounts.
5. If using geemap locally, run `geemap` examples to confirm authentication.
