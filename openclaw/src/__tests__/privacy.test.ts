import { describe, it, expect, beforeEach } from "vitest";
import { PIIRedactor } from "../privacy.js";

// ---------------------------------------------------------------------------
// PIIRedactor.redact()
// ---------------------------------------------------------------------------

describe("PIIRedactor.redact()", () => {
  let redactor: PIIRedactor;

  beforeEach(() => {
    redactor = new PIIRedactor();
  });

  it("replaces email addresses", () => {
    const { text, entities } = redactor.redact("Contact me at user@example.com for details.");
    expect(text).not.toContain("user@example.com");
    expect(text).toContain("[EMAIL_001]");
    expect(entities).toHaveLength(1);
    expect(entities[0].type).toBe("email");
    expect(entities[0].placeholder).toBe("EMAIL_001");
  });

  it("replaces standard phone numbers", () => {
    const { text, entities } = redactor.redact("Call 555-867-5309 any time.");
    expect(text).not.toContain("555-867-5309");
    expect(text).toContain("[PHONE_001]");
    expect(entities[0].type).toBe("phone");
  });

  it("replaces phone numbers with country code", () => {
    const { text } = redactor.redact("International: +1 (555) 867-5309");
    expect(text).not.toContain("555-867-5309");
    expect(text).toContain("[PHONE_001]");
  });

  it("replaces SSNs", () => {
    const { text, entities } = redactor.redact("SSN: 123-45-6789");
    expect(text).not.toContain("123-45-6789");
    expect(text).toContain("[SSN_001]");
    expect(entities[0].type).toBe("ssn");
  });

  it("replaces credit card numbers with spaces", () => {
    const { text, entities } = redactor.redact("Card: 4111 1111 1111 1111");
    expect(text).not.toContain("4111 1111 1111 1111");
    expect(text).toContain("[CREDIT_CARD_001]");
    expect(entities[0].type).toBe("credit_card");
  });

  it("replaces credit card numbers with dashes", () => {
    const { text } = redactor.redact("Card: 4111-1111-1111-1111");
    expect(text).not.toContain("4111-1111-1111-1111");
    expect(text).toContain("[CREDIT_CARD_001]");
  });

  it("replaces IPv4 addresses", () => {
    const { text, entities } = redactor.redact("Server at 192.168.1.100 is down.");
    expect(text).not.toContain("192.168.1.100");
    expect(text).toContain("[IP_ADDRESS_001]");
    expect(entities[0].type).toBe("ip_address");
  });

  it("assigns separate incrementing placeholders for multiple instances of same type", () => {
    const { text, entities } = redactor.redact(
      "Emails: first@test.com and second@test.com",
    );
    expect(text).toContain("[EMAIL_001]");
    expect(text).toContain("[EMAIL_002]");
    expect(entities).toHaveLength(2);
    expect(entities[0].placeholder).toBe("EMAIL_001");
    expect(entities[1].placeholder).toBe("EMAIL_002");
  });

  it("handles multiple PII types in the same text", () => {
    const { text, entities } = redactor.redact(
      "Email: test@example.com, phone: 555-123-4567",
    );
    expect(text).not.toContain("test@example.com");
    expect(text).not.toContain("555-123-4567");
    expect(entities.length).toBeGreaterThanOrEqual(2);
  });

  it("leaves non-PII text unchanged", () => {
    const input = "The quick brown fox jumps over the lazy dog.";
    const { text, entities } = redactor.redact(input);
    expect(text).toBe(input);
    expect(entities).toHaveLength(0);
  });

  it("returns empty output for empty input", () => {
    const { text, entities } = redactor.redact("");
    expect(text).toBe("");
    expect(entities).toHaveLength(0);
  });
});

// ---------------------------------------------------------------------------
// PIIRedactor.anonymizeSummary()
// ---------------------------------------------------------------------------

describe("PIIRedactor.anonymizeSummary()", () => {
  let redactor: PIIRedactor;

  beforeEach(() => {
    redactor = new PIIRedactor();
  });

  it("replaces EMAIL placeholders with [email]", () => {
    const result = redactor.anonymizeSummary("Contact [EMAIL_001] or [EMAIL_002].");
    expect(result).not.toContain("EMAIL_001");
    expect(result).toBe("Contact [email] or [email].");
  });

  it("replaces PHONE placeholders with [phone]", () => {
    const result = redactor.anonymizeSummary("Call [PHONE_001].");
    expect(result).toBe("Call [phone].");
  });

  it("replaces SSN placeholders with [ssn]", () => {
    const result = redactor.anonymizeSummary("SSN is [SSN_001].");
    expect(result).toBe("SSN is [ssn].");
  });

  it("replaces CREDIT_CARD placeholders with [card]", () => {
    const result = redactor.anonymizeSummary("Card: [CREDIT_CARD_001]");
    expect(result).toBe("Card: [card]");
  });

  it("replaces IP_ADDRESS placeholders with [ip]", () => {
    const result = redactor.anonymizeSummary("Server [IP_ADDRESS_001] down.");
    expect(result).toBe("Server [ip] down.");
  });

  it("leaves text without placeholders unchanged", () => {
    const input = "Nothing sensitive here.";
    expect(redactor.anonymizeSummary(input)).toBe(input);
  });
});

// ---------------------------------------------------------------------------
// PIIRedactor.reset()
// ---------------------------------------------------------------------------

describe("PIIRedactor.reset()", () => {
  it("resets placeholder counters so numbering restarts from 001", () => {
    const redactor = new PIIRedactor();

    const first = redactor.redact("first@test.com");
    expect(first.text).toContain("[EMAIL_001]");

    redactor.redact("second@test.com"); // counter at 002 now
    redactor.reset();

    const afterReset = redactor.redact("third@test.com");
    expect(afterReset.text).toContain("[EMAIL_001]"); // back to 001
  });
});
