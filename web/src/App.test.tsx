import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { EvidenceDetails } from "./App";

describe("raw evidence display", () => {
  it("renders untrusted command output as text, not HTML", () => {
    const payload = "<img src=x onerror=alert(1)>";
    const html = renderToStaticMarkup(
      createElement(EvidenceDetails, {
        evidence: {
          evidence_id: "E001",
          tool: "query_neighbors",
          arguments: { node: "r1" },
          status: "ok",
          observations: [
            { node: "r1", id: "ospf_neighbors", stdout: payload, stderr: "" },
          ],
        },
      }),
    );
    expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
    expect(html).not.toContain(payload);
  });
});
