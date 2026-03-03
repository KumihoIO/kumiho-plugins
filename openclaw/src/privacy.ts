/**
 * Client-side PII redaction.
 *
 * Detects and replaces common PII patterns before any data is sent to
 * Kumiho Cloud. Mirrors the regex patterns from the kumiho-memory Python
 * package (privacy.py).
 */

import type { RedactedEntity, RedactionResult } from "./types.js";

// ---------------------------------------------------------------------------
// PII patterns
// ---------------------------------------------------------------------------

interface PIIPattern {
  type: RedactedEntity["type"];
  regex: RegExp;
}

const PII_PATTERNS: PIIPattern[] = [
  {
    type: "email",
    regex: /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g,
  },
  {
    type: "phone",
    regex: /(?:\+?1[-.\s]?)?(?:\(?\d{3}\)?[-.\s]?)?\d{3}[-.\s]?\d{4}/g,
  },
  {
    type: "ssn",
    regex: /\b\d{3}-\d{2}-\d{4}\b/g,
  },
  {
    type: "credit_card",
    regex: /\b(?:\d{4}[-\s]?){3}\d{4}\b/g,
  },
  {
    type: "ip_address",
    regex: /\b(?:\d{1,3}\.){3}\d{1,3}\b/g,
  },
];

// ---------------------------------------------------------------------------
// Redactor
// ---------------------------------------------------------------------------

export class PIIRedactor {
  private counters = new Map<string, number>();

  /** Reset placeholder counters (useful between sessions). */
  reset(): void {
    this.counters.clear();
  }

  private nextPlaceholder(type: string): string {
    const count = (this.counters.get(type) ?? 0) + 1;
    this.counters.set(type, count);
    return `${type.toUpperCase()}_${String(count).padStart(3, "0")}`;
  }

  /**
   * Detect and redact PII from text.
   * Returns the sanitized text and a list of redacted entities.
   */
  redact(text: string): RedactionResult {
    const entities: RedactedEntity[] = [];
    let result = text;

    for (const pattern of PII_PATTERNS) {
      result = result.replace(pattern.regex, (_match) => {
        const placeholder = this.nextPlaceholder(pattern.type);
        entities.push({
          type: pattern.type,
          placeholder,
          original: "[REDACTED]",
        });
        return `[${placeholder}]`;
      });
    }

    return { text: result, entities };
  }

  /**
   * Replace remaining PII placeholders with generic descriptors
   * for human-readable summaries.
   */
  anonymizeSummary(summary: string): string {
    return summary
      .replace(/\[EMAIL_\d+\]/g, "[email]")
      .replace(/\[PHONE_\d+\]/g, "[phone]")
      .replace(/\[SSN_\d+\]/g, "[ssn]")
      .replace(/\[CREDIT_CARD_\d+\]/g, "[card]")
      .replace(/\[IP_ADDRESS_\d+\]/g, "[ip]");
  }
}
