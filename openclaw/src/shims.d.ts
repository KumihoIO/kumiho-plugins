declare module "node:os" {
  export function homedir(): string;
  export function platform(): string;
}

declare module "node:path" {
  export function join(...paths: string[]): string;
}

declare module "node:fs" {
  export function existsSync(path: string): boolean;
  export function readFileSync(
    path: string,
    encoding?: string,
  ): string;
  export function writeFileSync(
    path: string,
    data: string,
    encoding?: string,
  ): void;
}

declare module "node:child_process" {
  export interface ChildProcessWritable {
    writable: boolean;
    write(data: string, callback?: (err?: Error | null) => void): boolean;
  }

  export interface ChildProcessReadable {
    on(event: "data", listener: (chunk: Buffer) => void): void;
  }

  export interface ChildProcess {
    stdin?: ChildProcessWritable | null;
    stdout?: ChildProcessReadable | null;
    stderr?: ChildProcessReadable | null;
    on(event: "exit", listener: (code: number | null, signal: string | null) => void): this;
    on(event: "error", listener: (err: Error) => void): this;
    kill(signal?: string): boolean;
  }

  export interface ExecFileOptions {
    timeout?: number;
    env?: Record<string, string | undefined>;
  }

  export type ExecFileCallback = (
    err: Error | null,
    stdout: string,
    stderr: string,
  ) => void;

  export function execFile(
    file: string,
    args: readonly string[],
    options: ExecFileOptions,
    callback: ExecFileCallback,
  ): void;

  export interface SpawnOptions {
    stdio?: string[];
    env?: Record<string, string | undefined>;
    cwd?: string;
    detached?: boolean;
  }

  export function spawn(
    file: string,
    args?: readonly string[],
    options?: SpawnOptions,
  ): ChildProcess;

  export interface SpawnSyncOptions {
    encoding?: string;
    timeout?: number;
  }

  export function spawnSync(
    file: string,
    args?: readonly string[],
    options?: SpawnSyncOptions,
  ): {
    status: number | null;
    stdout: string;
  };
}

declare module "node:events" {
  export class EventEmitter {
    on(event: string, listener: (...args: unknown[]) => void): this;
    emit(event: string, ...args: unknown[]): boolean;
  }
}

declare module "node:readline" {
  interface ReadableInput {
    on(event: "data", listener: (chunk: Buffer) => void): void;
  }

  export interface Interface {
    on(event: "line", listener: (line: string) => void): this;
    close(): void;
  }

  export function createInterface(options: { input: ReadableInput }): Interface;
}

declare module "node:url" {
  export class URL {
    constructor(input: string, base?: string);
    hostname: string;
    port: string;
    protocol: string;
  }
}

declare module "node:crypto" {
  export interface Hash {
    update(data: string | Buffer): Hash;
    digest(encoding: "hex"): string;
  }

  export function createHash(algorithm: string): Hash;
}

declare module "node:fs/promises" {
  export function mkdir(
    path: string,
    options?: { recursive?: boolean },
  ): Promise<void>;
  export function writeFile(
    path: string,
    data: string | Buffer,
    encoding?: string,
  ): Promise<void>;
  export function readFile(path: string): Promise<Buffer>;
  export function readFile(path: string, encoding: string): Promise<string>;
  export function stat(path: string): Promise<{ size: number }>;
}

declare module "openclaw/plugin-sdk" {
  export interface OpenClawLogger {
    info(message: string): void;
    warn(message: string): void;
    error(message: string): void;
  }

  export interface OpenClawGatewayRequest {
    respond(ok: boolean, payload?: unknown): void;
    params?: Record<string, unknown>;
  }

  export interface OpenClawToolResult {
    content: Array<{ type: "text"; text: string }>;
    details?: unknown;
  }

  export interface OpenClawToolDefinition {
    name: string;
    label?: string;
    description?: string;
    parameters?: unknown;
    execute(toolCallId: string, params: Record<string, unknown>): Promise<OpenClawToolResult>;
  }

  export interface OpenClawCommandContext {
    args?: string;
  }

  export interface OpenClawProgramCommand {
    description(text: string): OpenClawProgramCommand;
    argument(spec: string, description?: string): OpenClawProgramCommand;
    option(flags: string, description?: string): OpenClawProgramCommand;
    action(handler: (...args: unknown[]) => unknown): OpenClawProgramCommand;
  }

  export interface OpenClawCliProgram {
    command(name: string): OpenClawProgramCommand;
  }

  export interface OpenClawHookEvent {
    messages?: Array<{ role: string; content: unknown }>;
  }

  export interface OpenClawPluginApi {
    pluginConfig?: unknown;
    logger: OpenClawLogger;
    registerGatewayMethod(
      name: string,
      handler: (request: OpenClawGatewayRequest) => unknown,
    ): void;
    registerTool(tool: OpenClawToolDefinition): void;
    registerCli(
      register: (ctx: { program: OpenClawCliProgram }) => void,
      options?: { commands?: string[] },
    ): void;
    registerCommand(command: {
      name: string;
      description: string;
      requireAuth?: boolean;
      acceptsArgs?: boolean;
      handler: (ctx: OpenClawCommandContext) => unknown;
    }): void;
    registerService(service: {
      id: string;
      start(ctx: unknown): unknown;
      stop(ctx: unknown): unknown;
    }): void;
    on(
      event: string,
      handler: (event: OpenClawHookEvent, ctx: unknown) => unknown,
    ): void;
  }
}

declare const process: {
  env: Record<string, string | undefined>;
};

declare class Buffer {
  toString(encoding?: string): string;
}

declare interface AbortSignal {}

declare class AbortController {
  readonly signal: AbortSignal;
  abort(): void;
}

declare class TextEncoder {
  encode(input?: string): Uint8Array;
}

declare const crypto: {
  subtle: {
    digest(
      algorithm: string,
      data: Uint8Array | ArrayBuffer,
    ): Promise<ArrayBuffer>;
  };
};

interface RequestInit {
  method?: string;
  headers?: Record<string, string>;
  body?: string;
  signal?: AbortSignal;
}

declare const console: {
  log(...args: unknown[]): void;
  info(...args: unknown[]): void;
  warn(...args: unknown[]): void;
  error(...args: unknown[]): void;
};

declare function setTimeout(
  handler: (...args: never[]) => void,
  timeout?: number,
): unknown;

declare function clearTimeout(timeoutId: unknown): void;

declare function fetch(
  input: string,
  init?: RequestInit,
): Promise<{
  ok: boolean;
  status: number;
  statusText: string;
  json(): Promise<unknown>;
  text(): Promise<string>;
}>;
