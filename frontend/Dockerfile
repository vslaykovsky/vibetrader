FROM node:20-alpine AS build

WORKDIR /app

COPY package.json package-lock.json* ./
RUN npm ci

COPY . .
RUN npm run build
RUN find dist -type f \( \
      -name '*.js' -o \
      -name '*.mjs' -o \
      -name '*.css' -o \
      -name '*.svg' -o \
      -name '*.json' \
    \) -size +1k -exec sh -c 'gzip -9 -c "$1" > "$1.gz"' _ {} \;


FROM nginx:1.27-alpine

COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY --from=build /app/dist /usr/share/nginx/html

EXPOSE 8080
CMD ["nginx", "-g", "daemon off;"]
