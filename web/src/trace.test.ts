import { readFileSync } from "node:fs";
import { describe, expect, it } from "vitest";
import {
  parseCase,
  parseIndex,
  stepsOf,
  topologyAt,
  visibleEvidence,
  type DemoCase,
} from "./trace";

function fixture(name: string): unknown {
  return JSON.parse(
    readFileSync(new URL(`../../examples/${name}`, import.meta.url), "utf8"),
  );
}

function demo(id: string): DemoCase {
  return parseCase(fixture(`cases/${id}.json`), id);
}

describe("saved trace conversion and replay", () => {
  it("loads and switches every reviewed example without a key or backend", () => {
    const index = parseIndex(fixture("index.json"));
    expect(index.cases.map((item) => item.id)).toEqual([
      "normal",
      "area-mismatch",
      "admin-down",
      "collection-failure",
      "missing-area",
    ]);
    for (const item of index.cases) {
      const value = demo(item.id);
      expect(value.trace.mode).toBe(item.run_mode);
      expect(stepsOf(value).length).toBeGreaterThan(1);
      expect(value.source_trace_sha256).toMatch(/^[a-f0-9]{64}$/);
    }
  });

  it("keeps evidence IDs run-local and joins actual model tool calls to results", () => {
    for (const id of [
      "normal",
      "area-mismatch",
      "admin-down",
      "collection-failure",
      "missing-area",
    ]) {
      const value = demo(id);
      const steps = stepsOf(value);
      const visible = visibleEvidence(value, steps.length);
      expect(Object.keys(visible).sort()).toEqual(
        Object.keys(value.trace.evidence).sort(),
      );
      for (const step of steps)
        if (step.kind === "tool" && step.evidenceId) {
          expect(visible[step.evidenceId]).toBe(step.item.result);
        }
    }
    const wrong = structuredClone(fixture("cases/normal.json")) as Record<
      string,
      any
    >;
    wrong.trace.evidence.E001 = demo("area-mismatch").trace.evidence.E001;
    expect(() => parseCase(wrong, "normal")).toThrow(/证据/);
  });

  it("compares tool arguments by meaning rather than JSON key order", () => {
    const changed = structuredClone(fixture("cases/normal.json")) as Record<
      string,
      any
    >;
    const round = changed.trace.rounds.find((item: any) =>
      item.tool_results.some((result: any) => result.name === "ping_address"),
    );
    const call = round.model_message.tool_calls.find(
      (item: any) => item.function.name === "ping_address",
    );
    const args = JSON.parse(call.function.arguments);
    call.function.arguments = JSON.stringify({
      target: args.target,
      node: args.node,
    });
    expect(parseCase(changed, "normal").trace.final?.status).toBe("normal");
  });

  it("does not expose later topology or diagnosis observations early", () => {
    const value = demo("area-mismatch");
    const steps = stepsOf(value);
    expect(
      topologyAt(value, 0).every(
        (node) => node.linkAddress === null && node.ospfArea === null,
      ),
    ).toBe(true);
    expect(Object.keys(visibleEvidence(value, 0))).toHaveLength(0);
    const firstArea =
      steps.findIndex(
        (step) =>
          step.kind === "tool" && step.item.name === "query_ospf_interface",
      ) + 1;
    expect(firstArea).toBeGreaterThan(0);
    const atFirstArea = topologyAt(value, firstArea);
    expect(atFirstArea.filter((node) => node.ospfArea !== null)).toHaveLength(
      1,
    );
    expect(steps.at(-1)?.kind).toBe("final");
    expect(Object.keys(visibleEvidence(value, steps.length - 1)).length).toBe(
      Object.keys(value.trace.evidence).length,
    );
  });

  it("does not render a failed OSPF command as a confirmed router fault", () => {
    const value = demo("collection-failure");
    const steps = stepsOf(value);
    const failedAt =
      steps.findIndex(
        (step) =>
          step.kind === "tool" &&
          step.item.result.status === "collection_failed",
      ) + 1;
    expect(failedAt).toBeGreaterThan(0);
    const failed = steps[failedAt - 1];
    if (failed.kind !== "tool") throw new Error("Expected tool step");
    const target = String(failed.item.arguments.node);
    const before = topologyAt(value, failedAt - 1).find(
      (node) => node.node === target,
    );
    const after = topologyAt(value, failedAt).find(
      (node) => node.node === target,
    );
    expect(after?.ospfArea).toBe(before?.ospfArea);
    expect(after?.admin).toBe(before?.admin);
    expect(value.trace.model_raw_final?.status).toBe("ospf_area_mismatch");
    expect(value.trace.final?.status).toBe("collection_failure");
    expect(
      value.trace.evidence_additions?.some(
        (item) => item.evidence_id === failed.evidenceId,
      ),
    ).toBe(true);
  });

  it("rejects missing fields, unknown schema and cross-case references", () => {
    const unknown = structuredClone(fixture("cases/normal.json")) as Record<
      string,
      any
    >;
    unknown.trace.schema_version = 999;
    expect(() => parseCase(unknown, "normal")).toThrow(
      /未知 trace schema_version/,
    );
    const noEvidence = structuredClone(fixture("cases/normal.json")) as Record<
      string,
      any
    >;
    delete noEvidence.trace.evidence;
    expect(() => parseCase(noEvidence, "normal")).toThrow(/必需字段/);
    const badRef = structuredClone(fixture("cases/normal.json")) as Record<
      string,
      any
    >;
    badRef.trace.final.evidence_ids.push("E999");
    expect(() => parseCase(badRef, "normal")).toThrow(/不存在的证据/);
    expect(() => parseIndex({ schema_version: 99, cases: [] })).toThrow(
      /不兼容/,
    );
  });
});
