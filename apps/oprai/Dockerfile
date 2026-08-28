# Stage 1: Build the Angular application
FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --no-audit --no-fund

COPY . .
RUN npm run build:prod

# Stage 2: Serve with nginx
FROM nginx:alpine AS serve

# Remove default nginx config
RUN rm /etc/nginx/conf.d/default.conf

# Add custom nginx config for SPA routing
COPY --from=build /app/dist/oprai/browser /usr/share/nginx/html

# Nginx config that handles SPA routing (all paths -> index.html)
RUN printf 'server {\n\
  listen 80;\n\
  server_name _;\n\
  root /usr/share/nginx/html;\n\
  index index.html;\n\
\n\
  # Gzip compression\n\
  gzip on;\n\
  gzip_vary on;\n\
  gzip_min_length 1000;\n\
  gzip_proxied any;\n\
  gzip_types text/plain text/css application/json application/javascript text/xml application/xml text/javascript image/svg+xml;\n\
\n\
  # Static /assets/ files (logos, icons) keep STABLE names — unlike the\n\
  # content-hashed JS/CSS chunks — so they must NOT be immutable, or a logo\n\
  # swap never reaches browsers that cached the old one (the "wrong Lighter\n\
  # icon" problem). Short cache + revalidate so updates propagate on their own.\n\
  # `^~` beats the regex block below for these paths.\n\
  location ^~ /assets/ {\n\
    expires 5m;\n\
    add_header Cache-Control "public, max-age=300, must-revalidate";\n\
    try_files $uri =404;\n\
  }\n\
\n\
  # Cache content-hashed bundles aggressively (their name changes on deploy)\n\
  location ~* \\.(js|css|png|jpg|jpeg|gif|ico|svg|woff|woff2|ttf|eot)$ {\n\
    expires 1y;\n\
    add_header Cache-Control "public, immutable";\n\
    try_files $uri =404;\n\
  }\n\
\n\
  # SPA fallback: serve index.html for all non-file routes. index.html must\n\
  # never be cached — it references the content-hashed JS/CSS chunks by name,\n\
  # so a stale index keeps loading old chunks after a deploy (the recurring\n\
  # "card shows old behaviour until hard-refresh" problem). A location-level\n\
  # add_header drops the server-level ones, so the security headers are\n\
  # repeated here.\n\
  location / {\n\
    try_files $uri $uri/ /index.html;\n\
    add_header Cache-Control "no-cache, no-store, must-revalidate" always;\n\
    expires -1;\n\
    add_header X-Frame-Options "SAMEORIGIN" always;\n\
    add_header X-Content-Type-Options "nosniff" always;\n\
    add_header Referrer-Policy "strict-origin-when-cross-origin" always;\n\
  }\n\
\n\
  # Security headers\n\
  add_header X-Frame-Options "SAMEORIGIN" always;\n\
  add_header X-Content-Type-Options "nosniff" always;\n\
  add_header Referrer-Policy "strict-origin-when-cross-origin" always;\n\
}\n' > /etc/nginx/conf.d/default.conf

EXPOSE 80

CMD ["nginx", "-g", "daemon off;"]
