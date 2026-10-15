# Frontend image: Vite build served by nginx, with /api proxied to the backend.
#
#   docker build -t lol-coach-frontend frontend
#   docker run --rm -p 8080:80 --add-host backend:host-gateway lol-coach-frontend
#
# The nginx config proxies /api/* to http://backend:8000, so name the backend
# service "backend" (docker compose does this automatically).
FROM node:22-alpine AS build

WORKDIR /app

RUN npm install --global pnpm@12.9.1

# Manifest + lockfile first so dependency install is a cached layer.
COPY package.json pnpm-lock.yaml pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile

COPY . .
RUN pnpm build

FROM nginx:1.27-alpine AS runtime

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD wget -q -O /dev/null http://127.0.0.1/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
