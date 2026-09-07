import { afterEach, describe, expect, it, vi } from "vitest";

import readingApi from "./reading";

describe("readingApi.ensure", () => {
  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("creates a reading item when a direct reading link has no record", async () => {
    const get = vi.spyOn(readingApi, "get").mockRejectedValue({ response: { status: 404 } });
    const add = vi.spyOn(readingApi, "add").mockResolvedValue({
      paper_id: "paper-1",
    } as never);

    await expect(readingApi.ensure("paper-1")).resolves.toMatchObject({ paper_id: "paper-1" });
    expect(get).toHaveBeenCalledWith("paper-1");
    expect(add).toHaveBeenCalledWith("paper-1");
  });

  it("preserves non-404 errors", async () => {
    const error = { response: { status: 503 } };
    vi.spyOn(readingApi, "get").mockRejectedValue(error);
    const add = vi.spyOn(readingApi, "add");

    await expect(readingApi.ensure("paper-1")).rejects.toBe(error);
    expect(add).not.toHaveBeenCalled();
  });

  it("retries transient availability failures before opening a paper", async () => {
    vi.useFakeTimers();
    const paper = { paper_id: "paper-1" };
    const ensure = vi
      .spyOn(readingApi, "ensure")
      .mockRejectedValueOnce({ response: { status: 503 } })
      .mockResolvedValueOnce(paper as never);

    const resultPromise = readingApi.ensureReady("paper-1");
    await vi.advanceTimersByTimeAsync(300);

    await expect(resultPromise).resolves.toBe(paper);
    expect(ensure).toHaveBeenCalledTimes(2);
  });
});
