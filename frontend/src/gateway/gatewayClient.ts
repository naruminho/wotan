/** gateway_client SDK (TypeScript) for frontends of generated experiments.
 *
 * Mirrors wotan.gateway_client: the same YAML configuration and TokenManager
 * live on the local backend - frontends call their local backend's /gw/*
 * proxy (or embed this in an app that talks to a tiny local Python server).
 * This file exists so experiment templates can ship a JS client without
 * reimplementing auth.
 */

export interface GatewayChatOptions {
  system?: string;
  model?: string;
  temperature?: number;
  max_tokens?: number;
  history?: { role: string; content: string }[];
}

export class GatewayClient {
  constructor(private baseUrl: string = "") {}

  private async call<T>(path: string, body: any): Promise<T> {
    const res = await fetch(`${this.baseUrl}${path}`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const text = await res.text();
      throw new Error(`gateway ${path} failed: ${res.status} ${text.slice(0, 300)}`);
    }
    return res.json() as Promise<T>;
  }

  chat(prompt: string, opts: GatewayChatOptions = {}): Promise<{ text: string }> {
    return this.call("/gw/chat", { prompt, ...opts });
  }

  chatWithImage(prompt: string, imagePath: string, opts: GatewayChatOptions = {}): Promise<{ text: string }> {
    return this.call("/gw/chat_image", { prompt, image_path: imagePath, ...opts });
  }

  chatWithDocument(prompt: string, documentPath: string, opts: GatewayChatOptions = {}): Promise<{ text: string }> {
    return this.call("/gw/chat_document", { prompt, document_path: documentPath, ...opts });
  }

  extractJson(text: string, schema: any, model?: string): Promise<any> {
    return this.call("/gw/extract_json", { text, schema, model });
  }

  runWorkflow(name: string, inputs: any): Promise<any> {
    return this.call("/gw/workflow", { name, inputs });
  }

  listModels(): Promise<{ models: any[] }> {
    return fetch(`${this.baseUrl}/gw/models`).then((r) => r.json());
  }
}
