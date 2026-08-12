FROM node:22-alpine AS build

WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci || npm install
COPY . .
RUN npm run build

# O resultado é estático; quem serve é o Caddy, que já faz TLS e proxy da API.
FROM caddy:2-alpine
COPY --from=build /app/dist /srv/www
