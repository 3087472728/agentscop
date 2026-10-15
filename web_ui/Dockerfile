FROM node:22-slim

RUN npm install -g pnpm@12

WORKDIR /app
EXPOSE 5173

# Dependencies install at container start into named volumes (compose
# mounts them over /app), so pnpm works on a Linux filesystem with
# native symlinks; the store volume keeps restarts fast.
CMD ["sh", "-c", "pnpm install --registry=https://registry.npmmirror.com --allow-build=sharp --allow-build=unrs-resolver && pnpm --filter frontend exec vite --host 0.0.0.0"]
