FROM node:20.19.1-alpine AS build

WORKDIR /src
COPY package.json tsconfig.json ./
COPY nodes ./nodes
COPY credentials ./credentials

RUN npm install
RUN npm run build

FROM n8nio/n8n:2.1.4

USER root
RUN mkdir -p /home/node/.n8n/custom
COPY --from=build --chown=node:node /src/dist /home/node/.n8n/custom
USER node
