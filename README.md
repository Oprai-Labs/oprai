# Oprai Web

## Development
```bash
cd "web page"
npm install
npm run dev
```

## Build & Deploy
```bash
cd "web page"
npm run build
wrangler pages deploy ./out --project-name=cool-breeze-92dd --branch=production
```