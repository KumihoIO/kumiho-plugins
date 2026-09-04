import { URL } from "node:url";

const TLS_SCHEMES = new Set(["https:", "grpcs:"]);
const PLAINTEXT_SCHEMES = new Set(["http:", "grpc:"]);

function unbracketHost(hostname: string): string {
  return hostname.startsWith("[") && hostname.endsWith("]")
    ? hostname.slice(1, -1)
    : hostname;
}

function formatHost(hostname: string): string {
  const host = unbracketHost(hostname);
  return host.includes(":") ? `[${host}]` : host;
}

function isLoopbackHost(hostname: string): boolean {
  const host = unbracketHost(hostname).toLowerCase();
  if (host === "localhost" || host === "::1") return true;
  const octets = host.split(".");
  return (
    octets.length === 4 &&
    octets.every((part) => /^\d{1,3}$/.test(part) && Number(part) <= 255) &&
    Number(octets[0]) === 127
  );
}

function parseEndpoint(raw: string): { url: URL; hadScheme: boolean } {
  const value = raw.trim();
  const hadScheme = value.includes("://");
  let url: URL;
  try {
    url = new URL(hadScheme ? value : `grpc://${value}`);
    // Accessing URL.port validates the port range in every supported Node.
    void url.port;
  } catch {
    throw new Error(`Invalid Kumiho CE endpoint: ${raw}`);
  }
  if (!url.hostname || url.username || url.password || url.search || url.hash) {
    throw new Error(`Invalid Kumiho CE endpoint: ${raw}`);
  }
  if (url.pathname && url.pathname !== "/") {
    throw new Error("Kumiho CE endpoints cannot contain a path");
  }
  if (!TLS_SCHEMES.has(url.protocol) && !PLAINTEXT_SCHEMES.has(url.protocol)) {
    throw new Error("Kumiho CE endpoint scheme must be grpc, http, grpcs, or https");
  }
  return { url, hadScheme };
}

/**
 * Normalize a CE gRPC target without discarding transport security.
 *
 * Loopback targets may remain bare/plaintext. Anything outside loopback must
 * explicitly use grpcs:// or https://; a :443 suffix alone is not proof that
 * the client will create TLS credentials.
 */
export function normalizeCeEndpoint(raw: string | undefined): string {
  const value = (raw ?? "").trim();
  if (!value) return "";

  const { url, hadScheme } = parseEndpoint(value);
  const loopback = isLoopbackHost(url.hostname);
  const tls = TLS_SCHEMES.has(url.protocol);
  if (!loopback && (!hadScheme || !tls)) {
    throw new Error(
      "Remote Kumiho CE endpoints require an explicit grpcs:// or https:// TLS URL",
    );
  }

  const defaultPort = tls ? "443" : hadScheme ? "80" : "";
  const port = url.port || defaultPort;
  const authority = `${formatHost(url.hostname)}${port ? `:${port}` : ""}`;
  if (tls) return `${url.protocol}//${authority}`;
  return authority;
}

/** True only for a validated, non-loopback CE target. */
export function isRemoteCeEndpoint(endpoint: string): boolean {
  const normalized = normalizeCeEndpoint(endpoint);
  const { url } = parseEndpoint(normalized);
  return !isLoopbackHost(url.hostname);
}
